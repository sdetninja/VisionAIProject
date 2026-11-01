from __future__ import annotations
from .bus import JsonlBus, MemoryBus
from .config import Config
from .db import SqliteRepository
from .evidence import LocalEvidenceStore, http_fetcher
from .outbox import OutboxPublisher
from .service import IncidentService


def build(cfg: Config, fetch=None, bus=None):
    repo = SqliteRepository(cfg.db_path)
    store = LocalEvidenceStore(cfg.evidence_dir)
    if fetch is None and cfg.frame_source_url:
        fetch = http_fetcher(cfg.frame_source_url)
    bus = bus or (JsonlBus(cfg.bus_path) if cfg.bus == "jsonl" else MemoryBus())
    svc = IncidentService(cfg, repo, store, fetch)
    pub = OutboxPublisher(repo, bus, cfg.outbox_batch, cfg.outbox_max_attempts, cfg.outbox_interval_sec)
    return svc, pub
