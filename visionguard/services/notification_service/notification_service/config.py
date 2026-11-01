from __future__ import annotations
from dataclasses import dataclass, field
import yaml

SEV = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RANK = {s: i for i, s in enumerate(SEV)}
RANK["UNRATED"] = 2          # unrated incidents are treated as HIGH for routing: fail loud, not silent


@dataclass
class Config:
    channels: dict = field(default_factory=lambda: {"console": {"type": "console"}})
    routes: list = field(default_factory=lambda: [{"name": "all", "match": {}, "channels": ["console"]}])
    escalations: list = field(default_factory=list)
    dashboard_url: str = ""
    db_path: str = ":memory:"
    analysis_wait_sec: float = 30      # send as UNRATED if M4 hasn't analysed by then
    max_attempts: int = 5
    backoff_base_sec: float = 5
    rate_limit_per_min: int = 20       # per channel; excess is deferred, not dropped

    @classmethod
    def load(cls, path=None) -> "Config":
        if not path:
            return cls()
        raw = yaml.safe_load(open(path)) or {}
        c = cls(**raw)
        c.validate()
        return c

    def validate(self):
        for r in self.routes + self.escalations:
            for ch in r["channels"]:
                if ch not in self.channels:
                    raise ValueError(f"rule {r.get('name')!r} uses unknown channel {ch!r}")
            for s in (r.get("match", {}).get("severity") or []):
                if s not in RANK:
                    raise ValueError(f"unknown severity {s!r}")
        for e in self.escalations:
            if "after_min" not in e:
                raise ValueError(f"escalation {e.get('name')!r} needs after_min")
