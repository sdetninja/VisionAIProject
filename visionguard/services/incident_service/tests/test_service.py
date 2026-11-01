import json
import pytest
from helpers import ev, fake_fetch
from incident_service import models as M
from incident_service.bus import MemoryBus
from incident_service.config import Config
from incident_service.db import SqliteRepository
from incident_service.evidence import LocalEvidenceStore
from incident_service.outbox import OutboxPublisher
from incident_service.service import IncidentService


@pytest.fixture
def env(tmp_path):
    cfg = Config(evidence_dir=str(tmp_path / "ev"), merge_window_sec=10)
    repo = SqliteRepository(":memory:")
    svc = IncidentService(cfg, repo, LocalEvidenceStore(cfg.evidence_dir), fake_fetch)
    bus = MemoryBus()
    return svc, repo, bus, OutboxPublisher(repo, bus, max_attempts=3)


def test_create_and_duplicate(env):
    svc, repo, *_ = env
    e = ev()
    r1 = svc.ingest(e)
    r2 = svc.ingest(e)
    assert r1["action"] == "created" and r2 == {"incident_id": r1["incident_id"], "action": "duplicate"}
    inc = svc.get(r1["incident_id"])
    assert inc["status"] == "OPEN" and inc["event_count"] == 1 and len(inc["evidence_uris"]) == 3
    assert svc.store.get(inc["thumbnail_uri"])[:2] == b"\xff\xd8"


def test_merge_same_track_within_window(env):
    svc, *_ = env
    a = svc.ingest(ev(rule_id="PPE_HELMET", missing_ppe=["helmet"]))
    b = svc.ingest(ev(rule_id="PPE_VEST", missing_ppe=["vest"], timestamp="2026-01-01T10:00:05.000Z", needs_review=True))
    assert b["action"] == "merged" and b["incident_id"] == a["incident_id"]
    inc = svc.get(a["incident_id"])
    assert inc["missing_ppe"] == ["helmet", "vest"] and inc["rule_ids"] == ["PPE_HELMET", "PPE_VEST"]
    assert inc["needs_review"] and inc["event_count"] == 2


def test_no_merge_outside_window_or_other_track(env):
    svc, *_ = env
    a = svc.ingest(ev())
    late = svc.ingest(ev(timestamp="2026-01-01T10:05:00.000Z"))
    other = svc.ingest(ev(track_id="CAM_001-T002"))
    assert len({a["incident_id"], late["incident_id"], other["incident_id"]}) == 3


def test_missing_frame_is_tolerated(env):
    svc, *_ = env
    r = svc.ingest(ev(evidence_frames=[99]))
    inc = svc.get(r["incident_id"])
    assert inc["evidence_status"] == "missing" and inc["evidence_uris"] == []


def test_invalid_event_rejected(env):
    svc, *_ = env
    with pytest.raises(M.InvalidEvent):
        svc.ingest({"event_id": "x"})
    with pytest.raises(M.InvalidEvent):
        svc.ingest(ev(timestamp="garbage"))
    assert svc.counters["rejected"] == 2


def test_lifecycle_and_labels(env):
    svc, *_ = env
    iid = svc.ingest(ev())["incident_id"]
    svc.transition(iid, "start_review", "alice")
    assert svc.get(iid)["status"] == "UNDER_REVIEW"
    svc.transition(iid, "reject", "alice", "welding glare")
    assert svc.get(iid)["status"] == "FALSE_POSITIVE"
    labels = svc.repo.labels()
    assert len(labels) == 1 and labels[0]["label"] == "false_positive"
    with pytest.raises(M.InvalidTransition):
        svc.transition(iid, "resolve", "alice")
    with pytest.raises(M.NotFound):
        svc.transition("INC-999999", "confirm", "alice")
    assert [h["to_status"] for h in svc.get(iid)["history"]] == ["OPEN", "UNDER_REVIEW", "FALSE_POSITIVE"]


def test_closed_incident_not_merged(env):
    svc, *_ = env
    a = svc.ingest(ev())["incident_id"]
    svc.transition(a, "confirm", "bob")
    svc.transition(a, "resolve", "bob")
    b = svc.ingest(ev(timestamp="2026-01-01T10:00:04.000Z"))
    assert b["action"] == "created" and b["incident_id"] != a


def test_outbox_publish_retry_and_dead_letter(env):
    svc, repo, bus, pub = env
    svc.ingest(ev())
    bus.fail = True
    for _ in range(3):
        assert pub.run_once() == 0
    st = repo.outbox_stats()
    assert st["dead"] == 1 and st["pending"] == 0 and bus.messages == []
    bus.fail = False
    assert repo.requeue_dead() == 1
    assert pub.run_once() == 1
    topic, key, payload = bus.messages[0]
    assert topic == M.TOPIC_CREATED and payload["incident"]["incident_id"] == key
    assert pub.run_once() == 0   # nothing re-sent


def test_outbox_events_per_action(env):
    svc, repo, bus, pub = env
    a = svc.ingest(ev())["incident_id"]
    svc.ingest(ev(timestamp="2026-01-01T10:00:03.000Z"))
    svc.transition(a, "confirm", "bob")
    pub.run_once()
    assert [m[0] for m in bus.messages] == [M.TOPIC_CREATED, M.TOPIC_UPDATED, M.TOPIC_STATUS]


def test_rollback_leaves_no_partial_state(env):
    svc, repo, *_ = env
    orig = repo.enqueue
    repo.enqueue = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        svc.ingest(ev())
    repo.enqueue = orig
    assert repo.list_incidents()[1] == 0 and repo.outbox_stats()["total"] == 0


def test_list_filters(env):
    svc, *_ = env
    svc.ingest(ev(needs_review=True))
    svc.ingest(ev(camera_id="CAM_002", track_id="CAM_002-T001"))
    assert svc.list(camera_id="CAM_002")[1] == 1
    assert svc.list(needs_review=True)[1] == 1
    assert svc.list(status="OPEN")[1] == 2


def test_evidence_path_traversal_blocked(env):
    svc, *_ = env
    with pytest.raises(ValueError):
        svc.store.get("file://../../etc/passwd")
