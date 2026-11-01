import json, threading
from pathlib import Path


class JsonlPublisher:
    def __init__(self, path):
        self.p = Path(path); self.p.parent.mkdir(parents=True, exist_ok=True); self.l = threading.Lock()

    def publish(self, topic, key, payload):
        with self.l, open(self.p, "a") as f:
            f.write(json.dumps({"topic": topic, "key": key, "payload": payload}) + "\n")


class MemoryPublisher:
    def __init__(self): self.messages = []
    def publish(self, topic, key, payload): self.messages.append((topic, key, payload))
