"""FastAPI app. Run: uvicorn incident_service.app:create_app --factory --port 8003
Auth is NOT handled here; M6 (Backend API) fronts this with RBAC/OIDC. Do not expose directly."""
from __future__ import annotations
import os
from typing import Optional

from . import models as M


def create_app(cfg=None, service=None, publisher=None):
    from fastapi import FastAPI, HTTPException, Response
    from pydantic import BaseModel
    from .config import Config
    from .factory import build

    if service is None:
        service, publisher = build(cfg or Config.load(os.environ.get("INCIDENT_CONFIG")))
    app = FastAPI(title="VisionGuard Incident Service")
    try:    # M9: /metrics + request ids + JSON access logs. Optional so the service still runs without the ops package.
        from vg_obs import instrument
        _reg = instrument(app, "incident")
        from vg_obs.collectors import register_incident
        register_incident(service, _reg)
    except ImportError:
        pass

    class Review(BaseModel):
        action: str
        actor: str
        comment: Optional[str] = None

    @app.on_event("startup")
    def _start():
        if publisher and os.environ.get("INCIDENT_OUTBOX", "1") == "1":
            publisher.start()

    @app.on_event("shutdown")
    def _stop():
        if publisher:
            publisher.stop()

    @app.get("/health")
    def health():
        return {"status": "ok", **service.stats()}

    @app.post("/events")
    def post_event(ev: dict):
        try:
            return service.ingest(ev)
        except M.InvalidEvent as e:
            raise HTTPException(422, str(e))

    @app.get("/incidents")
    def list_incidents(status: Optional[str] = None, camera_id: Optional[str] = None, zone: Optional[str] = None,
                       needs_review: Optional[bool] = None, limit: int = 50, offset: int = 0):
        items, total = service.list(status=status, camera_id=camera_id, zone=zone, needs_review=needs_review,
                                    limit=min(limit, 200), offset=offset)
        return {"total": total, "items": items}

    @app.get("/incidents/{iid}")
    def get_incident(iid: str):
        try:
            return service.get(iid)
        except M.NotFound:
            raise HTTPException(404, "incident not found")

    @app.post("/incidents/{iid}/review")
    def review(iid: str, body: Review):
        try:
            return service.transition(iid, body.action, body.actor, body.comment)
        except M.NotFound:
            raise HTTPException(404, "incident not found")
        except M.InvalidTransition as e:
            raise HTTPException(409, str(e))

    @app.get("/evidence")
    def evidence(uri: str):
        try:
            return Response(service.store.get(uri), media_type="image/jpeg")
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "evidence not found")

    @app.get("/labels")
    def labels():
        return service.repo.labels()

    @app.post("/outbox/requeue-dead")
    def requeue():
        return {"requeued": service.repo.requeue_dead()}

    return app
