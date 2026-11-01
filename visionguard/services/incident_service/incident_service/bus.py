"""Event bus abstraction. At-least-once delivery: consumers must dedupe on payload['incident_id']+version/event_id."""
from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Protocol


class EventBus(Protocol):
    def publish(self, topic: str, key: str, payload: dict) -> None: ...


class MemoryBus:
    def __init__(self):
        self.messages = []
        self.fail = False          # test hook
        self._l = threading.Lock()

    def publish(self, topic, key, payload):
        if self.fail:
            raise ConnectionError("bus down")
        with self._l:
            self.messages.append((topic, key, payload))


class JsonlBus:
    """Append-only file; tail it from M4/M5 during MVP. Replace with Kafka/Redis Streams later."""
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._l = threading.Lock()

    def publish(self, topic, key, payload):
        with self._l, open(self.path, "a") as f:
            f.write(json.dumps({"topic": topic, "key": key, "payload": payload}) + "\n")
            f.flush()


class KafkaBus:  # pragma: no cover - needs a broker
    def __init__(self, conf: dict):
        from confluent_kafka import Producer
        self.p = Producer({"enable.idempotence": True, "acks": "all", **conf})

    def publish(self, topic, key, payload):
        self.p.produce(topic, key=key, value=json.dumps(payload))
        if self.p.flush(5) > 0:
            raise TimeoutError("kafka flush timed out")
