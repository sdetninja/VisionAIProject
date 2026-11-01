"""Glue to M2: `IncidentEventSink` can be passed as RuleEngineSink's on_event callback."""
from __future__ import annotations
import json
import time
from typing import Iterator


class IncidentEventSink:
    def __init__(self, service):
        self.service = service

    def __call__(self, event: dict):
        return self.service.ingest(event)


def read_events_jsonl(path: str, follow=False, poll=0.5) -> Iterator[dict]:
    with open(path) as f:
        while True:
            line = f.readline()
            if not line:
                if not follow:
                    return
                time.sleep(poll)
                continue
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
