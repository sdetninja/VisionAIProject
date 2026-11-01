"""Rolling JPEG frame buffer used for evidence retrieval.

M3 (Incident Service) must fetch evidence by (camera_id, frame_id) through this interface -
never by reaching into the pipeline's memory. The in-memory implementation serves the MVP;
swap in a Redis/object-store implementation of FrameBuffer for a distributed deployment.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Optional


class FrameBuffer(ABC):
    @abstractmethod
    def put(self, camera_id: str, frame_id: int, jpeg: bytes) -> None: ...

    @abstractmethod
    def get(self, camera_id: str, frame_id: int) -> Optional[bytes]: ...

    @abstractmethod
    def get_range(self, camera_id: str, start_id: int, end_id: int) -> dict[int, bytes]: ...

    @abstractmethod
    def latest_id(self, camera_id: str) -> Optional[int]: ...


class InMemoryFrameBuffer(FrameBuffer):
    def __init__(self, max_frames_per_camera: int):
        self._max = max(1, int(max_frames_per_camera))
        self._data: dict[str, OrderedDict[int, bytes]] = {}
        self._lock = threading.Lock()

    def put(self, camera_id, frame_id, jpeg):
        with self._lock:
            od = self._data.setdefault(camera_id, OrderedDict())
            od[frame_id] = jpeg
            while len(od) > self._max:
                od.popitem(last=False)

    def get(self, camera_id, frame_id):
        with self._lock:
            return self._data.get(camera_id, {}).get(frame_id)

    def get_range(self, camera_id, start_id, end_id):
        with self._lock:
            return {k: v for k, v in self._data.get(camera_id, {}).items() if start_id <= k <= end_id}

    def latest_id(self, camera_id):
        with self._lock:
            od = self._data.get(camera_id)
            return next(reversed(od)) if od else None
