import json
import pytest
from backend_api.audit import AuditLog
from backend_api.auth import HS256Verifier, Principal
from backend_api.errors import ApiError
from backend_api.gateway import Gateway, RateLimiter

SECRET = "x" * 40
NOW = 1_000_000.0


def verifier():
    return HS256Verifier(SECRET, "api", "vg", clock=lambda: NOW)


def claims(**kw):
    c = {"sub": "alice", "role": "reviewer", "aud": "api", "iss": "vg", "exp": NOW + 600}
    c.update(kw)
    return c


def status_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except ApiError as e:
        return e.status
    return 200


# ---- fakes
INCS = {
    "INC-1": {"incident_id": "INC-1", "camera_id": "CAM_001", "status": "OPEN", "evidence_uris": ["file://CAM_001/a.jpg", "file://CAM_001/b.jpg"], "thumbnail_uri": "file://CAM_001/a.jpg", "events": [{"x": 1}]},
    "INC-2": {"incident_id": "INC-2", "camera_id": "CAM_002", "status": "OPEN", "evidence_uris": [], "thumbnail_uri": None},
    "INC-3": {"incident_id": "INC-3", "camera_id": "CAM_001", "status": "OPEN", "evidence_uris": []},
}


class FakeIncidents:
    def __init__(self): self.posts = []
    def get(self, path, **q):
        if path == "/incidents":
            items = [i for i in INCS.values() if not q.get("camera_id") or i["camera_id"] == q["camera_id"]]
            return {"total": len(items), "items": items[q.get("offset", 0): q.get("offset", 0) + q.get("limit", 50)]}
        if path.startswith("/incidents/"):
            iid = path.split("/")[2]
            if iid not in INCS: raise ApiError(404, "not found")
            return dict(INCS[iid])
        if path == "/labels": return [{"label": "true_positive"}]
        raise AssertionError(path)
    def get_raw(self, path, **q): return b"\xff\xd8jpeg:" + q["uri"].encode()
    def post(self, path, body=None, **q):
        self.posts.append((path, body))
        if body["action"] == "resolve": raise ApiError(409, "upstream: not allowed")
        return {**INCS["INC-1"], "status": "CONFIRMED"}


class FakeAgent:
    def __init__(self): self.down = False
    def get(self, path, **q):
        if self.down: raise ApiError(503, "x")
        return {"severity": "HIGH"}


class FakeVision:
    def get(self, path, **q):
        return [{"camera_id": "CAM_001"}, {"camera_id": "CAM_002"}] if path == "/cameras" else {"cam": path}


class FakeNotify:
    def get(self, path, **q): return [{"id": 1}]
    def post(self, path, body=None, **q): return {"requeued": 2}


def make():
    inc, agent = FakeIncidents(), FakeAgent()
    return Gateway(FakeVision(), inc, agent, FakeNotify(), AuditLog(":memory:", clock=lambda: NOW)), inc, agent


P = lambda role="reviewer", cams=None, sub="alice": Principal(sub, role, cams)


# ---- auth
def test_jwt_valid():
    v = verifier()
    p = v.verify(v.sign(claims(cameras=["CAM_001"])))
    assert (p.sub, p.role, p.cameras) == ("alice", "reviewer", ["CAM_001"])


def test_jwt_rejections():
    v = verifier()
    good = v.sign(claims())
    assert status_of(v.verify, good[:-3] + "AAA") == 401                      # bad signature
    assert status_of(v.verify, v.sign(claims(exp=NOW - 1000))) == 401        # expired
    assert status_of(v.verify, v.sign(claims(aud="other"))) == 401
    assert status_of(v.verify, v.sign(claims(iss="evil"))) == 401
    assert status_of(v.verify, v.sign(claims(role="root"))) == 403           # unknown role
    assert status_of(v.verify, v.sign(claims(cameras="CAM_001"))) == 401     # bad scope claim
    assert status_of(v.verify, "garbage") == 401
    import base64
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    assert status_of(v.verify, f"{b({'alg': 'none'})}.{b(claims())}.") == 401  # alg=none attack
    with pytest.raises(ValueError):
        HS256Verifier("short", "a", "b")


# ---- RBAC
def test_viewer_cannot_review_or_admin():
    g, inc, _ = make()
    assert status_of(g.review, P("viewer"), "INC-1", "confirm") == 403
    assert status_of(g.labels, P("reviewer")) == 403
    assert status_of(g.requeue_notifications, P("reviewer")) == 403
    assert status_of(g.audit_log, P("viewer")) == 403
    assert inc.posts == []
    assert g.labels(P("admin")) and g.audit_log(P("admin"))["chain_valid"]


def test_denied_attempts_are_audited():
    g, *_ = make()
    status_of(g.labels, P("viewer", sub="mallory"))
    assert g.audit.list()[0]["action"] == "denied" and g.audit.list()[0]["actor"] == "mallory"


# ---- review
def test_actor_comes_from_token_and_is_audited():
    g, inc, _ = make()
    r = g.review(P(sub="alice"), "INC-1", "confirm", "looks real")
    assert inc.posts[0][1]["actor"] == "alice" and r["status"] == "CONFIRMED"
    assert g.audit.list()[0]["action"] == "incident.confirm"


def test_review_validation_and_upstream_conflict():
    g, inc, _ = make()
    assert status_of(g.review, P(), "INC-1", "delete") == 422
    assert status_of(g.review, P(), "INC-1", "confirm", "x" * 2000) == 422
    assert status_of(g.review, P(), "INC-1", "resolve") == 409
    assert "failed:409" in g.audit.list()[0]["outcome"]
    assert status_of(g.review, P(), "NOPE", "confirm") == 404


# ---- scoping
def test_camera_scope_hides_other_cameras():
    g, inc, _ = make()
    scoped = P("reviewer", ["CAM_001"])
    assert [c["camera_id"] for c in g.cameras(scoped)] == ["CAM_001"]
    assert status_of(g.get_incident, scoped, "INC-2") == 404             # not 403: no existence leak
    assert status_of(g.review, scoped, "INC-2", "confirm") == 404 and inc.posts == []
    assert status_of(g.evidence, scoped, "INC-2", 0) == 404
    assert status_of(g.camera_latest, scoped, "CAM_002") == 404
    lst = g.list_incidents(scoped)
    assert {i["incident_id"] for i in lst["items"]} == {"INC-1", "INC-3"}
    assert g.list_incidents(scoped, camera_id="CAM_002")["items"] == []


def test_scoped_pagination_offset():
    g, *_ = make()
    s = P("viewer", ["CAM_001"])
    assert [i["incident_id"] for i in g.list_incidents(s, limit=1)["items"]] == ["INC-1"]
    assert [i["incident_id"] for i in g.list_incidents(s, limit=1, offset=1)["items"]] == ["INC-3"]


# ---- data exposure
def test_raw_storage_uris_never_exposed():
    g, *_ = make()
    r = g.get_incident(P("viewer"), "INC-1")
    blob = json.dumps(r)
    assert "file://" not in blob and "events" not in r
    assert r["evidence"] == ["/api/incidents/INC-1/evidence/0", "/api/incidents/INC-1/evidence/1"]
    assert r["analysis"]["severity"] == "HIGH"
    assert "file://" not in json.dumps(g.list_incidents(P("viewer")))


def test_evidence_index_bounds_and_audit():
    g, *_ = make()
    assert g.evidence(P("viewer"), "INC-1", 1).endswith(b"b.jpg")
    assert status_of(g.evidence, P("viewer"), "INC-1", 2) == 404
    assert status_of(g.evidence, P("viewer"), "INC-1", -1) == 404
    assert g.audit.list()[0]["action"] == "evidence.view"


def test_agent_down_degrades_gracefully():
    g, _, agent = make()
    agent.down = True
    assert g.get_incident(P("viewer"), "INC-1")["analysis"] == {"unavailable": True}


# ---- audit / rate limit
def test_audit_chain_detects_tampering():
    g, *_ = make()
    for i in range(3): g.audit.record("a", "admin", f"act{i}")
    assert g.audit.verify()
    g.audit.c.execute("UPDATE audit SET actor='eve' WHERE id=2")
    assert not g.audit.verify()


def test_rate_limiter():
    t = [0.0]
    rl = RateLimiter(per_min=3, clock=lambda: t[0])
    for _ in range(3): rl.check("u")
    assert status_of(rl.check, "u") == 429
    rl.check("other")
    t[0] += 20
    rl.check("u")
