import cv2
import numpy as np

from .schemas import TrackResult

_PALETTE = {"person": (60, 180, 75), "helmet": (255, 140, 0), "no_helmet": (0, 0, 230),
            "vest": (0, 200, 255), "no_vest": (0, 0, 230), "gloves": (255, 0, 255),
            "no_gloves": (0, 0, 230), "fire": (0, 0, 255), "smoke": (150, 150, 150)}


def _color(name: str):
    return _PALETTE.get(name, (200, 200, 50))


def _box(img, bbox, label, color):
    x1, y1, x2, y2 = (int(v) for v in bbox)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(img, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), color, -1)
    cv2.putText(img, label, (x1 + 2, max(10, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)


def annotate(img: np.ndarray, res: TrackResult, fps: float = 0.0) -> np.ndarray:
    out = img.copy()
    for d in res.ppe_detections + res.hazard_detections:
        _box(out, d.bbox, f"{d.class_name} {d.confidence:.2f}", _color(d.class_name))
    for t in res.tracks:
        _box(out, t.bbox, f"{t.track_id.split('-')[-1]} {t.class_name} {t.confidence:.2f}", _color(t.class_name))
    hud = f"{res.camera_id} f={res.frame_id} tracks={len(res.tracks)} infer={res.inference_ms:.0f}ms fps={fps:.1f}"
    cv2.putText(out, hud, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(out, hud, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out
