"""Tracking stage: ByteTrack (via the `supervision` library) with anonymous, camera-scoped IDs.

Only classes in `track_classes` (persons, machinery) get track IDs; PPE detections are passed through
per frame and associated to tracks by the Rule Engine (M2).
Limitation: one tracker instance serves all tracked classes, so a person and a truck that overlap
heavily could in rare cases swap IDs. Split into per-class trackers if profiling shows it matters.
"""
from __future__ import annotations

import numpy as np
import supervision as sv

from .config import TrackerConfig
from .schemas import Detection, Track


class ByteTrackTracker:
    def __init__(self, camera_id: str, cfg: TrackerConfig, frame_rate: float):
        self.camera_id = camera_id
        self.classes = [c for c in cfg.track_classes]
        self._cidx = {c: i for i, c in enumerate(self.classes)}
        kw = dict(lost_track_buffer=cfg.lost_buffer, minimum_matching_threshold=cfg.match_threshold,
                  frame_rate=max(1, int(round(frame_rate))))
        try:
            self._bt = sv.ByteTrack(track_activation_threshold=cfg.activation_threshold, **kw)
        except TypeError:   # older supervision releases
            self._bt = sv.ByteTrack(track_thresh=cfg.activation_threshold, track_buffer=cfg.lost_buffer,
                                    match_thresh=cfg.match_threshold, frame_rate=kw["frame_rate"])
        self._first_seen: dict[int, int] = {}
        self._last_seen: dict[int, int] = {}

    def split(self, dets: list[Detection]) -> tuple[list[Detection], list[Detection]]:
        tracked = [d for d in dets if d.class_name in self._cidx]
        others = [d for d in dets if d.class_name not in self._cidx]
        return tracked, others

    def update(self, tracked: list[Detection], frame_id: int) -> list[Track]:
        if tracked:
            sd = sv.Detections(
                xyxy=np.array([d.bbox for d in tracked], dtype=np.float32),
                confidence=np.array([d.confidence for d in tracked], dtype=np.float32),
                class_id=np.array([self._cidx[d.class_name] for d in tracked], dtype=int),
            )
        else:
            sd = sv.Detections.empty()
        out = self._bt.update_with_detections(sd)
        tracks: list[Track] = []
        for i in range(len(out)):
            tid = out.tracker_id[i]
            if tid is None or int(tid) < 0:
                continue
            tid = int(tid)
            self._first_seen.setdefault(tid, frame_id)
            self._last_seen[tid] = frame_id
            cid = int(out.class_id[i]) if out.class_id is not None else 0
            tracks.append(Track(
                track_id=f"{self.camera_id}-T{tid:03d}",
                class_name=self.classes[cid],
                confidence=round(float(out.confidence[i]), 4) if out.confidence is not None else 0.0,
                bbox=[round(float(v), 1) for v in out.xyxy[i]],
                age_frames=frame_id - self._first_seen[tid] + 1,
            ))
        if len(self._first_seen) > 2000:   # prune long-dead tracks
            for t in [t for t, f in self._last_seen.items() if f < frame_id - 2000]:
                self._first_seen.pop(t, None)
                self._last_seen.pop(t, None)
        return tracks
