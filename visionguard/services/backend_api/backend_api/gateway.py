"""Business logic of the API, independent of FastAPI so it is unit-testable.
Rules enforced here: RBAC, camera scoping (404 not 403 to avoid existence leaks), actor taken from the token
(never the request body), raw storage URIs never exposed, every sensitive action audited."""
from __future__ import annotations
import time

from .audit import AuditLog
from .errors import ApiError
from .rbac import camera_allowed, can

REVIEW_ACTIONS = {"start_review", "confirm", "reject", "resolve"}
MAX_COMMENT = 1000


class RateLimiter:
    def __init__(self, per_min=120, clock=time.monotonic):
        self.rate, self.clock, self.b = per_min / 60.0, clock, {}
        self.cap = per_min

    def check(self, key):
        now = self.clock()
        tokens, last = self.b.get(key, (self.cap, now))
        tokens = min(self.cap, tokens + (now - last) * self.rate)
        if tokens < 1:
            self.b[key] = (tokens, now)
            raise ApiError(429, "rate limit exceeded")
        self.b[key] = (tokens - 1, now)


class Gateway:
    def __init__(self, vision, incidents, agent, notify, audit: AuditLog, max_scan_pages=10):
        self.vision, self.incidents, self.agent, self.notify, self.audit = vision, incidents, agent, notify, audit
        self.max_scan_pages = max_scan_pages

    # ---------- helpers
    def _need(self, p, perm):
        if not can(p.role, perm):
            self.audit.record(p.sub, p.role, "denied", perm, "forbidden")
            raise ApiError(403, "forbidden")

    def _inc(self, p, iid):
        inc = self.incidents.get(f"/incidents/{iid}")
        if not camera_allowed(p, inc.get("camera_id")):
            raise ApiError(404, "not found")
        return inc

    @staticmethod
    def _public(inc, analysis=None):
        out = {k: v for k, v in inc.items() if k not in ("evidence_uris", "thumbnail_uri", "events")}
        iid = inc["incident_id"]
        n = len(inc.get("evidence_uris") or [])
        out["evidence"] = [f"/api/incidents/{iid}/evidence/{i}" for i in range(n)]
        out["thumbnail"] = out["evidence"][0] if n else None
        if analysis is not None:
            out["analysis"] = analysis
        return out

    # ---------- cameras
    def cameras(self, p):
        self._need(p, "cameras:read")
        data = self.vision.get("/cameras")
        items = data if isinstance(data, list) else data.get("cameras", [])
        cid = lambda c: c if isinstance(c, str) else c.get("camera_id")
        return [c for c in items if camera_allowed(p, cid(c))]

    def camera_latest(self, p, camera_id):
        self._need(p, "cameras:read")
        if not camera_allowed(p, camera_id):
            raise ApiError(404, "not found")
        return self.vision.get(f"/cameras/{camera_id}/latest")

    # ---------- incidents
    def list_incidents(self, p, status=None, camera_id=None, zone=None, needs_review=None, limit=50, offset=0):
        self._need(p, "incidents:read")
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        if camera_id and not camera_allowed(p, camera_id):
            return {"total": 0, "items": [], "limit": limit, "offset": offset}
        q = dict(status=status, camera_id=camera_id, zone=zone, needs_review=needs_review)
        if p.cameras is None:
            d = self.incidents.get("/incidents", limit=limit, offset=offset, **q)
            return {"total": d["total"], "items": [self._public(i) for i in d["items"]], "limit": limit, "offset": offset}
        # Scoped user: M3 can't filter by a camera list, so scan pages and filter. Bounded by max_scan_pages.
        kept, skipped, off, page = [], 0, 0, 200
        for _ in range(self.max_scan_pages):
            d = self.incidents.get("/incidents", limit=page, offset=off, **q)
            for i in d["items"]:
                if camera_allowed(p, i["camera_id"]):
                    if skipped < offset:
                        skipped += 1
                    elif len(kept) < limit:
                        kept.append(self._public(i))
            off += page
            if off >= d["total"] or len(kept) >= limit:
                break
        return {"total": None, "items": kept, "limit": limit, "offset": offset}   # total unknown for scoped users

    def get_incident(self, p, iid):
        self._need(p, "incidents:read")
        inc = self._inc(p, iid)
        analysis = None
        if can(p.role, "analyses:read"):
            try:
                analysis = self.agent.get(f"/analyses/{iid}")
            except ApiError as e:
                analysis = {"unavailable": True} if e.status in (404, 502, 503) else None
        return self._public(inc, analysis)

    def evidence(self, p, iid, idx):
        self._need(p, "incidents:read")
        inc = self._inc(p, iid)
        uris = inc.get("evidence_uris") or []
        if not (0 <= idx < len(uris)):
            raise ApiError(404, "not found")
        self.audit.record(p.sub, p.role, "evidence.view", f"{iid}#{idx}")
        return self.incidents.get_raw("/evidence", uri=uris[idx])

    def review(self, p, iid, action, comment=None):
        self._need(p, "incidents:review")
        if action not in REVIEW_ACTIONS:
            raise ApiError(422, "invalid action")
        if comment is not None and len(comment) > MAX_COMMENT:
            raise ApiError(422, "comment too long")
        self._inc(p, iid)      # scope check before any mutation
        try:
            res = self.incidents.post(f"/incidents/{iid}/review", {"action": action, "actor": p.sub, "comment": comment})
        except ApiError as e:
            self.audit.record(p.sub, p.role, f"incident.{action}", iid, f"failed:{e.status}")
            raise
        self.audit.record(p.sub, p.role, f"incident.{action}", iid, "ok", comment or "")
        return self._public(res)

    # ---------- admin
    def labels(self, p):
        self._need(p, "labels:read")
        self.audit.record(p.sub, p.role, "labels.export")
        return self.incidents.get("/labels")

    def notifications(self, p, status=None, limit=100):
        self._need(p, "notifications:read")
        return self.notify.get("/notifications", status=status, limit=min(limit, 500))

    def requeue_notifications(self, p):
        self._need(p, "notifications:manage")
        r = self.notify.post("/notifications/requeue-dead")
        self.audit.record(p.sub, p.role, "notifications.requeue", "", "ok", str(r))
        return r

    def audit_log(self, p, limit=100, actor=None):
        self._need(p, "audit:read")
        return {"chain_valid": self.audit.verify(), "items": self.audit.list(min(limit, 500), actor)}
