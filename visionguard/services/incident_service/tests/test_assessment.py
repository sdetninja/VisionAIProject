"""v3: unannotated evidence copies, the assessments table, verdict copy-back and the priority sort."""
import sqlite3

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from helpers import ev, fake_fetch

from incident_service import models as M
from incident_service.app import create_app
from incident_service.config import Config
from incident_service.db import SqliteRepository
from incident_service.evidence import LocalEvidenceStore
from incident_service.service import IncidentService


@pytest.fixture
def svc(tmp_path):
    cfg = Config(evidence_dir=str(tmp_path / "ev"))
    return IncidentService(cfg, SqliteRepository(":memory:"), LocalEvidenceStore(cfg.evidence_dir), fake_fetch)


def A(version, verdict="CONFIRMED", **kw):
    return {"incident_version": version, "verdict": verdict, "judge_mode": "vit", "severity": "HIGH", "risk_score": 70,
            "vit": {"helmet": {"ensemble": 0.9}}, "risk_factors": ["x"], "recommended_actions": ["a"],
            "citations": [{"id": "OSHA"}], "grounded": True, **kw}


def test_raw_evidence_has_no_drawn_box(svc):
    iid = svc.ingest(ev())["incident_id"]
    inc = svc.get(iid)
    assert len(inc["raw_evidence"]) == 3 and inc["raw_evidence"][0]["frame_id"] == 1
    assert inc["raw_evidence"][0]["event_id"] == inc["events"][0]["event_id"]
    dec = lambda u: cv2.imdecode(np.frombuffer(svc.store.get(u), np.uint8), cv2.IMREAD_COLOR)
    raw, drawn = dec(inc["raw_evidence"][0]["uri"]), dec(inc["evidence_uris"][0])
    red = lambda im: int(((im[:, :, 2] > 200) & (im[:, :, 1] < 60)).sum())
    assert red(raw) == 0 and red(drawn) > 50          # the red rectangle is only on the reviewer copy


def test_raw_evidence_can_be_switched_off(tmp_path):
    cfg = Config(evidence_dir=str(tmp_path / "ev"), keep_raw_evidence=False)
    s = IncidentService(cfg, SqliteRepository(":memory:"), LocalEvidenceStore(cfg.evidence_dir), fake_fetch)
    assert s.get(s.ingest(ev())["incident_id"])["raw_evidence"] == []


def test_assessment_roundtrip_and_version_guard(svc):
    iid = svc.ingest(ev())["incident_id"]
    v1 = svc.get(iid)["updated_at"]
    assert svc.get(iid)["assessment"] is None
    assert svc.record_assessment(iid, A(v1))["current"]
    inc = svc.get(iid)
    assert inc["judge_verdict"] == "CONFIRMED" and inc["severity"] == "HIGH" and inc["risk_score"] == 70
    assert inc["updated_at"] == v1                       # recording an assessment must not create a new version
    a = inc["assessment"]
    assert a["vit"] == {"helmet": {"ensemble": 0.9}} and a["grounded"] is True and a["citations"] == [{"id": "OSHA"}]
    # a newer version, then a late answer for the old one: the incident keeps the newer verdict
    assert svc.record_assessment(iid, A("9999-01-01T00:00:00.000Z", "LIKELY_FALSE", risk_score=40))["current"]
    assert not svc.record_assessment(iid, A(v1, "UNSURE"))["current"]
    inc = svc.get(iid)
    assert inc["judge_verdict"] == "LIKELY_FALSE" and inc["assessment"]["verdict"] == "LIKELY_FALSE"
    # same version again replaces (idempotent re-run), never duplicates
    svc.record_assessment(iid, A("9999-01-01T00:00:00.000Z", "UNSURE"))
    assert svc.repo._q("SELECT COUNT(*) n FROM assessments").fetchone()["n"] == 2


def test_assessment_validation(svc):
    iid = svc.ingest(ev())["incident_id"]
    with pytest.raises(M.InvalidAssessment):
        svc.record_assessment(iid, A("v", "MAYBE"))
    with pytest.raises(M.InvalidAssessment):
        svc.record_assessment(iid, {"verdict": "UNSURE", "judge_mode": "vit"})
    with pytest.raises(M.NotFound):
        svc.record_assessment("INC-999999", A("v"))


def test_priority_sort_and_verdict_filter(svc):
    ppe_false = svc.ingest(ev(track_id="T1"))["incident_id"]
    ppe_ok = svc.ingest(ev(track_id="T2"))["incident_id"]
    fire = svc.ingest(ev(track_id=None, event_type="HAZARD_DETECTED", hazard_class="fire", missing_ppe=[]))["incident_id"]
    unassessed = svc.ingest(ev(track_id="T3"))["incident_id"]
    v = lambda i: svc.get(i)["updated_at"]
    svc.record_assessment(ppe_false, A(v(ppe_false), "LIKELY_FALSE", risk_score=90))
    svc.record_assessment(ppe_ok, A(v(ppe_ok), "UNSURE", risk_score=50))
    svc.record_assessment(fire, A(v(fire), "LIKELY_FALSE", risk_score=60))   # fire is never moved down
    order = [i["incident_id"] for i in svc.list(sort="priority")[0]]
    assert order == [fire, ppe_ok, unassessed, ppe_false]
    assert [i["incident_id"] for i in svc.list(judge_verdict="LIKELY_FALSE")[0]] == [fire, ppe_false]
    with pytest.raises(M.IncidentError):
        svc.list(sort="random")


def test_old_database_is_migrated(tmp_path):
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT UNIQUE, event_type TEXT NOT NULL,"
              " camera_id TEXT NOT NULL, zone TEXT NOT NULL, track_id TEXT, hazard_class TEXT, missing_ppe TEXT NOT NULL DEFAULT '[]',"
              " rule_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0,"
              " confidence REAL NOT NULL DEFAULT 0, first_event_time TEXT NOT NULL, last_event_time TEXT NOT NULL,"
              " last_event_ts REAL NOT NULL, event_count INTEGER NOT NULL DEFAULT 1, evidence_uris TEXT NOT NULL DEFAULT '[]',"
              " thumbnail_uri TEXT, evidence_status TEXT NOT NULL DEFAULT 'none', created_at TEXT NOT NULL,"
              " updated_at TEXT NOT NULL, reviewed_by TEXT, review_comment TEXT)")
    c.execute("INSERT INTO incidents(incident_id,event_type,camera_id,zone,status,first_event_time,last_event_time,"
              "last_event_ts,created_at,updated_at) VALUES('INC-000001','PPE_VIOLATION','C','Z','OPEN','t','t',0,'t','t')")
    c.commit(); c.close()
    repo = SqliteRepository(p)
    inc = repo.get_incident("INC-000001")
    assert inc["raw_evidence"] == [] and inc["judge_verdict"] is None


def test_assessment_api(svc):
    c = TestClient(create_app(service=svc, publisher=None))
    iid = svc.ingest(ev())["incident_id"]
    v = svc.get(iid)["updated_at"]
    assert c.post(f"/incidents/{iid}/assessment", json=A(v)).json()["current"] is True
    assert c.post(f"/incidents/{iid}/assessment", json=A(v, "NOPE")).status_code == 422
    assert c.post("/incidents/INC-999999/assessment", json=A(v)).status_code == 404
    assert c.get(f"/incidents/{iid}").json()["assessment"]["verdict"] == "CONFIRMED"
    assert c.get("/incidents", params={"sort": "priority"}).json()["items"][0]["judge_verdict"] == "CONFIRMED"
    assert c.get("/incidents", params={"sort": "bad"}).status_code == 422
