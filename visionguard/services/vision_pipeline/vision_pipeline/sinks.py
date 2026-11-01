"""Where TrackResults go. M2 can attach in-process (CallbackSink) now, or consume JSONL / a bus later."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Protocol

from .schemas import TrackResult


class ResultSink(Protocol):
    def emit(self, result: TrackResult) -> None: ...


class CallbackSink:
    def __init__(self, fn: Callable[[TrackResult], None]):
        self.fn = fn

    def emit(self, result):
        self.fn(result)


class JsonlSink:
    def __init__(self, directory: str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, result):
        line = result.model_dump_json(by_alias=True)
        with self._lock, open(self.dir / f"{result.camera_id}.jsonl", "a") as f:
            f.write(line + "\n")
