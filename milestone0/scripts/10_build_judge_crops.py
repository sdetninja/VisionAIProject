"""Build the crop dataset for the ViT judges (second-opinion verifiers) from the combined Milestone 0 dataset.

  python scripts/10_build_judge_crops.py                       # -> data/processed/judge_crops/{train,val,test}/<class>/
  python scripts/10_build_judge_crops.py --dry-run             # counts only

The judges check ONE claim from the rule engine on a crop, so every class is a kind of crop the judge will see at run time:

  check    crop at run time                         classes        built from (combined dataset labels)
  helmet   head region of the person                helmet         helmet boxes (+ padding)
                                                    no_helmet      no_helmet boxes (+ padding)
  vest     the person box                           vest           person boxes with a vest box on the torso
                                                    no_vest        person boxes with no vest box, fully in frame
  hazard   the fire/smoke box (+ padding)           fire, smoke    fire / smoke boxes (+ padding)
                                                    none           random regions of images with no fire or smoke

One 7-class classifier per architecture; at run time only the classes of the check are compared (softmax renormalised
over that subset). Each source keeps its own split (the combined dataset's), so test crops never come from train images.
no_vest only uses sources that label vests themselves (construction, sh17): in hf the vest boxes come from a teacher model,
so a missing vest there may be a teacher miss rather than a real negative. hf persons WITH a teacher vest box (confidence
>= 0.5 when the dataset was built) are still used as vest examples.
"""
import argparse
import random
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/processed/combined"
OUT = ROOT / "data/processed/judge_crops"
NAMES = ["person", "helmet", "no_helmet", "vest", "gloves", "no_gloves", "goggles", "mask", "fire", "smoke"]
CLASSES = ["helmet", "no_helmet", "vest", "no_vest", "fire", "smoke", "none"]
VEST_SOURCES = ("construction", "sh17")          # sources whose own labels include vests: positives and negatives
VEST_POS_ONLY = ("hf",)                           # vests from a teacher model: positives only
MAX_SIDE = 256                     # crops are stored small; the models train at 224
CAP = {"train": 6000, "val": 1500, "test": 2000}   # per class, keeps classes balanced and training time bounded


def read_boxes(label_path, W, H):
    out = []
    for line in label_path.read_text().split("\n") if label_path.exists() else []:
        p = line.split()
        if len(p) != 5:
            continue
        c, x, y, w, h = int(p[0]), *map(float, p[1:])
        out.append((NAMES[c], (x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H))
    return out


def pad(b, f, W, H):
    x1, y1, x2, y2 = b
    dw, dh = (x2 - x1) * f, (y2 - y1) * f
    return max(0, x1 - dw), max(0, y1 - dh), min(W, x2 + dw), min(H, y2 + dh)


def ioa(inner, outer):
    """share of `inner` lying inside `outer`"""
    ix = max(0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return ix * iy / a if a > 0 else 0


def crops_for_image(img_path, label_path, rng):
    im = Image.open(img_path)
    W, H = im.size
    boxes = read_boxes(label_path, W, H)
    src = img_path.name.split("_")[0]
    found = []                                                     # (class, box)
    for name, *b in boxes:
        w, h = b[2] - b[0], b[3] - b[1]
        if name in ("helmet", "no_helmet") and min(w, h) >= 20:
            found.append((name, pad(b, 0.25, W, H)))
        elif name in ("fire", "smoke") and min(w, h) >= 24:
            found.append((name, pad(b, 0.15, W, H)))
    if src in VEST_SOURCES + VEST_POS_ONLY:
        vests = [b for n, *b in boxes if n == "vest"]
        for name, *p in boxes:
            if name != "person":
                continue
            w, h = p[2] - p[0], p[3] - p[1]
            if h < 96 or w < 32:
                continue                                            # too small to judge a vest
            torso = (p[0], p[1] + 0.2 * h, p[2], p[1] + 0.75 * h)
            has_vest = any(ioa(v, p) >= 0.5 and torso[1] <= (v[1] + v[3]) / 2 <= torso[3] for v in vests)
            in_frame = p[1] > 4 and p[3] < H - 4                    # same idea as the rule engine's edge checks
            if has_vest:
                found.append(("vest", pad(p, 0.05, W, H)))
            elif in_frame and src in VEST_SOURCES:
                found.append(("no_vest", pad(p, 0.05, W, H)))
    if not any(n in ("fire", "smoke") for n, *_ in boxes):          # background regions for the hazard check
        for _ in range(2):
            s = rng.uniform(0.15, 0.6) * min(W, H)
            x, y = rng.uniform(0, W - s), rng.uniform(0, H - s)
            found.append(("none", (x, y, x + s, y + s)))
    return im, found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    for split in ("train", "val", "test"):
        imgs = sorted((SRC / "images" / split).glob("*"))
        rng.shuffle(imgs)                                           # so the per-class cap samples across sources
        counts, skipped = Counter(), Counter()
        for img in imgs:
            lab = SRC / "labels" / split / (img.stem + ".txt")
            im, found = crops_for_image(img, lab, rng)
            for name, b in found:
                if counts[name] >= CAP[split]:
                    skipped[name] += 1
                    continue
                counts[name] += 1
                if a.dry_run:
                    continue
                d = OUT / split / name
                d.mkdir(parents=True, exist_ok=True)
                c = im.crop(tuple(int(v) for v in b)).convert("RGB")
                c.thumbnail((MAX_SIDE, MAX_SIDE))
                c.save(d / f"{img.stem}_{counts[name]:05d}.jpg", quality=92)
        print(split, {k: counts[k] for k in CLASSES}, "capped:", dict(skipped))


if __name__ == "__main__":
    main()
