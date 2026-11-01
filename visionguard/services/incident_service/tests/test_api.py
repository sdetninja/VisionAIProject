import pytest
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from helpers import ev, fake_fetch
from incident_service.app import create_app
from incident_service.bus import MemoryBus
from incident_service.config import Config
from incident_service.factory import build


def test_api_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("INCIDENT_OUTBOX", "0")
    svc, pub = build(Config(db_path=":memory:", evidence_dir=str(tmp_path)), fetch=fake_fetch, bus=MemoryBus())
    c = TestClient(create_app(service=svc, publisher=pub))
    r = c.post("/events", json=ev()).json()
    iid = r["incident_id"]
    assert c.get("/incidents").json()["total"] == 1
    got = c.get(f"/incidents/{iid}").json()
    assert c.get("/evidence", params={"uri": got["thumbnail_uri"]}).status_code == 200
    assert c.post(f"/incidents/{iid}/review", json={"action": "confirm", "actor": "a"}).json()["status"] == "CONFIRMED"
    assert c.post(f"/incidents/{iid}/review", json={"action": "start_review", "actor": "a"}).status_code == 409
    assert c.get("/incidents/NOPE").status_code == 404
    assert c.post("/events", json={"x": 1}).status_code == 422
