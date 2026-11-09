"""Per-class confidence filtering in YoloDetector, with ultralytics replaced by a stub (no torch or weights needed)."""
import sys
import types

import numpy as np

from vision_pipeline.config import ModelConfig
from vision_pipeline.detector import YoloDetector


class _T:                                    # minimal stand-in for a torch tensor: .cpu().numpy()
    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Model:
    names = {0: "person", 1: "fire"}
    ROWS = [[0, 0, 10, 10, 0.25, 0], [0, 0, 10, 10, 0.40, 0], [0, 0, 10, 10, 0.18, 1], [0, 0, 10, 10, 0.12, 1]]

    def __init__(self, *_):
        self.seen_conf = []

    def predict(self, frame, conf, **_):
        self.seen_conf.append(conf)
        rows = np.array([r for r in self.ROWS if r[4] >= conf], dtype=np.float32).reshape(-1, 6)
        res = types.SimpleNamespace(boxes=_Boxes(rows))
        return [res]


class _Boxes:
    def __init__(self, rows):
        self.xyxy, self.conf, self.cls = _T(rows[:, :4]), _T(rows[:, 4]), _T(rows[:, 5])
        self.n = len(rows)

    def __len__(self):
        return self.n


def _detector(monkeypatch, **cfg):
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=_Model))
    return YoloDetector(ModelConfig(weights="x.pt", device="cpu", **cfg))


def test_class_conf_lowers_threshold_only_for_listed_classes(monkeypatch):
    d = _detector(monkeypatch, conf=0.30, class_conf={"fire": 0.15})
    out = d.infer(np.zeros((64, 64, 3), np.uint8))
    assert d.model.seen_conf[-1] == 0.15                         # model runs at the lowest threshold
    assert sorted((x.class_name, x.confidence) for x in out) == [("fire", 0.18), ("person", 0.4)]


def test_without_class_conf_global_conf_applies(monkeypatch):
    d = _detector(monkeypatch, conf=0.30)
    out = d.infer(np.zeros((64, 64, 3), np.uint8))
    assert d.model.seen_conf[-1] == 0.30 and [x.class_name for x in out] == ["person"]
