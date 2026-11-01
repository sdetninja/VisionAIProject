from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    jwt_secret_env: str = "VG_JWT_SECRET"
    audience: str = "visionguard-api"
    issuer: str = "visionguard"
    vision_url: str = "http://localhost:8001"
    incident_url: str = "http://localhost:8003"
    agent_url: str = "http://localhost:8004"
    notify_url: str = "http://localhost:8005"
    audit_db: str = "data/audit.db"
    cors_origins: list = field(default_factory=lambda: ["http://localhost:3000"])
    rate_limit_per_min: int = 120

    @classmethod
    def from_env(cls):
        e, s = os.environ.get, cls()
        for f, k in (("audience", "VG_AUDIENCE"), ("issuer", "VG_ISSUER"), ("vision_url", "VG_VISION_URL"),
                     ("incident_url", "VG_INCIDENT_URL"), ("agent_url", "VG_AGENT_URL"), ("notify_url", "VG_NOTIFY_URL"),
                     ("audit_db", "VG_AUDIT_DB")):
            if e(k):
                setattr(s, f, e(k))
        if e("VG_CORS_ORIGINS"):
            s.cors_origins = [o.strip() for o in e("VG_CORS_ORIGINS").split(",") if o.strip()]
            if "*" in s.cors_origins:
                raise ValueError("wildcard CORS is not allowed")
        return s
