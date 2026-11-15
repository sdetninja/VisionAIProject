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
    """With follow=True a missing file is waited for, so M3 can be started before M2."""
    import logging
    import os
    if follow and not os.path.exists(path):
        logging.getLogger("incident_service").info("waiting for %s to appear (it is created by the upstream service)", path)
        while not os.path.exists(path):
            time.sleep(poll)
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
