"""Evaluate a trained model on the frozen TEST split and compare with go/no-go targets.

  python scripts/07_evaluate_baseline.py --weights runs/ppe-yolo-v0/weights/best.pt \
      --data data/processed/ppe/data.yaml --targets configs/targets.yaml --group ppe
Writes reports/baseline_<group>.md. Verdict per class: PASS / BELOW TARGET / UNRELIABLE (too few test instances).
"""
import argparse
from pathlib import Path

import yaml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--targets", default="configs/targets.yaml")
    ap.add_argument("--group", choices=["ppe", "hazard"], required=True)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default=None)
    a = ap.parse_args()

    from ultralytics import YOLO
    tg = yaml.safe_load(Path(a.targets).read_text())
    targets, min_n = tg[a.group], tg.get("min_test_instances_per_class", 30)
    m = YOLO(a.weights).val(data=a.data, split="test", imgsz=a.imgsz, conf=a.conf, device=a.device, plots=True)
    b = m.box
    n_inst = dict(zip(b.ap_class_index.tolist(), [int(x) for x in m.nt_per_class[b.ap_class_index]]))
    rows, passed, below, unreliable = [], 0, [], []
    for k, ci in enumerate(b.ap_class_index.tolist()):
        name = m.names[ci]
        p, r, ap50 = float(b.p[k]), float(b.r[k]), float(b.ap50[k])
        t = targets.get(name)
        n = n_inst[ci]
        if n < min_n:
            verdict = "UNRELIABLE (few test instances)"
            unreliable.append(name)
        elif t and r >= t["recall"] and ap50 >= t["map50"]:
            verdict, passed = "PASS", passed + 1
        else:
            verdict = "BELOW TARGET"
            below.append(name)
        tt = f"R>={t['recall']}, mAP50>={t['map50']}" if t else "-"
        rows.append(f"| {name} | {n} | {p:.2f} | {r:.2f} | {ap50:.2f} | {tt} | {verdict} |")
    report = ["# Baseline evaluation (" + a.group + ")", "", f"weights: `{a.weights}`  conf={a.conf}  imgsz={a.imgsz}", "",
              f"Overall mAP50={b.map50:.3f}  mAP50-95={b.map:.3f}", "",
              "| class | test n | precision | recall | mAP50 | target | verdict |", "|---|---|---|---|---|---|---|", *rows, "",
              "## Decision", "",
              f"- Passed: {passed}",
              f"- Below target (collect data / tune / defer rule): {', '.join(below) or 'none'}",
              f"- Unreliable (label more own-camera test data): {', '.join(unreliable) or 'none'}"]
    out = Path(f"reports/baseline_{a.group}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report))
    print("\n".join(report))


if __name__ == "__main__":
    main()
