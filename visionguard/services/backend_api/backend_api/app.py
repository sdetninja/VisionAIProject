"""uvicorn backend_api.app:create_app --factory --port 8000
Env: VG_JWT_SECRET (>=32 chars) [+ see config.py]. For a real IdP use OidcVerifier instead of HS256Verifier."""
from __future__ import annotations
import os


def create_app(settings=None, verifier=None, gateway=None):
    from fastapi import Depends, FastAPI, Header, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    from typing import Optional

    from .audit import AuditLog
    from .auth import HS256Verifier
    from .config import Settings
    from .errors import ApiError
    from .gateway import Gateway, RateLimiter
    from .upstream import HttpUpstream

    s = settings or Settings.from_env()
    if verifier is None:
        secret = os.environ.get(s.jwt_secret_env)
        if not secret:
            raise RuntimeError(f"{s.jwt_secret_env} not set")
        verifier = HS256Verifier(secret, s.audience, s.issuer)
    if gateway is None:
        os.makedirs(os.path.dirname(s.audit_db) or ".", exist_ok=True)
        gateway = Gateway(HttpUpstream(s.vision_url), HttpUpstream(s.incident_url), HttpUpstream(s.agent_url),
                          HttpUpstream(s.notify_url), AuditLog(s.audit_db))
    limiter = RateLimiter(s.rate_limit_per_min)

    app = FastAPI(title="VisionGuard API", docs_url=None, redoc_url=None)   # docs off by default in prod
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"], allow_credentials=False)
    try:    # M9: request ids + RED metrics. /metrics is served here too, so set VG_METRICS_TOKEN when this port is reachable by users.
        from vg_obs import instrument
        instrument(app, "gateway")
    except ImportError:
        pass

    @app.middleware("http")
    async def sec_headers(request: Request, call_next):
        r = await call_next(request)
        r.headers.update({"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "X-Frame-Options": "DENY",
                          "Referrer-Policy": "no-referrer"})
        return r

    @app.exception_handler(ApiError)
    async def _err(_, e: ApiError):
        return JSONResponse({"detail": e.detail}, status_code=e.status, headers={"WWW-Authenticate": "Bearer"} if e.status == 401 else None)

    def user(authorization: Optional[str] = Header(None)):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "missing bearer token")
        p = verifier.verify(authorization[7:].strip())
        limiter.check(p.sub)
        return p

    class Review(BaseModel):
        action: str
        comment: Optional[str] = None      # NOTE: no actor field - identity comes from the token

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/me")
    def me(p=Depends(user)):
        return {"sub": p.sub, "role": p.role, "cameras": p.cameras}

    @app.get("/api/cameras")
    def cameras(p=Depends(user)): return gateway.cameras(p)

    @app.get("/api/cameras/{cid}/latest")
    def latest(cid: str, p=Depends(user)): return gateway.camera_latest(p, cid)

    @app.get("/api/incidents")
    def incidents(status: Optional[str] = None, camera_id: Optional[str] = None, zone: Optional[str] = None,
                  needs_review: Optional[bool] = None, limit: int = 50, offset: int = 0, p=Depends(user)):
        return gateway.list_incidents(p, status, camera_id, zone, needs_review, limit, offset)

    @app.get("/api/incidents/{iid}")
    def incident(iid: str, p=Depends(user)): return gateway.get_incident(p, iid)

    @app.get("/api/incidents/{iid}/evidence/{idx}")
    def evidence(iid: str, idx: int, p=Depends(user)):
        return Response(gateway.evidence(p, iid, idx), media_type="image/jpeg")

    @app.post("/api/incidents/{iid}/review")
    def review(iid: str, body: Review, p=Depends(user)): return gateway.review(p, iid, body.action, body.comment)

    @app.get("/api/labels")
    def labels(p=Depends(user)): return gateway.labels(p)

    @app.get("/api/notifications")
    def notifications(status: Optional[str] = None, limit: int = 100, p=Depends(user)): return gateway.notifications(p, status, limit)

    @app.post("/api/notifications/requeue-dead")
    def requeue(p=Depends(user)): return gateway.requeue_notifications(p)

    @app.get("/api/audit")
    def audit(limit: int = 100, actor: Optional[str] = None, p=Depends(user)): return gateway.audit_log(p, limit, actor)

    return app
