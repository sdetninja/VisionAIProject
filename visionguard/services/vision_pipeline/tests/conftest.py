import cv2
import numpy as np
import pytest

from vision_pipeline.schemas import Detection


class FakeDetector:
    """Finds bright rectangles in synthetic frames: big one = person, small one = helmet. No ML needed."""
    model_version = "fake-v0"

    def infer(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w * h < 200:
                continue
            cls = "person" if h > 2 * w * 0.8 and h > 80 else "helmet"
            out.append(Detection(class_name=cls, confidence=0.9, bbox=[x, y, x + w, y + h]))
        return out


@pytest.fixture
def fake_detector():
    return FakeDetector()


@pytest.fixture
def synthetic_video(tmp_path):
    """3 s @ 30 fps: one 'person' (tall white rect) walking right, with a 'helmet' (small rect) on its head."""
    path = str(tmp_path / "walk.mp4")
    w, h, fps, n = 640, 360, 30, 90
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(n):
        img = np.zeros((h, w, 3), np.uint8)
        x = 50 + i * 4
        cv2.rectangle(img, (x, 100), (x + 60, 300), (255, 255, 255), -1)      # person
        cv2.rectangle(img, (x + 15, 70), (x + 45, 98), (255, 255, 255), -1)   # helmet (above, separate blob)
        vw.write(img)
    vw.release()
    return path
