"""Person-level copy-paste augmentation to fill rare classes (gloves, no_gloves, goggles, ...).

Why person-level: pasting an isolated glove crop onto a random background produces unrealistic
images. Pasting a whole person crop WITH its PPE labels keeps the context intact, and we change
the background, scale, position, flip and lighting. Use ONLY for training (split: train_only).

  python scripts/04_synthetic_copy_paste.py --data data/processed/ppe/data.yaml \
      --backgrounds data/backgrounds --out data/synthetic/ppe --n 600 --rare gloves no_gloves goggles no_helmet no_vest

--backgrounds: folder of empty factory scenes (photos of your own site with no people are best;
extract them with 01_capture_frames.py). Output is YOLO format: out/images/train, out/labels/train.

Generated image-synthesis (diffusion) data is NOT produced here. If you add it, keep it train-only,
label-check a sample by eye, and never let it enter val/test.
"""
import argparse
import random
from pathlib import Path

import cv2
import numpy as np
import yaml

IMG_EXT = {".jpg", ".jpeg", ".png"}


def read_labels(p):
    rows = []
    if p.exists():
        for line in p.read_text().splitlines():
            f = line.split()
            if len(f) >= 5:
                rows.append((int(float(f[0])), *map(float, f[1:5])))
    return rows


def collect_persons(root: Path, person_id: int, min_px: int, rare_ids: set):
    crops = []
    for img_p in sorted(p for p in (root / "images" / "train").glob("*") if p.suffix.lower() in IMG_EXT):
        rows = read_labels(root / "labels" / "train" / f"{img_p.stem}.txt")
        persons = [r for r in rows if r[0] == person_id]
        if not persons:
            continue
        img = None
        for _, cx, cy, w, h in persons:
            if img is None:
                img = cv2.imread(str(img_p))
                if img is None:
                    break
                H, W = img.shape[:2]
            x1, y1, x2, y2 = (cx - w / 2) * W, (cy - h / 2) * H, (cx + w / 2) * W, (cy + h / 2) * H
            if (x2 - x1) < min_px or (y2 - y1) < min_px * 2:
                continue
            inner = []
            for c, icx, icy, iw, ih in rows:
                if c == person_id:
                    continue
                px, py = icx * W, icy * H
                if x1 <= px <= x2 and y1 <= py <= y2:
                    inner.append((c, ((icx * W) - x1) / (x2 - x1), ((icy * H) - y1) / (y2 - y1),
                                  (iw * W) / (x2 - x1), (ih * H) / (y2 - y1)))
            crop = img[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
            if crop.size == 0:
                continue
            has_rare = any(i[0] in rare_ids for i in inner)
            crops.append((crop, inner, has_rare))
    return crops


def feather_mask(h, w, border=0.12):
    m = np.ones((h, w), np.float32)
    k = max(3, int(min(h, w) * border) | 1)
    m[:k // 2, :] = m[-(k // 2):, :] = 0
    m[:, :k // 2] = m[:, -(k // 2):] = 0
    return cv2.GaussianBlur(m, (k, k), 0)[..., None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--backgrounds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--rare", nargs="*", default=[])
    ap.add_argument("--max-persons", type=int, default=4)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    cfg = yaml.safe_load(Path(a.data).read_text())
    root = Path(cfg["path"])
    names = cfg["names"]
    inv = {v: int(k) for k, v in names.items()}
    rare_ids = {inv[r] for r in a.rare if r in inv}
    crops = collect_persons(root, inv["person"], 40, rare_ids)
    rare_crops = [c for c in crops if c[2]]
    print(f"{len(crops)} person crops, {len(rare_crops)} containing rare classes")
    bgs = [p for p in Path(a.backgrounds).glob("*") if p.suffix.lower() in IMG_EXT]
    if not crops or not bgs:
        raise SystemExit("Need person crops (run 03_prepare_dataset first) and background images")

    out = Path(a.out)
    (out / "images" / "train").mkdir(parents=True, exist_ok=True)
    (out / "labels" / "train").mkdir(parents=True, exist_ok=True)
    (out / "data.yaml").write_text(yaml.safe_dump({"names": names}))

    for n in range(a.n):
        bg = cv2.imread(str(rnd.choice(bgs)))
        if bg is None:
            continue
        s = a.imgsz / max(bg.shape[:2])
        bg = cv2.resize(bg, None, fx=s, fy=s)
        H, W = bg.shape[:2]
        labels = []
        for _ in range(rnd.randint(1, a.max_persons)):
            crop, inner, _r = rnd.choice(rare_crops if (rare_crops and rnd.random() < 0.7) else crops)
            target_h = rnd.uniform(0.25, 0.75) * H
            sc = target_h / crop.shape[0]
            c = cv2.resize(crop, None, fx=sc, fy=sc)
            flip = rnd.random() < 0.5
            if flip:
                c = c[:, ::-1]
            # lighting jitter
            c = np.clip(c.astype(np.float32) * rnd.uniform(0.75, 1.25) + rnd.uniform(-20, 20), 0, 255)
            ch, cw = c.shape[:2]
            if cw >= W or ch >= H:
                continue
            x0, y0 = rnd.randint(0, W - cw), rnd.randint(0, H - ch)
            m = feather_mask(ch, cw)
            roi = bg[y0:y0 + ch, x0:x0 + cw].astype(np.float32)
            bg[y0:y0 + ch, x0:x0 + cw] = (roi * (1 - m) + c * m).astype(np.uint8)
            labels.append((inv["person"], (x0 + cw / 2) / W, (y0 + ch / 2) / H, cw / W, ch / H))
            for cid, rx, ry, rw, rh in inner:
                rx = 1 - rx if flip else rx
                labels.append((cid, (x0 + rx * cw) / W, (y0 + ry * ch) / H, rw * cw / W, rh * ch / H))
        if rnd.random() < 0.3:
            bg = cv2.GaussianBlur(bg, (0, 0), rnd.uniform(0.5, 1.5))  # mimic CCTV softness
        stem = f"synth_{n:05d}"
        cv2.imwrite(str(out / "images" / "train" / f"{stem}.jpg"), bg, [cv2.IMWRITE_JPEG_QUALITY, rnd.randint(60, 95)])
        (out / "labels" / "train" / f"{stem}.txt").write_text(
            "\n".join(f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}" for c, x, y, w, h in labels))
    print(f"Wrote {a.n} synthetic images to {out}. Eyeball 30 of them before training!")


if __name__ == "__main__":
    main()
