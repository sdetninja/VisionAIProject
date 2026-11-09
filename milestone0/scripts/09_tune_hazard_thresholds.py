"""Pick per-class confidence thresholds for hazard alarms (fire/smoke) from FRAME-level recall and false alarms.

  python scripts/09_tune_hazard_thresholds.py --weights runs/.../best.pt --data data/processed/combined/data.yaml

Box mAP under-rates alarm quality: an alarm needs the frame flagged, not every flame outlined. Per class this measures
  frame recall = share of frames that contain the class where it is detected at >= threshold
  false alarm  = share of frames WITHOUT the class where it is detected at >= threshold (PPE scenes included)
Thresholds are CHOSEN on the val split (lowest threshold whose false-alarm rate fits the budget) and REPORTED on test,
so the test numbers stay an honest estimate. The N-of-M columns estimate the rule engine's debouncing
(confirm n of the last m frames) assuming independent frames: optimistic for false alarms from a persistent lookalike
(a sunset stays in view), so treat those as a lower bound and keep human review on hazard events.
Writes reports/hazard_thresholds_<run>.md and prints the YAML to paste into the rule engine / vision pipeline configs.
"""
import argparse
from math import comb
from pathlib import Path

import yaml

GRID = [round(0.05 * i, 2) for i in range(1, 13)]           # 0.05 .. 0.60


def p_confirm(p, n, m):
    """P(at least n hits in m independent frames) with per-frame hit probability p."""
    return sum(comb(m, k) * p ** k * (1 - p) ** (m - k) for k in range(n, m + 1))


def frame_scores(model, root, split, ids, device):
    """{image: (set of true class ids, {class id: max predicted conf})}"""
    imgs = sorted((root / "images" / split).glob("*"))
    out = {}
    for i in range(0, len(imgs), 64):
        chunk = imgs[i:i + 64]
        for p, r in zip(chunk, model.predict([str(x) for x in chunk], conf=min(GRID), device=device, verbose=False)):
            lbl = root / "labels" / split / (p.stem + ".txt")
            truth = {int(t.split()[0]) for t in lbl.read_text().splitlines() if t.strip()} if lbl.exists() else set()
            best = {}
            for k, c in zip(r.boxes.cls.int().tolist(), r.boxes.conf.tolist()):
                if k in ids:
                    best[k] = max(best.get(k, 0.0), c)
            out[p] = (truth, best)
    return out


def rates(scores, k, thr):
    pos = [b for t, b in scores.values() if k in t]
    neg = [b for t, b in scores.values() if k not in t]
    rec = sum(b.get(k, 0) >= thr for b in pos) / max(len(pos), 1)
    fa = sum(b.get(k, 0) >= thr for b in neg) / max(len(neg), 1)
    return rec, fa, len(pos), len(neg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--classes", nargs="+", default=["fire", "smoke"])
    ap.add_argument("--max-fa", nargs="+", type=float, default=[0.01, 0.02],
                    help="per-frame false-alarm budget per class (same order as --classes), chosen on val")
    ap.add_argument("--confirm", nargs=2, type=int, default=[3, 5], metavar=("N", "M"), help="rule engine debounce")
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()

    from ultralytics import YOLO
    d = yaml.safe_load(Path(a.data).read_text())
    root = Path(d["path"])
    names = d["names"] if isinstance(d["names"], dict) else dict(enumerate(d["names"]))
    by_name = {v: int(k) for k, v in names.items()}
    ids = {by_name[c] for c in a.classes}
    model = YOLO(a.weights)
    n, m = a.confirm
    val = frame_scores(model, root, "val", ids, a.device)
    test = frame_scores(model, root, "test", ids, a.device)

    run = Path(a.weights).parent.parent.name
    rows = [f"# Hazard thresholds: {run}", "", f"Weights: `{a.weights}`. Debounce: {n} of {m} frames. "
            "Chosen on val, reported on test. N-of-M columns assume independent frames (see script docstring).", ""]
    chosen = {}
    for c, budget in zip(a.classes, a.max_fa):
        k = by_name[c]
        ok = [t for t in GRID if rates(val, k, t)[1] <= budget]
        thr = min(ok) if ok else max(GRID)
        chosen[c] = thr
        _, _, npos, nneg = rates(test, k, thr)
        rows += [f"## {c}: threshold **{thr}** (val false-alarm budget {budget:.1%})", "",
                 f"Test: {npos} frames with {c}, {nneg} without.", "",
                 "| conf | val recall | val false alarm | test recall | test false alarm | "
                 f"test alarm P({n}/{m}) | test false alarm P({n}/{m}) |", "|---|---|---|---|---|---|---|"]
        for t in GRID:
            vr, vf, _, _ = rates(val, k, t)
            tr, tf, _, _ = rates(test, k, t)
            mark = " ←" if t == thr else ""
            rows.append(f"| {t:.2f}{mark} | {vr:.0%} | {vf:.1%} | {tr:.0%} | {tf:.1%} | "
                        f"{p_confirm(tr, n, m):.0%} | {p_confirm(tf, n, m):.2%} |")
        rows.append("")
    snippet = {"rule_engine (R-FLOOR-HAZARD)": {"class_conf": chosen},
               "vision_pipeline (model)": {"class_conf": chosen}}
    rows += ["## Config", "", "```yaml", yaml.safe_dump(snippet, sort_keys=False).strip(), "```", ""]
    text = "\n".join(rows)
    print(text)
    Path("reports").mkdir(exist_ok=True)
    Path(f"reports/hazard_thresholds_{run}.md").write_text(text)


if __name__ == "__main__":
    main()
