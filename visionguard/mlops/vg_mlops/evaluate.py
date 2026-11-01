"""Adapter: run ultralytics validation on the frozen TEST split and return registry-shaped metrics.
Lazy import: only this module needs ultralytics. (Untested here - mirrors milestone0/scripts/07_evaluate_baseline.py.)"""
from __future__ import annotations


def metrics_from_ultralytics(weights, data_yaml, imgsz=640, conf=0.25, device=None) -> dict:  # pragma: no cover
    from ultralytics import YOLO
    m = YOLO(weights).val(data=data_yaml, split="test", imgsz=imgsz, conf=conf, device=device, plots=False)
    b = m.box
    per = {}
    for k, ci in enumerate(b.ap_class_index.tolist()):
        per[m.names[ci]] = {"precision": float(b.p[k]), "recall": float(b.r[k]), "map50": float(b.ap50[k]),
                            "n": int(m.nt_per_class[ci])}
    return {"overall": {"map50": float(b.map50), "map50_95": float(b.map)}, "per_class": per,
            "latency_ms": float(m.speed["inference"]), "imgsz": imgsz, "conf": conf}
