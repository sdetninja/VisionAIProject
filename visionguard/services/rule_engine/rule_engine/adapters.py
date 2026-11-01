"""Glue to M1 (input) and M3 (output)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Iterator

from .engine import RuleEngine


class RuleEngineSink:
    """Attach to M1 in-process:  pipeline.add_sink(RuleEngineSink(engine, on_event=incident_service.ingest))
    Implements M1's ResultSink protocol (emit(TrackResult))."""

    def __init__(self, engine: RuleEngine, on_event: Callable[[dict], None]):
        self.engine, self.on_event = engine, on_event

    def emit(self, result) -> None:
        for ev in self.engine.process(result):
            self.on_event(ev)


class JsonlEventWriter:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")


def read_jsonl(path: str, follow: bool = False, poll_s: float = 0.2, idle_stop_s: float = 0.0) -> Iterator[dict]:
    """Read M1's JSONL output; with follow=True behave like `tail -f` (stop after idle_stop_s of silence if > 0)."""
    with open(path) as f:
        idle = 0.0
        while True:
            line = f.readline()
            if line:
                idle = 0.0
                if line.strip():
                    yield json.loads(line)
                continue
            if not follow:
                return
            time.sleep(poll_s)
            idle += poll_s
            if idle_stop_s and idle >= idle_stop_s:
                return
