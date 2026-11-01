"""Evidence: fetch frames from M1 (in-process buffer or HTTP), annotate, store."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Callable, Optional, Protocol
import cv2
import numpy as np


class EvidenceStore(Protocol):
    def put(self, key: str, jpeg: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...


class LocalEvidenceStore:
    """Filesystem store. Swap for MinioEvidenceStore in production (same interface)."""
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, jpeg: bytes) -> str:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("bad evidence key")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(jpeg)
        os.replace(tmp, p)
        return f"file://{key}"

    def get(self, uri: str) -> bytes:
        key = uri.removeprefix("file://")
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("bad evidence uri")
        return p.read_bytes()


class MinioEvidenceStore:  # pragma: no cover - needs a server
    def __init__(self, endpoint, access_key, secret_key, bucket="evidence", secure=False):
        from minio import Minio
        self.c, self.bucket = Minio(endpoint, access_key, secret_key, secure=secure), bucket
        if not self.c.bucket_exists(bucket):
            self.c.make_bucket(bucket)

    def put(self, key, jpeg):
        import io
        self.c.put_object(self.bucket, key, io.BytesIO(jpeg), len(jpeg), content_type="image/jpeg")
        return f"s3://{self.bucket}/{key}"

    def get(self, uri):
        r = self.c.get_object(self.bucket, uri.split("/", 3)[3])
        try:
            return r.read()
        finally:
            r.close()


# frame fetcher: (camera_id, frame_id) -> BGR ndarray or None
FrameFetcher = Callable[[str, int], Optional[np.ndarray]]


def buffer_fetcher(buffer) -> FrameFetcher:
    """Adapter for M1 FrameBuffer. Handles get() returning an ndarray, a packet with .image, or JPEG bytes."""
    def fetch(camera_id, frame_id):
        r = buffer.get(camera_id, frame_id)
        if r is None:
            return None
        r = getattr(r, "image", r)
        if isinstance(r, (bytes, bytearray)):
            return cv2.imdecode(np.frombuffer(r, np.uint8), cv2.IMREAD_COLOR)
        return r
    return fetch


def http_fetcher(base_url: str, timeout: float = 3.0) -> FrameFetcher:  # pragma: no cover - needs M1 running
    import urllib.request
    def fetch(camera_id, frame_id):
        try:
            with urllib.request.urlopen(f"{base_url.rstrip('/')}/cameras/{camera_id}/frames/{frame_id}", timeout=timeout) as r:
                return cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None
    return fetch


def _blur_faces(img):
    try:
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        for x, y, w, h in det.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.1, 5):
            img[y:y + h, x:x + w] = cv2.GaussianBlur(img[y:y + h, x:x + w], (0, 0), 15)
    except Exception:
        pass
    return img


def annotate(img, event: dict, draw=True, blur=False):
    img = img.copy()
    if blur:
        img = _blur_faces(img)
    bbox = event.get("bbox")
    if draw and bbox and len(bbox) == 4:
        x1, y1, x2, y2 = (int(v) for v in bbox)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
        label = event.get("rule_id", event["event_type"])
        cv2.putText(img, label, (x1, max(12, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    return img


def collect_evidence(event: dict, fetch: FrameFetcher, store: EvidenceStore, incident_key: str,
                     max_frames=3, quality=85, draw=True, blur=False) -> list:
    """Return stored URIs. Picks up to max_frames evenly spaced from evidence_frames; failures are skipped."""
    frames = list(event.get("evidence_frames") or [])
    if not frames:
        return []
    if len(frames) > max_frames:
        idx = np.linspace(0, len(frames) - 1, max_frames).round().astype(int)
        frames = [frames[i] for i in idx]
    uris = []
    for fid in frames:
        img = fetch(event["camera_id"], int(fid))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", annotate(img, event, draw, blur), [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            uris.append(store.put(f"{event['camera_id']}/{incident_key}/{event['event_id'][:8]}_{fid}.jpg", buf.tobytes()))
    return uris
