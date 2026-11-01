"""Detection stage. YoloDetector wraps Ultralytics YOLO (imported lazily so tests run without torch)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import ModelConfig
from .schemas import Detection

log = logging.getLogger("vision.detector")


class Detector(Protocol):
    model_version: str

    def infer(self, frame_bgr: np.ndarray) -> list[Detection]: ...


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def pick_device() -> str:
    import torch
    if torch.cuda.is_available():
        return "0"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class YoloDetector:
    def __init__(self, cfg: ModelConfig):
        from ultralytics import YOLO
        self.cfg = cfg
        self.model = YOLO(cfg.weights)
        self.device = cfg.device or pick_device()
        self.half = cfg.half and self.device not in ("cpu", "mps")
        self.model_version = cfg.version or Path(cfg.weights).stem
        self.names = {int(k): _norm(v) for k, v in self.model.names.items()}
        self.keep = {_norm(c) for c in cfg.keep_classes} if cfg.keep_classes else None
        log.info("YOLO %s on %s, classes=%s", self.model_version, self.device, sorted(self.names.values()))
        self.infer(np.zeros((cfg.imgsz, cfg.imgsz, 3), np.uint8))   # warm-up

    def infer(self, frame_bgr: np.ndarray) -> list[Detection]:
        r = self.model.predict(frame_bgr, conf=self.cfg.conf, iou=self.cfg.iou, imgsz=self.cfg.imgsz,
                               device=self.device, half=self.half, verbose=False)[0]
        if r.boxes is None or len(r.boxes) == 0:
            return []
        xyxy = r.boxes.xyxy.cpu().numpy()
        conf = r.boxes.conf.cpu().numpy()
        cls = r.boxes.cls.cpu().numpy().astype(int)
        out = []
        for b, c, k in zip(xyxy, conf, cls):
            name = self.names.get(int(k), str(k))
            if self.keep and name not in self.keep:
                continue
            out.append(Detection(class_name=name, confidence=round(float(c), 4),
                                 bbox=[round(float(v), 1) for v in b]))
        return out


class SharedDetector:
    """Serialises access so several camera threads can share one model instance (and its GPU memory)."""

    def __init__(self, inner: Detector):
        self._inner = inner
        self._lock = threading.Lock()
        self.model_version = inner.model_version

    def infer(self, frame_bgr):
        with self._lock:
            return self._inner.infer(frame_bgr)
