"""Optional HTTP service.  CONFIG_PATH=configs/rules.example.yaml uvicorn rule_engine.app:app --port 8002

POST /evaluate        body = M1 TrackResult -> {"events": [...]}   (stateful per camera/track)
GET  /rules           current config summary
PUT  /rules           body = full YAML text (validated; hot reload; written to CONFIG_PATH)  -> audit log line
GET  /events          recent events held in memory
GET  /observations/{camera_id}   last per-person observations (why a violation did / didn't fire)
GET  /health, /stats
"""
from __future__ import annotations

import logging
import os
from collections import deque

import yaml
from fastapi import Body, FastAPI, HTTPException

from .config import ConfigError, build_config, load_config
from .engine import RuleEngine

log = logging.getLogger("rule_engine.audit")


def create_app(engine: RuleEngine | None = None, config_path: str | None = None) -> FastAPI:
    app = FastAPI(title="VisionGuard Rule Engine", version="0.1.0")
    path = config_path or os.environ.get("CONFIG_PATH", "configs/rules.example.yaml")
    eng = engine or RuleEngine(load_config(path))
    try:    # M9: /metrics + request ids + JSON access logs. Optional so the service still runs without the ops package.
        from vg_obs import instrument
        _reg = instrument(app, "rule_engine")
        from vg_obs.collectors import register_rule_engine
        register_rule_engine(eng, _reg)
    except ImportError:
        pass
    recent: deque = deque(maxlen=500)

    @app.get("/health")
    def health():
        return {"status": "ok", "config_version": eng.config.version}

    @app.get("/stats")
    def stats():
        return eng.stats()

    @app.post("/evaluate")
    def evaluate(track_result: dict = Body(...)):
        try:
            events = eng.process(track_result)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(422, f"invalid TrackResult: {e!r}")
        recent.extend(events)
        return {"events": events}

    @app.get("/events")
    def events(limit: int = 100):
        return list(recent)[-limit:]

    @app.get("/observations/{camera_id}")
    def observations(camera_id: str):
        return eng.last_observations.get(camera_id, [])

    @app.get("/rules")
    def rules():
        return {"version": eng.config.version,
                "zones": [{"zone_id": z.zone_id, "camera_id": z.camera_id, "rules": [r.id for r in z.rules]}
                          for z in eng.config.zones]}

    @app.put("/rules")
    def put_rules(yaml_text: str = Body(..., media_type="text/plain")):
        try:
            cfg = build_config(yaml.safe_load(yaml_text))
        except (ConfigError, yaml.YAMLError) as e:
            raise HTTPException(422, str(e))
        eng.reload(cfg)
        with open(path, "w") as f:
            f.write(yaml_text)
        log.warning("AUDIT rules_updated version=%s zones=%d", cfg.version, len(cfg.zones))
        return {"status": "reloaded", "version": cfg.version}

    return app


app = create_app() if os.environ.get("CONFIG_PATH") else None
