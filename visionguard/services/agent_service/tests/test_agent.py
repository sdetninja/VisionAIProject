import json
import pytest
from agent_service.bus import MemoryPublisher
from agent_service.factory import build
from agent_service.guardrails import validate
from agent_service.kb import BM25Retriever, load_chunks
from agent_service.risk import load_risk_config, score_incident

KB = "knowledge_base"


def inc(**kw):
    b = {"incident_id": "INC-000001", "event_type": "PPE_VIOLATION", "camera_id": "CAM_001", "zone": "FLOOR",
         "missing_ppe": ["helmet"], "hazard_class": None, "needs_review": False, "event_count": 1, "rule_ids": ["PPE_HELMET"],
         "first_event_time": "2026-01-01T10:00:00Z", "updated_at": "v1"}
    b.update(kw)
    return b


class BadLLM:
    def __init__(self, out): self.out = out
    def explain(self, facts, passages):
        if isinstance(self.out, Exception): raise self.out
        return self.out


def test_load_chunks_and_retrieval():
    ch = load_chunks(KB)
    assert len(ch) >= 8 and all(c.source for c in ch)
    r = BM25Retriever(ch)
    top = r.search("missing helmet head protection")[0][0]
    assert top.source == "SOP-PPE-01" and top.heading == "Head protection"
    assert r.search("fire detected")[0][0].source == "SOP-FIRE-03"
    assert r.search("zzzz qqqq") == []


def test_risk_deterministic_and_monotonic():
    cfg = load_risk_config()
    a = score_incident(inc(), cfg)
    b = score_incident(inc(missing_ppe=["helmet", "vest"]), cfg)
    assert a.score == 55 and a.severity == "HIGH" and b.score > a.score
    assert score_incident(inc(), cfg).score == a.score
    assert score_incident(inc(), cfg, recent_similar=2).score == a.score + 16
    assert score_incident(inc(needs_review=True), cfg).score < a.score
    fire = score_incident(inc(event_type="HAZARD_DETECTED", missing_ppe=[], hazard_class="fire"), cfg)
    assert fire.severity == "CRITICAL" and fire.score == 100
    assert score_incident(inc(missing_ppe=["x"] * 50), cfg).score == 100


def test_risk_config_rejects_unknown_key(tmp_path):
    p = tmp_path / "r.yaml"; p.write_text("bogus: 1")
    with pytest.raises(ValueError):
        load_risk_config(str(p))


def test_guardrails():
    facts = {"severity": "HIGH"}
    ok = json.dumps({"summary": "High severity helmet issue", "recommended_actions": ["a"], "citations": ["x"]})
    assert validate(ok, facts, {"x"})[0] is not None
    assert validate("nope", facts, {"x"})[0] is None
    bad_cit = json.dumps({"summary": "s", "recommended_actions": [], "citations": ["zzz"]})
    assert "citation" in validate(bad_cit, facts, {"x"})[1][0]
    contra = json.dumps({"summary": "This is a low severity matter", "recommended_actions": [], "citations": ["x"]})
    assert any("contradicts" in p for p in validate(contra, facts, {"x"})[1])
    nocite = json.dumps({"summary": "s", "recommended_actions": [], "citations": []})
    assert validate(nocite, facts, {"x"})[0] is None
    assert validate(nocite, facts, set())[0] is not None


def test_end_to_end_template():
    bus = MemoryPublisher()
    svc = build(KB, bus=bus)
    a = svc.analyze(inc())
    assert a["severity"] == "HIGH" and a["advisory_only"] and a["grounded"] and a["explanation_mode"] == "template"
    assert a["citations"][0]["source"] == "SOP-PPE-01"
    assert bus.messages[0][0] == "incident.analyzed.v1"
    assert svc.analyze(inc()) == a and svc.counters["cached"] == 1 and len(bus.messages) == 1
    svc.analyze(inc(updated_at="v2"))
    assert svc.counters["analyzed"] == 2


@pytest.mark.parametrize("out", ["garbage", RuntimeError("api down"),
                                 json.dumps({"summary": "Low severity", "recommended_actions": [], "citations": ["bogus"]})])
def test_llm_failure_falls_back_and_keeps_severity(out):
    svc = build(KB, llm_obj=BadLLM(out))
    a = svc.analyze(inc())
    assert a["explanation_mode"] == "template_fallback" and a["severity"] == "HIGH" and a["guardrail_problems"]


def test_no_kb_match_requires_human_review():
    svc = build(KB)
    a = svc.analyze(inc(event_type="UNKNOWN_X", missing_ppe=[], zone="QQQ", rule_ids=[]))
    assert not a["grounded"] and a["needs_human_review"]


def test_repeat_pattern_raises_score():
    svc = build(KB)
    a1 = svc.analyze(inc(incident_id="INC-1"))
    a2 = svc.analyze(inc(incident_id="INC-2", first_event_time="2026-01-01T12:00:00Z"))
    assert a2["risk_score"] > a1["risk_score"] and any("similar" in f for f in a2["risk_factors"])


def test_handle_message_topics():
    svc = build(KB)
    assert svc.handle_message({"topic": "incident.status_changed.v1", "payload": {}}) is None
    r = svc.handle_message({"topic": "incident.created.v1", "payload": {"incident": inc()}})
    assert r["incident_id"] == "INC-000001"
    with pytest.raises(ValueError):
        svc.analyze({"x": 1})
