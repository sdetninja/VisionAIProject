"""Pipeline orchestrator: one reader thread + one worker thread per camera, shared detector."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

import cv2

from .annotate import annotate
from .config import PipelineConfig
from .frame_buffer import FrameBuffer, InMemoryFrameBuffer
from .ingestion import CameraReader
from .metrics import (ACTIVE_TRACKS, DETECTIONS, FRAMES_PROCESSED, INFERENCE_SECONDS, PIPELINE_LATENCY,
                      PROCESSING_ERRORS, PROCESSING_FPS)
from .schemas import FramePacket, TrackResult
from .sinks import JsonlSink, ResultSink
from .tracker import ByteTrackTracker

log = logging.getLogger("vision.pipeline")


class CameraWorker(threading.Thread):
    def __init__(self, reader: CameraReader, detector, hazard_detector, cfg: PipelineConfig,
                 buffer: FrameBuffer, sinks: list[ResultSink]):
        super().__init__(name=f"worker-{reader.cfg.camera_id}", daemon=True)
        self.reader, self.detector, self.hazard = reader, detector, hazard_detector
        self.cfg, self.buffer, self.sinks = cfg, buffer, sinks
        self.camera_id = reader.cfg.camera_id
        self.tracker = ByteTrackTracker(self.camera_id, cfg.tracker, reader.cfg.target_fps)
        self.history: deque[TrackResult] = deque(maxlen=cfg.results_history)
        self.latest: Optional[tuple] = None          # (image, TrackResult)
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self.processed = 0
        self.errors = 0
        self.fps = 0.0
        self._t_last = time.monotonic()

    def stop(self):
        self._stop_evt.set()

    def snapshot(self):
        with self._lock:
            return self.latest

    def run(self):
        while not self._stop_evt.is_set():
            pkt = self.reader.get(timeout=0.5)
            if pkt is None:
                if self.reader.state in ("finished", "stopped") and self.reader.pending() == 0:
                    break
                continue
            try:
                self._process(pkt)
            except Exception:   # a bad frame must never kill the camera
                self.errors += 1
                PROCESSING_ERRORS.labels(self.camera_id).inc()
                log.exception("%s: frame %s failed", self.camera_id, pkt.frame_id)

    def _process(self, pkt: FramePacket) -> None:
        cam = self.camera_id
        ok, enc = cv2.imencode(".jpg", pkt.image, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.buffer.jpeg_quality])
        if ok:
            self.buffer.put(cam, pkt.frame_id, enc.tobytes())

        t0 = time.perf_counter()
        dets = self.detector.infer(pkt.image)
        hazards = self.hazard.infer(pkt.image) if self.hazard else []
        infer_s = time.perf_counter() - t0
        INFERENCE_SECONDS.labels(cam).observe(infer_s)

        tracked, others = self.tracker.split(dets)
        tracks = self.tracker.update(tracked, pkt.frame_id)

        res = TrackResult(
            camera_id=cam, frame_id=pkt.frame_id, timestamp=pkt.timestamp,
            model_version=self.detector.model_version,
            frame_width=pkt.width, frame_height=pkt.height,
            inference_ms=round(infer_s * 1000, 1),
            tracks=tracks, ppe_detections=others, hazard_detections=hazards)

        for d in dets + hazards:
            DETECTIONS.labels(cam, d.class_name).inc()
        ACTIVE_TRACKS.labels(cam).set(len(tracks))
        FRAMES_PROCESSED.labels(cam).inc()
        PIPELINE_LATENCY.labels(cam).observe(time.monotonic() - pkt.captured_monotonic)

        now = time.monotonic()
        inst = 1.0 / max(now - self._t_last, 1e-6)
        self._t_last = now
        self.fps = inst if self.processed == 0 else 0.9 * self.fps + 0.1 * inst
        PROCESSING_FPS.labels(cam).set(self.fps)
        self.processed += 1

        with self._lock:
            self.latest = (pkt.image, res)
            self.history.append(res)
        for s in self.sinks:
            try:
                s.emit(res)
            except Exception:
                log.exception("sink failed")


class VisionPipeline:
    def __init__(self, cfg: PipelineConfig, detector=None, hazard_detector=None,
                 buffer: Optional[FrameBuffer] = None, sinks: Optional[list[ResultSink]] = None):
        self.cfg = cfg
        if detector is None:
            from .detector import SharedDetector, YoloDetector
            detector = SharedDetector(YoloDetector(cfg.model))
            if cfg.hazard_model and hazard_detector is None:
                hazard_detector = SharedDetector(YoloDetector(cfg.hazard_model))
        self.detector, self.hazard = detector, hazard_detector
        max_frames = int(cfg.buffer.seconds * max([c.target_fps for c in cfg.cameras] + [1])) + 1
        self.buffer = buffer or InMemoryFrameBuffer(max_frames)
        self.sinks: list[ResultSink] = list(sinks or [])
        if cfg.jsonl_dir:
            self.sinks.append(JsonlSink(cfg.jsonl_dir))
        self.readers: dict[str, CameraReader] = {}
        self.workers: dict[str, CameraWorker] = {}

    def add_sink(self, sink: ResultSink):
        self.sinks.append(sink)

    def start(self):
        for c in self.cfg.cameras:
            if not c.enabled:
                continue
            r = CameraReader(c)
            w = CameraWorker(r, self.detector, self.hazard, self.cfg, self.buffer, self.sinks)
            self.readers[c.camera_id], self.workers[c.camera_id] = r, w
            r.start()
            w.start()
        log.info("pipeline started: %s", list(self.workers))

    def stop(self):
        for r in self.readers.values():
            r.stop()
        for w in self.workers.values():
            w.stop()
        for t in [*self.readers.values(), *self.workers.values()]:
            t.join(timeout=3)

    def wait_until_finished(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        for w in self.workers.values():
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            w.join(left)
        return not any(w.is_alive() for w in self.workers.values())

    def status(self) -> list[dict]:
        out = []
        for cid, r in self.readers.items():
            w = self.workers[cid]
            out.append({"camera_id": cid, "state": r.state, "captured": r.captured, "dropped": r.dropped,
                        "processed": w.processed, "errors": w.errors, "fps": round(w.fps, 2),
                        "latest_frame_id": self.buffer.latest_id(cid)})
        return out

    def mjpeg_frames(self, camera_id: str, fps: float = 10.0, draw: bool = True):
        w = self.workers.get(camera_id)
        if w is None:
            return
        last = -1
        while True:
            snap = w.snapshot()
            if snap is None or snap[1].frame_id == last:
                if w.reader.state in ("finished", "stopped") and not w.is_alive():
                    return
                time.sleep(0.03)
                continue
            img, res = snap
            last = res.frame_id
            frame = annotate(img, res, w.fps) if draw else img
            ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + enc.tobytes() + b"\r\n"
            time.sleep(1.0 / fps)
