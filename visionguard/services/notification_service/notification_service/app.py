"""uvicorn notification_service.app:create_app --factory --port 8005. Fronted by M6 (no auth here).
Run a background loop calling service.tick() every few seconds."""
import os
import threading
from typing import Optional


def create_app(service=None, tick_sec=5.0):
    from fastapi import FastAPI
    from .config import Config
    from .factory import build
    service = service or build(Config.load(os.environ.get("NOTIFY_CONFIG")))
    app = FastAPI(title="VisionGuard Notification Service")
    try:    # M9: /metrics + request ids + JSON access logs. Optional so the service still runs without the ops package.
        from vg_obs import instrument
        _reg = instrument(app, "notification")
        from vg_obs.collectors import register_notification
        register_notification(service, _reg)
    except ImportError:
        pass
    stop = threading.Event()

    @app.on_event("startup")
    def _s():
        if tick_sec:
            threading.Thread(target=lambda: [service.tick() for _ in iter(lambda: not stop.wait(tick_sec), False)], daemon=True).start()

    @app.on_event("shutdown")
    def _x():
        stop.set()

    @app.get("/health")
    def health():
        return {"status": "ok", **service.stats()}

    @app.post("/messages")
    def ingest(msg: dict):
        service.handle_message(msg)
        return {"ok": True}

    @app.get("/notifications")
    def lst(status: Optional[str] = None, limit: int = 100):
        return service.store.list(status, min(limit, 500))

    @app.post("/notifications/requeue-dead")
    def requeue():
        return {"requeued": service.store.requeue_dead(service.clock())}
    return app
