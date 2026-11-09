"""Build ONE YOLO dataset for a combined PPE + fire/smoke model from sources that each label only some classes.

  python scripts/08_build_combined.py --config configs/sources_combined.yaml            # build
  python scripts/08_build_combined.py --config configs/sources_combined.yaml --dry-run  # counts only, no teachers

Per source: read YOLO txt or COCO json -> map class names -> derive missing negatives from the source's own boxes
(e.g. SH17 Head without Helmet -> no_helmet) -> fill classes the source never labels with a teacher model
(pseudo-labels, conf >= pseudo_conf) -> seeded subsample per split.
Each source keeps its OWN train/val/test split, so the combined test set never shares images with training.
Images are symlinked (no copies); labels are written. Output: <output_dir>/{images,labels}/{train,val,test}, data.yaml,
and reports/combined_summary.md with per-class counts and the provenance of every label.
"""
import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import yaml

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = ("train", "val", "test")


def _device():
    try:
        import torch
        return 0 if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    except ImportError:
        return "cpu"


DEVICE = _device()                                       # teachers run on the GPU (CPU took ~10 min for 3k images)


def norm(s):
    return str(s).strip().lower()


def overlap(a, b):
    """Intersection over the smaller box (xyxy, any units)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def xywhn_to_xyxy(x, y, w, h):
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


# ---------------------------------------------------------------- readers: -> list of (image_path, [(name, xyxy_norm)])
def read_yolo(src, root, split_dir):
    names = src.get("names")
    if not names:
        d = yaml.safe_load((root / "data.yaml").read_text())["names"]
        names = [d[k] for k in sorted(d)] if isinstance(d, dict) else d
    img_dir = root / split_dir
    items = []
    include = src.get("include")                          # optional filename globs, e.g. ["ds1_*"]
    for img in sorted(p for p in img_dir.glob("*") if p.suffix.lower() in IMG_EXT):
        if include and not any(img.match(g) for g in include):
            continue
        lbl = Path(str(img.parent).replace("/images", "/labels")) / (img.stem + ".txt")
        boxes = []
        if lbl.exists():
            for line in lbl.read_text().splitlines():
                p = line.split()
                if len(p) >= 5:
                    boxes.append((names[int(p[0])], xywhn_to_xyxy(*map(float, p[1:5]))))
        items.append((img, boxes))
    return items


def read_coco(src, root, split_dir):
    d = json.loads((root / split_dir / "_annotations.coco.json").read_text())
    fix = src.get("relabel") or {}                       # export name -> true class (for mislabelled exports)
    cats = {c["id"]: fix.get(c["name"], c["name"]) for c in d["categories"]}
    by_img = defaultdict(list)
    for a in d["annotations"]:
        by_img[a["image_id"]].append(a)
    items = []
    for im in d["images"]:
        if "_aug" in im["file_name"]:          # Roboflow augmented copies: near-duplicates, never used
            continue
        W, H = im["width"], im["height"]
        boxes = [(cats[a["category_id"]], (a["bbox"][0] / W, a["bbox"][1] / H, (a["bbox"][0] + a["bbox"][2]) / W,
                                             (a["bbox"][1] + a["bbox"][3]) / H)) for a in by_img[im["id"]]]
        items.append((root / split_dir / im["file_name"], boxes))
    return sorted(items, key=lambda t: t[0].name)


# ---------------------------------------------------------------- per-source processing
def take(items, n, rng, background_fraction=None, prefer=None):
    if n == "all" or n >= len(items):
        return list(items)
    if prefer:                                          # fill first from images containing any of these raw classes
        want = {norm(p) for p in prefer}
        hit = [t for t in items if any(norm(c) in want for c, _ in t[1])]
        rest = [t for t in items if t not in hit]
        first = rng.sample(hit, min(n, len(hit)))
        return first + rng.sample(rest, n - len(first))
    if background_fraction is None:
        return rng.sample(items, n)
    pos = [t for t in items if t[1]]
    neg = [t for t in items if not t[1]]
    nb = min(len(neg), round(n * background_fraction))
    return rng.sample(pos, min(len(pos), n - nb)) + rng.sample(neg, nb)


def convert(src, raw_boxes, classes):
    """map names, derive negatives. Returns [(class_idx, xyxy, provenance)]."""
    m = {norm(k): v for k, v in src["map"].items()}
    out = [(classes.index(m[norm(n)]), b, "label") for n, b in raw_boxes if norm(n) in m]
    for target, rule in (src.get("derive") or {}).items():
        base = [b for n, b in raw_boxes if norm(n) == norm(rule["from"])]
        cover = [b for n, b in raw_boxes if norm(n) == norm(rule["unless_overlaps"])]
        for b in base:
            if not any(overlap(b, c) >= 0.3 for c in cover):
                out.append((classes.index(target), b, "derived"))
    return out


def pseudo_label(paths, teacher, wanted, conf, classes):
    """Run a teacher; return {path: [(class_idx, xyxy_norm, 'pseudo')]} for the wanted class names only."""
    res = {}
    tn = {i: norm(n) for i, n in teacher.names.items()}
    want = {norm(w) for w in wanted}
    for i in range(0, len(paths), 32):
        chunk = paths[i:i + 32]
        for p, r in zip(chunk, teacher.predict([str(x) for x in chunk], conf=conf, verbose=False, stream=False, device=DEVICE)):
            boxes = []
            for (x1, y1, x2, y2), k in zip(r.boxes.xyxyn.tolist(), r.boxes.cls.tolist()):
                if tn[int(k)] in want:
                    boxes.append((classes.index(tn[int(k)]), (x1, y1, x2, y2), "pseudo"))
            res[p] = boxes
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true", help="read and sample only; no teachers, nothing written")
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    classes = cfg["classes"]
    rng = random.Random(cfg.get("seed", 42))
    out = Path(cfg["output_dir"])
    teachers = {}

    stats = defaultdict(Counter)      # (split) -> class -> n
    prov = defaultdict(Counter)       # source -> provenance:class -> n
    nimg = defaultdict(Counter)       # source -> split -> images
    if not a.dry_run:
        for s in SPLITS:
            for sub in ("images", "labels"):
                d = out / sub / s
                d.mkdir(parents=True, exist_ok=True)
                for f in d.iterdir():
                    f.unlink()

    for src in cfg["sources"]:
        root = Path(src["path"])
        reader = read_coco if src["format"] == "coco" else read_yolo
        shared = None
        if src["splits"]["val"] == src["splits"]["test"]:        # one folder for both: split it 50/50, disjoint
            pool = reader(src, root, src["splits"]["val"])
            random.Random(cfg.get("seed", 42)).shuffle(pool)
            shared = {"val": pool[: len(pool) // 2], "test": pool[len(pool) // 2:]}
        for split in SPLITS:
            items = shared[split] if shared and split in shared else reader(src, root, src["splits"][split])
            items = take(items, src["take"][split], rng, src.get("background_fraction"),
                         src.get("prefer") if split == "train" else None)   # val/test stay unbiased samples
            nimg[src["name"]][split] = len(items)
            labels = {p: convert(src, boxes, classes) for p, boxes in items}
            if not a.dry_run:
                for kind, wanted in (src.get("pseudo") or {}).items():
                    if kind not in teachers:
                        from ultralytics import YOLO
                        teachers[kind] = YOLO(cfg["teachers"][kind])
                    extra = pseudo_label(list(labels), teachers[kind], wanted, cfg.get("pseudo_conf", 0.5), classes)
                    for p, bx in extra.items():
                        labels[p] += bx
            for p, bx in labels.items():
                for k, _, how in bx:
                    stats[split][classes[k]] += 1
                    prov[src["name"]][f"{how}:{classes[k]}"] += 1
                if a.dry_run:
                    continue
                stem = f"{src['name']}__{p.stem}"
                link = out / "images" / split / (stem + p.suffix.lower())
                link.symlink_to(p.resolve())
                lines = []
                for k, (x1, y1, x2, y2), _ in bx:
                    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(1, x2), min(1, y2)
                    if x2 - x1 > 1e-3 and y2 - y1 > 1e-3:
                        lines.append(f"{k} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}")
                (out / "labels" / split / (stem + ".txt")).write_text("\n".join(lines) + ("\n" if lines else ""))
        print(f"{src['name']:<18} " + "  ".join(f"{s}={nimg[src['name']][s]}" for s in SPLITS))

    if not a.dry_run:
        (out / "data.yaml").write_text(yaml.safe_dump(
            {"path": str(out.resolve()), "train": "images/train", "val": "images/val", "test": "images/test",
             "names": dict(enumerate(classes))}, sort_keys=False))

    rows = ["# Combined dataset summary", "", "| class | train | val | test |", "|---|---|---|---|"]
    rows += [f"| {c} | {stats['train'][c]} | {stats['val'][c]} | {stats['test'][c]} |" for c in classes]
    rows += ["", "Images: " + ", ".join(f"{s} {sum(nimg[n][s] for n in nimg)}" for s in SPLITS), "", "## Label provenance per source", ""]
    for n, c in prov.items():
        rows.append(f"- **{n}**: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    text = "\n".join(rows) + "\n"
    print(text)
    if not a.dry_run:
        Path("reports").mkdir(exist_ok=True)
        Path("reports/combined_summary.md").write_text(text)


if __name__ == "__main__":
    main()
