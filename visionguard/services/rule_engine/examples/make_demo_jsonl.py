"""Generate a synthetic M1-style TrackResult JSONL so M2 can be demoed without cameras or models.

Story (CAM_001, 1280x720, 10 fps, 20 s):
  0-20 s  Worker A stands in the WELDING zone wearing a helmet and gloves                -> no event
  6-20 s  Worker B walks into the WELDING zone with NO helmet (explicit no_helmet)       -> PPE_VIOLATION
  10-20 s Worker C stands in the PRESS_RESTRICTED area                                    -> RESTRICTED_AREA_ENTRY
  15-20 s Fire detected on the floor                                                      -> HAZARD_DETECTED
Usage: python examples/make_demo_jsonl.py out/demo_results.jsonl
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 10, 31, 10, 30, 0, tzinfo=timezone.utc)
out = Path(sys.argv[1] if len(sys.argv) > 1 else "out/demo_results.jsonl")
out.parent.mkdir(parents=True, exist_ok=True)


def person(tid, x, bottom=650, w=120, h=380):
    return {"track_id": tid, "class": "person", "confidence": 0.95, "bbox": [x, bottom - h, x + w, bottom], "age_frames": 20}


def head(p, cls, conf=0.9):
    x1, y1, x2, y2 = p["bbox"]; w, h = x2 - x1, y2 - y1
    return {"class": cls, "confidence": conf, "bbox": [x1 + .25 * w, y1 + .02 * h, x1 + .75 * w, y1 + .15 * h]}


def vest(p, cls="vest", conf=0.9):
    x1, y1, x2, y2 = p["bbox"]; w, h = x2 - x1, y2 - y1
    return {"class": cls, "confidence": conf, "bbox": [x1 + .1 * w, y1 + .2 * h, x1 + .9 * w, y1 + .55 * h]}


def hand(p, cls, conf=0.8):
    x1, y1, x2, y2 = p["bbox"]; w, h = x2 - x1, y2 - y1
    return {"class": cls, "confidence": conf, "bbox": [x1 - 0.2 * w, y1 + .5 * h, x1, y1 + .58 * h]}


with open(out, "w") as f:
    for i in range(1, 201):
        t = i / 10
        tracks, ppe, hz = [], [], []
        a = person("CAM_001-T001", 250); tracks.append(a); ppe += [head(a, "helmet"), hand(a, "gloves"), vest(a)]
        if t >= 6:
            b = person("CAM_001-T002", 520); tracks.append(b); ppe += [head(b, "no_helmet"), vest(b)]
        if t >= 10:
            c = person("CAM_001-T003", 1000); tracks.append(c); ppe.append(vest(c))
        if t >= 15:
            hz.append({"class": "fire", "confidence": 0.82, "bbox": [900, 300, 980, 420]})
        rec = {"schema_version": "1.0", "camera_id": "CAM_001", "frame_id": i,
               "timestamp": (T0 + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
               "model_version": "demo", "frame_width": 1280, "frame_height": 720, "inference_ms": 12.0,
               "tracks": tracks, "ppe_detections": ppe, "hazard_detections": hz}
        f.write(json.dumps(rec) + "\n")
print("wrote", out)
