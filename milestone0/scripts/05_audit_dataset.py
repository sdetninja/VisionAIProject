"""Audit a prepared YOLO dataset: class balance per split, small-object share, warnings.

  python scripts/05_audit_dataset.py --data data/processed/ppe/data.yaml --out reports/audit_ppe.md
Small-object rule of thumb: box height < 3% of image height (at 640px that is ~19px) is very hard
for a YOLO nano/small model; classes dominated by those will need higher imgsz or a crop strategy.
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path

import yaml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="reports/audit.md")
    ap.add_argument("--min-test", type=int, default=30)
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.data).read_text())
    root, names = Path(cfg["path"]), cfg["names"]
    counts = {s: Counter() for s in ("train", "val", "test")}
    heights = defaultdict(list)
    n_img = Counter()
    for s in counts:
        for lp in (root / "labels" / s).glob("*.txt"):
            n_img[s] += 1
            for line in lp.read_text().splitlines():
                f = line.split()
                if len(f) >= 5:
                    c = int(float(f[0]))
                    counts[s][c] += 1
                    heights[c].append(float(f[4]))

    lines = ["# Dataset audit", "", f"Images: {dict(n_img)}", "",
             "| class | train | val | test | median box h (img frac) | % small (<3%) |", "|---|---|---|---|---|---|"]
    warnings = []
    for i in sorted(names):
        hs = sorted(heights[i])
        med = hs[len(hs) // 2] if hs else 0
        small = 100 * sum(h < 0.03 for h in hs) / len(hs) if hs else 0
        tr, va, te = (counts[s][i] for s in ("train", "val", "test"))
        lines.append(f"| {names[i]} | {tr} | {va} | {te} | {med:.3f} | {small:.0f}% |")
        if te < a.min_test:
            warnings.append(f"`{names[i]}`: only {te} test instances (<{a.min_test}); metrics unreliable - label more own-camera data")
        if tr and tr < 300:
            warnings.append(f"`{names[i]}`: only {tr} train instances; consider synthetic copy-paste or more data")
        if small > 50:
            warnings.append(f"`{names[i]}`: {small:.0f}% small boxes; try imgsz 960/1280 or a person-crop second stage")
    lines += ["", "## Warnings", ""] + ([f"- {w}" for w in warnings] or ["- none"])
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
