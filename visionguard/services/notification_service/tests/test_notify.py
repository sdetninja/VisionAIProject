import pytest
from notification_service.config import Config
from notification_service.factory import build


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class Chan:
    def __init__(self): self.sent, self.fail = [], False
    def send(self, m):
        if self.fail: raise ConnectionError("down")
        self.sent.append(m)


def inc(iid="INC-1", **kw):
    b = {"incident_id": iid, "event_type": "PPE_VIOLATION", "camera_id": "CAM_001", "zone": "FLOOR",
         "missing_ppe": ["helmet"], "needs_review": False, "status": "OPEN"}
    b.update(kw)
    return b


def created(i): return {"topic": "incident.created.v1", "payload": {"incident": i}}
def analyzed(iid, sev, summary="s"): return {"topic": "incident.analyzed.v1", "payload": {"incident_id": iid, "severity": sev, "summary": summary, "recommended_actions": ["act"]}}
def status(i, to): return {"topic": "incident.status_changed.v1", "payload": {"incident": i, "to": to}}


def make(**over):
    cfg = Config(channels={"a": {}, "b": {}}, routes=[
        {"name": "all", "match": {}, "channels": ["a"]},
        {"name": "high", "match": {"min_severity": "HIGH"}, "channels": ["b"]}],
        escalations=[{"name": "sup", "after_min": 15, "match": {"min_severity": "HIGH"}, "channels": ["b"]}],
        dashboard_url="http://d", analysis_wait_sec=30, **over)
    clk, a, b = Clock(), Chan(), Chan()
    return build(cfg, {"a": a, "b": b}, clk), clk, a, b


def test_waits_for_analysis_then_routes_by_severity():
    s, clk, a, b = make()
    s.handle_message(created(inc()))
    assert a.sent == [] and b.sent == []          # waiting for the AI severity
    s.handle_message(analyzed("INC-1", "MEDIUM"))
    assert len(a.sent) == 1 and b.sent == []
    assert a.sent[0]["link"] == "http://d/incidents/INC-1" and "Suggested: act" in a.sent[0]["text"]


def test_high_goes_to_both_channels():
    s, clk, a, b = make()
    s.handle_message(created(inc()))
    s.handle_message(analyzed("INC-1", "HIGH"))
    assert len(a.sent) == 1 and len(b.sent) == 1


def test_unrated_fallback_after_wait_fails_loud():
    s, clk, a, b = make()
    s.handle_message(created(inc()))
    clk.t += 31
    s.tick()
    assert a.sent[0]["severity"] == "UNRATED" and len(b.sent) == 1    # UNRATED treated as HIGH for routing


def test_no_duplicate_on_redelivery():
    s, clk, a, b = make()
    for _ in range(3):
        s.handle_message(created(inc()))
        s.handle_message(analyzed("INC-1", "HIGH"))
    assert len(a.sent) == 1 and len(b.sent) == 1


def test_severity_upgrade_renotifies_once():
    s, clk, a, b = make()
    s.handle_message(created(inc()))
    s.handle_message(analyzed("INC-1", "MEDIUM"))
    s.handle_message(analyzed("INC-1", "CRITICAL"))
    s.handle_message(analyzed("INC-1", "CRITICAL"))
    kinds = [m["kind"] for m in a.sent]
    assert kinds == ["initial", "upgrade"] and "raised from MEDIUM to CRITICAL" in a.sent[1]["text"]


def test_escalation_when_unacknowledged():
    s, clk, a, b = make()
    s.handle_message(created(inc())); s.handle_message(analyzed("INC-1", "HIGH"))
    clk.t += 14 * 60; s.tick()
    assert len(b.sent) == 1
    clk.t += 2 * 60; s.tick(); s.tick()
    assert [m["kind"] for m in b.sent] == ["initial", "escalation"] and b.sent[1]["title"].startswith("ESCALATION")


def test_ack_prevents_escalation():
    s, clk, a, b = make()
    i = inc()
    s.handle_message(created(i)); s.handle_message(analyzed("INC-1", "HIGH"))
    s.handle_message(status(i, "UNDER_REVIEW"))
    clk.t += 3600; s.tick()
    assert len(b.sent) == 1


def test_false_positive_before_send_cancels_initial():
    s, clk, a, b = make()
    i = inc()
    s.handle_message(created(i))
    s.handle_message(status(i, "FALSE_POSITIVE"))
    s.handle_message(analyzed("INC-1", "HIGH"))
    assert a.sent == [] and b.sent == []


def test_retry_backoff_then_dead_then_requeue():
    s, clk, a, b = make(max_attempts=3, backoff_base_sec=10)
    a.fail = True
    s.handle_message(created(inc())); s.handle_message(analyzed("INC-1", "LOW"))
    assert s.store.stats() == {"PENDING": 1}
    s.tick(); assert a.sent == []                    # backoff: not yet due
    clk.t += 10; s.tick()
    clk.t += 20; s.tick()
    assert s.store.stats() == {"DEAD": 1} and s.counters["dead"] == 1
    a.fail = False
    assert s.store.requeue_dead(clk.t) == 1
    s.tick(); assert len(a.sent) == 1


def test_one_channel_failure_does_not_block_other():
    s, clk, a, b = make()
    a.fail = True
    s.handle_message(created(inc())); s.handle_message(analyzed("INC-1", "HIGH"))
    assert len(b.sent) == 1


def test_rate_limit_defers_not_drops():
    s, clk, a, b = make(rate_limit_per_min=2)
    for i in range(4):
        s.handle_message(created(inc(f"INC-{i}"))); s.handle_message(analyzed(f"INC-{i}", "LOW"))
    assert len(a.sent) == 2 and s.store.stats()["PENDING"] == 2
    clk.t += 61; s.tick()
    assert len(a.sent) == 4


def test_match_filters():
    cfg = Config(channels={"a": {}}, routes=[{"name": "r", "match": {"zone": ["WELDING"], "needs_review": True}, "channels": ["a"]}])
    a = Chan(); s = build(cfg, {"a": a}, Clock())
    s.handle_message(created(inc("I1", zone="FLOOR", needs_review=True))); s.handle_message(analyzed("I1", "HIGH"))
    s.handle_message(created(inc("I2", zone="WELDING", needs_review=False))); s.handle_message(analyzed("I2", "HIGH"))
    s.handle_message(created(inc("I3", zone="WELDING", needs_review=True))); s.handle_message(analyzed("I3", "HIGH"))
    assert [m["incident_id"] for m in a.sent] == ["I3"] and "human review" in a.sent[0]["text"]


def test_config_validation(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("channels: {a: {type: console}}\nroutes: [{name: x, match: {}, channels: [zzz]}]")
    with pytest.raises(ValueError):
        Config.load(str(p))
    p.write_text("channels: {a: {type: console}}\nroutes: [{name: x, match: {severity: [BOGUS]}, channels: [a]}]")
    with pytest.raises(ValueError):
        Config.load(str(p))
