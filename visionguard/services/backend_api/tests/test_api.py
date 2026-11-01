import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from backend_api.app import create_app
from backend_api.config import Settings
from test_backend import make, verifier, claims


def test_http_flow():
    g, inc, _ = make()
    c = TestClient(create_app(Settings(), verifier(), g))
    v = verifier()
    h = lambda **k: {"Authorization": "Bearer " + v.sign(claims(**k))}
    assert c.get("/health").status_code == 200
    assert c.get("/api/incidents").status_code == 401
    assert c.get("/api/incidents", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert c.get("/api/incidents", headers=h()).status_code == 200
    assert c.post("/api/incidents/INC-1/review", json={"action": "confirm", "actor": "spoof"}, headers=h()).status_code == 200
    assert inc.posts[-1][1]["actor"] == "alice"
    assert c.get("/api/labels", headers=h()).status_code == 403
    r = c.get("/api/incidents/INC-1/evidence/0", headers=h())
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["x-content-type-options"] == "nosniff"
