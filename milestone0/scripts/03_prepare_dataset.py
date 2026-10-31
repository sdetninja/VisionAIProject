"""Merge several YOLO-format datasets into one with a unified class taxonomy.

  python scripts/03_prepare_dataset.py --config configs/sources_ppe.yaml
  python scripts/03_prepare_dataset.py --config configs/sources_hazard.yaml

Supports both layouts: <root>/{train,valid,test}/{images,labels}  and  <root>/images/<split>, <root>/labels/<split>
(any image whose path contains an `images` directory with a sibling `labels` directory).
Steps: read source class names from the source's data.yaml -> remap via config -> drop unmapped
-> de-duplicate by file hash -> split -> write data.yaml. Output is deterministic (seeded).
"""
import argparse
import hashlib
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import yaml

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_source_names(root: Path):
    for cand in [root / "data.yaml", root / "dataset.yaml", *root.glob("*.yaml")]:
        if cand.exists():
            d = yaml.safe_load(cand.read_text())
            names = d.get("names")
            if isinstance(names, dict):
                return [names[k] for k in sorted(names)]
            if names:
                return list(names)
    return None


def label_path_for(img: Path):
    parts = list(img.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            parts[i] = "labels"
            return Path(*parts).with_suffix(".txt")
    return None


def file_hash(p: Path):
    return hashlib.md5(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    rnd = random.Random(cfg.get("seed", 42))
    classes = cfg["classes"]
    cidx = {c: i for i, c in enumerate(classes)}
    out = Path(cfg["output_dir"])
    if out.exists():
        shutil.rmtree(out)
    seen, items, stats = set(), [], defaultdict(Counter)

    for s in cfg["sources"]:
        root = Path(s["path"])
        if not root.exists():
            print(f"[skip] {s['name']}: {root} not found")
            continue
        src_names = load_source_names(root)
        cmap = {k.lower(): v for k, v in s["map"].items()}
        for img in sorted(p for p in root.rglob("*") if p.suffix.lower() in IMG_EXT):
            lp = label_path_for(img)
            if lp is None:
                continue
            h = file_hash(img)
            if h in seen:
                stats[s["name"]]["duplicate_images"] += 1
                continue
            rows = []
            if lp.exists():
                for line in lp.read_text().splitlines():
                    f = line.split()
                    if len(f) < 5:
                        continue
                    sid = int(float(f[0]))
                    if src_names:
                        name = src_names[sid].lower() if sid < len(src_names) else None
                    else:  # no data.yaml: assume the source already uses target names order
                        name = classes[sid].lower() if sid < len(classes) else None
                    tgt = cmap.get(name)
                    if tgt is None:
                        stats[s["name"]]["dropped_boxes"] += 1
                        continue
                    rows.append((cidx[tgt], f[1:5]))
            if not rows:
                stats[s["name"]]["background_images"] += 1
                if not cfg.get("keep_background_images", True) or not lp.exists():
                    continue
            seen.add(h)
            items.append((s, img, rows))

    # cap background images
    labeled = [i for i in items if i[2]]
    bg = [i for i in items if not i[2]]
    cap = int(cfg.get("max_background_fraction", 0.05) * max(len(labeled), 1))
    rnd.shuffle(bg)
    items = labeled + bg[:cap]
    rnd.shuffle(items)

    vf, tf = cfg["val_fraction"], cfg["test_fraction"]
    counts = Counter()
    for s, img, rows in items:
        mode = s.get("split", "auto")
        r = rnd.random()
        if mode == "train_only":
            split = "train"
        elif mode == "test_heavy":
            split = "test" if r < 0.5 else ("val" if r < 0.8 else "train")
        else:
            split = "test" if r < tf else ("val" if r < tf + vf else "train")
        name = f"{s['name']}__{img.stem}"
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        shutil.copy2(img, out / "images" / split / f"{name}{img.suffix.lower()}")
        (out / "labels" / split / f"{name}.txt").write_text(
            "\n".join(f"{c} {' '.join(b)}" for c, b in rows))
        counts[split] += 1
        stats[s["name"]][f"->{split}"] += 1

    data_yaml = {"path": str(out.resolve()), "train": "images/train", "val": "images/val",
                 "test": "images/test", "names": {i: c for i, c in enumerate(classes)}}
    (out / "data.yaml").write_text(yaml.safe_dump(data_yaml, sort_keys=False))
    print("\nImages per split:", dict(counts))
    for name, c in stats.items():
        print(f"  {name}: {dict(c)}")
    print(f"\nWrote {out/'data.yaml'}")
    print("Next: python scripts/05_audit_dataset.py --data", out / "data.yaml")


if __name__ == "__main__":
    main()
