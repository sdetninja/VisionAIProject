"""Build M1-style TrackResult dicts for tests."""
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 10, 31, 10, 30, 0, tzinfo=timezone.utc)
W, H = 1280, 720


def ts(frame_id, fps=10):
    return (T0 + timedelta(seconds=frame_id / fps)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def person(tid="CAM_001-T001", x=300, bottom=600, w=120, h=360, conf=0.95, age=10):
    return {"track_id": tid, "class": "person", "confidence": conf,
            "bbox": [x, bottom - h, x + w, bottom], "age_frames": age}


def head_box(p, cls="helmet", conf=0.9):
    x1, y1, x2, y2 = p["bbox"]
    w, h = x2 - x1, y2 - y1
    return {"class": cls, "confidence": conf, "bbox": [x1 + 0.25 * w, y1 + 0.02 * h, x1 + 0.75 * w, y1 + 0.15 * h]}


def hand_box(p, cls="gloves", conf=0.8, side="left"):
    x1, y1, x2, y2 = p["bbox"]
    w, h = x2 - x1, y2 - y1
    cx = x1 - 0.1 * w if side == "left" else x2 + 0.1 * w
    return {"class": cls, "confidence": conf, "bbox": [cx - 10, y1 + 0.5 * h, cx + 10, y1 + 0.58 * h]}


def vest_box(p, cls="vest", conf=0.9):
    x1, y1, x2, y2 = p["bbox"]
    w, h = x2 - x1, y2 - y1
    return {"class": cls, "confidence": conf, "bbox": [x1 + 0.1 * w, y1 + 0.2 * h, x1 + 0.9 * w, y1 + 0.55 * h]}


def frame(fid, persons=(), ppe=(), hazards=(), camera="CAM_001"):
    return {"schema_version": "1.0", "camera_id": camera, "frame_id": fid, "timestamp": ts(fid),
            "model_version": "test", "frame_width": W, "frame_height": H, "inference_ms": 10.0,
            "tracks": list(persons), "ppe_detections": list(ppe), "hazard_detections": list(hazards)}


def cfg(zones_yaml_rules, polygon="null", extra=""):
    """Tiny config factory: one whole-frame zone (or polygon) with given rules (YAML list text)."""
    import yaml
    from rule_engine.config import build_config
    poly = f"polygon: {polygon}" if polygon != "null" else ""
    doc = f"""
{extra}
zones:
  - zone_id: Z1
    camera_id: CAM_001
    {poly}
    rules:
{zones_yaml_rules}
"""
    return build_config(yaml.safe_load(doc))
