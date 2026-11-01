"""Camera ingestion: capture, sample, reconnect, and bounded hand-off to the pipeline.

Live sources (webcam/RTSP): time-based sampling at target_fps; if the consumer is slow the OLDEST
queued frame is dropped (and counted) so latency never grows.
Files: index-based sampling; with realtime=False the reader blocks instead of dropping, which makes
offline replays and tests deterministic.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import cv2

from .config import CameraConfig
from .metrics import CAMERA_UP, FRAMES_CAPTURED, FRAMES_DROPPED
from .schemas import FramePacket, utc_now_iso

log = logging.getLogger("vision.ingestion")
_STREAM_PREFIXES = ("rtsp://", "rtsps://", "http://", "https://", "rtmp://")


class CameraReader(threading.Thread):
    def __init__(self, cfg: CameraConfig, queue_size: int = 2):
        super().__init__(name=f"reader-{cfg.camera_id}", daemon=True)
        self.cfg = cfg
        self.source = int(cfg.source) if str(cfg.source).isdigit() else cfg.source
        self.is_file = isinstance(self.source, str) and not self.source.lower().startswith(_STREAM_PREFIXES)
        self._q: deque[FramePacket] = deque()
        self._qsize = queue_size
        self._cond = threading.Condition()
        self._stop_evt = threading.Event()
        self.state = "starting"       # starting | running | reconnecting | finished | stopped
        self.finished = False
        self.frame_id = 0
        self.captured = 0
        self.dropped = 0

    # ---- consumer side -------------------------------------------------
    def get(self, timeout: float = 0.5) -> Optional[FramePacket]:
        with self._cond:
            if not self._q:
                self._cond.wait(timeout)
            if self._q:
                pkt = self._q.popleft()
                self._cond.notify_all()
                return pkt
        return None

    def pending(self) -> int:
        with self._cond:
            return len(self._q)

    def stop(self) -> None:
        self._stop_evt.set()
        with self._cond:
            self._cond.notify_all()

    # ---- producer side -------------------------------------------------
    def _open(self) -> Optional[cv2.VideoCapture]:
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return None
        if not self.is_file:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def run(self) -> None:
        cam = self.cfg.camera_id
        backoff = 1.0
        while not self._stop_evt.is_set():
            cap = self._open()
            if cap is None:
                self.state = "reconnecting"
                CAMERA_UP.labels(cam).set(0)
                log.warning("%s: cannot open %r, retrying in %.0fs", cam, self.source, backoff)
                self._stop_evt.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            self.state = "running"
            CAMERA_UP.labels(cam).set(1)
            log.info("%s: opened %r", cam, self.source)
            try:
                self._read_loop(cap)
            finally:
                cap.release()
            if self.finished:
                break
        CAMERA_UP.labels(cam).set(0)
        self.state = "finished" if self.finished else "stopped"
        with self._cond:
            self._cond.notify_all()

    def _read_loop(self, cap: cv2.VideoCapture) -> None:
        cfg = self.cfg
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if src_fps <= 1 or src_fps > 240:
            src_fps = 30.0
        step = max(1, round(src_fps / cfg.target_fps)) if self.is_file else 1
        interval = 1.0 / cfg.target_fps
        last_emit = 0.0
        idx = 0
        t_start = time.monotonic()
        while not self._stop_evt.is_set():
            ok, img = cap.read()
            if not ok:
                if self.is_file:
                    if cfg.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        idx, t_start = 0, time.monotonic()
                        continue
                    self.finished = True
                    return
                self.state = "reconnecting"
                CAMERA_UP.labels(cfg.camera_id).set(0)
                log.warning("%s: read failed, reconnecting", cfg.camera_id)
                return
            idx += 1
            if self.is_file and cfg.realtime:   # pace to native speed
                ahead = t_start + idx / src_fps - time.monotonic()
                if ahead > 0:
                    time.sleep(ahead)
            if self.is_file:
                if idx % step != 0:
                    continue
            else:
                now = time.monotonic()
                if now - last_emit < interval * 0.9:
                    continue
                last_emit = now
            if cfg.max_width and img.shape[1] > cfg.max_width:
                scale = cfg.max_width / img.shape[1]
                img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            self._emit(img)

    def _emit(self, img) -> None:
        cam = self.cfg.camera_id
        block = self.is_file and not self.cfg.realtime
        self.frame_id += 1
        pkt = FramePacket(cam, self.frame_id, utc_now_iso(), img, time.monotonic())
        with self._cond:
            if block:
                while len(self._q) >= self._qsize and not self._stop_evt.is_set():
                    self._cond.wait(0.1)
            elif len(self._q) >= self._qsize:
                self._q.popleft()
                self.dropped += 1
                FRAMES_DROPPED.labels(cam).inc()
            self._q.append(pkt)
            self._cond.notify_all()
        self.captured += 1
        FRAMES_CAPTURED.labels(cam).inc()
