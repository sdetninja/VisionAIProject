from __future__ import annotations
from dataclasses import dataclass, field, fields
import yaml


@dataclass
class Config:
    db_path: str = "incidents.db"
    evidence_dir: str = "evidence"
    merge_window_sec: float = 10.0       # events for same camera/zone/track within this gap -> same incident
    max_evidence_frames: int = 3
    jpeg_quality: int = 85
    draw_boxes: bool = True
    blur_faces: bool = False             # best-effort Haar face blur on stored evidence
    frame_source_url: str = ""           # M1 API base, e.g. http://localhost:8001 (empty = in-process buffer)
    bus: str = "jsonl"                   # jsonl | memory
    bus_path: str = "bus/events.jsonl"
    outbox_batch: int = 50
    outbox_max_attempts: int = 5
    outbox_interval_sec: float = 1.0
    review_required_types: list = field(default_factory=lambda: [])

    @classmethod
    def load(cls, path: str | None) -> "Config":
        if not path:
            return cls()
        raw = yaml.safe_load(open(path)) or {}
        known = {f.name for f in fields(cls)}
        bad = set(raw) - known
        if bad:
            raise ValueError(f"unknown config keys: {sorted(bad)}")
        return cls(**raw)
