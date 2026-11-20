import io
import json

import pytest
from PIL import Image

from agent_service.factory import build
from agent_service.guardrails import validate
from agent_service.judge.panel import DisabledPanel
from agent_service.kb import BM25Retriever, load_chunks
from agent_service.m3client import FakeM3
from agent_service.risk import load_risk_config, score_incident

KB = "knowledge_base"
THRESH = {"helmet": {"reject_below": 0.34, "confirm_above": 0.58}, "vest": {"reject_below": 0.22, "confirm_above": 0.75},
          "hazard": {"reject_below": 0.78, "confirm_above": 0.78}}


def inc(**kw):
    b = {"incident_id": "INC-000001", "event_type": "PPE_VIOLATION", "camera_id": "CAM_001", "zone": "FLOOR",
         "missing_ppe": ["helmet"], "hazard_class": None, "needs_review": False, "event_count": 1, "rule_ids": ["PPE_HELMET"],
         "first_event_time": "2026-01-01T10:00:00Z", "updated_at": "v1", "events": [], "raw_evidence": []}
    b.update(kw)
    return b


def jpeg(w=640, h=480):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 90)).save(b, "JPEG")
    return b.getvalue()


def with_evidence(i, bbox=(200, 100, 300, 400), frames=(1, 2, 3)):
    """Incident detail as M3 returns it: one event per missing item, three unannotated frames each."""
    events, raw, images = [], [], {}
    items = i["missing_ppe"] or [None]
    for k, item in enumerate(items):
        eid = f"ev{k}"
        events.append({"event_id": eid, "event_type": i["event_type"], "missing_ppe": [item] if item else [],
                       "hazard_class": i.get("hazard_class"), "bbox": list(bbox)})
        for f in frames:
            uri = f"file://{eid}_{f}_raw.jpg"
            raw.append({"uri": uri, "event_id": eid, "frame_id": f})
            images[uri] = jpeg()
    return {**i, "events": events, "raw_evidence": raw}, images


class StubScorer:
    """Constant claim probability per check (what the ViT ensemble would return)."""
    def __init__(self, **p):
        self.p, self.thresholds, self.versions, self.calls = p, THRESH, {"stub": "1"}, []

    def score(self, check, crops):
        self.calls.append((check, len(crops), crops[0].size))
        return {"models": {"a": [self.p[check]] * len(crops)}, "ensemble": [self.p[check]] * len(crops)}


class StubPanel:
    def __init__(self, opus, sonnet, cost=0.01):
        self.v, self.cost, self.seen = {"opus": opus, "sonnet": sonnet}, cost, []

    def review(self, claim, crop_jpeg, frame_jpeg):
        self.seen.append((claim, crop_jpeg is not None, frame_jpeg is not None))
        return {n: {"verdict": v, "confidence": 0.9, "lookalike": None, "reason": "r", "cost_usd": self.cost} for n, v in self.v.items()}


class BadLLM:
    def __init__(self, out): self.out = out
    def explain(self, facts, passages):
        if isinstance(self.out, Exception): raise self.out
        return self.out


def svc_for(detail, images, scorer=None, panel=None, **kw):
    m3 = FakeM3({detail["incident_id"]: detail}, images)
    return build(KB, m3=m3, scorer=scorer, panel_obj=panel or DisabledPanel(), **kw), m3


# ------------------------------------------------------------------ knowledge base, risk, guardrails (unchanged)
def test_load_chunks_and_retrieval():
    ch = load_chunks(KB)
    assert len(ch) >= 8 and all(c.source for c in ch)
    r = BM25Retriever(ch)
    top = r.search("missing helmet head protection")[0][0]
    assert top.source == "OSHA-1910.135" and top.heading.startswith("Head protection required")
    assert r.search("fire detected")[0][0].source == "OSHA-1910.38"
    assert r.search("smoke detected")[0][0].source == "OSHA-1910.38-SMOKE"
    assert r.search("missing gloves")[0][0].source in {"OSHA-1910.138", "SITE-PPE-RESPONSE"}
    assert r.search("zzzz qqqq") == []


def test_rule_id_letter_does_not_match_text():
    r = BM25Retriever(load_chunks(KB))
    assert r.search("R-ZONE") == []      # "zone" is a stop word and the 1-letter "r" is dropped (it matched "Type R")


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


# ------------------------------------------------------------------ assessment service end to end (fake M3)
def test_ppe_assessment_written_to_m3_and_idempotent():
    d, imgs = with_evidence(inc())
    sc = StubScorer(helmet=0.9)
    svc, m3 = svc_for(d, imgs, sc)
    a = svc.assess("INC-000001")
    assert a["verdict"] == "CONFIRMED" and a["judge_mode"] == "vit" and a["vit"]["helmet"]["frames"] == 3
    assert a["severity"] == "HIGH" and a["advisory_only"] and a["grounded"] and a["explanation_mode"] == "template"
    assert {"SITE-PPE-RESPONSE", "OSHA-1910.135"} <= {c["source"] for c in a["citations"]}
    assert a["incident_version"] == "v1" and m3.posted[0][0] == "INC-000001"
    assert sc.calls[0][0] == "helmet" and sc.calls[0][2] == (78, 78)    # head square 60 px + 15 % padding each side
    assert svc.assess("INC-000001")["verdict"] == "CONFIRMED" and svc.counters["cached"] == 1 and len(m3.posted) == 1
    m3.inc["INC-000001"]["updated_at"] = "v2"                            # a new version is judged again
    svc.assess("INC-000001")
    assert len(m3.posted) == 2 and svc.counters["assessed"] == 2


@pytest.mark.parametrize("helmet,vest,expect", [
    (0.9, 0.1, "CONFIRMED"),        # any item confirmed
    (0.1, 0.1, "LIKELY_FALSE"),     # every item rejected
    (0.1, 0.5, "UNSURE"),           # one rejected, one unsure
    (0.45, 0.5, "UNSURE"),
])
def test_grouped_ppe_verdicts(helmet, vest, expect):
    d, imgs = with_evidence(inc(missing_ppe=["helmet", "vest"]))
    svc, _ = svc_for(d, imgs, StubScorer(helmet=helmet, vest=vest))
    a = svc.assess("INC-000001")
    assert a["verdict"] == expect and set(a["vit"]["items"]) == {"helmet", "vest"}


def test_items_without_a_judge():
    d, imgs = with_evidence(inc(missing_ppe=["gloves"]))
    svc, _ = svc_for(d, imgs, StubScorer(helmet=0.1))
    a = svc.assess("INC-000001")
    assert a["verdict"] == "NO_JUDGE" and a["judge_mode"] == "none" and "no trained check" in a["judge_reason"]
    d, imgs = with_evidence(inc(missing_ppe=["helmet", "gloves"]))
    svc, _ = svc_for(d, imgs, StubScorer(helmet=0.1))
    assert svc.assess("INC-000001")["verdict"] == "UNSURE"     # helmet rejected, gloves unjudged: not "false"


def test_no_models_or_no_evidence_is_no_judge():
    d, imgs = with_evidence(inc())
    svc, _ = svc_for(d, imgs, scorer=None)
    assert svc.assess("INC-000001")["verdict"] == "NO_JUDGE"
    svc, _ = svc_for(inc(), {}, StubScorer(helmet=0.9))         # no unannotated frames stored
    a = svc.assess("INC-000001")
    assert a["verdict"] == "NO_JUDGE" and "no usable evidence" in a["judge_reason"]


def test_restricted_area_not_applicable():
    d = inc(event_type="RESTRICTED_AREA_ENTRY", missing_ppe=[])
    svc, _ = svc_for(d, {}, StubScorer())
    assert svc.assess("INC-000001")["verdict"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("vit,opus,sonnet,expect", [
    (0.99, "CONFIRMED", "CONFIRMED", "CONFIRMED"),
    (0.10, "CONFIRMED", "CONFIRMED", "CONFIRMED"),     # both LLMs confirm: ViT cannot overrule
    (0.10, "REJECTED", "REJECTED", "LIKELY_FALSE"),    # all three say false
    (0.95, "REJECTED", "REJECTED", "UNSURE"),          # ViT disagrees
    (0.10, "REJECTED", "UNSURE", "UNSURE"),            # LLMs disagree
    (0.10, "CONFIRMED", "REJECTED", "UNSURE"),
])
def test_fire_smoke_panel_table(vit, opus, sonnet, expect):
    d, imgs = with_evidence(inc(event_type="HAZARD_DETECTED", missing_ppe=[], hazard_class="fire"), bbox=(100, 50, 400, 300))
    panel = StubPanel(opus, sonnet)
    svc, _ = svc_for(d, imgs, StubScorer(hazard=vit), panel)
    a = svc.assess("INC-000001")
    assert a["verdict"] == expect and a["judge_mode"] == "vit+llm" and set(a["llm"]) == {"opus", "sonnet"}
    assert panel.seen[0] == ({"text": "fire detected", "camera_id": "CAM_001", "zone": "FLOOR"}, True, True)
    assert a["severity"] == "CRITICAL"                  # the judge never lowers fire/smoke severity
    assert svc.counters["llm_cost_usd"] == pytest.approx(0.02)


def test_fire_without_api_key_is_unsure():
    d, imgs = with_evidence(inc(event_type="HAZARD_DETECTED", missing_ppe=[], hazard_class="smoke"))
    svc, _ = svc_for(d, imgs, StubScorer(hazard=0.01))
    a = svc.assess("INC-000001")
    assert a["verdict"] == "UNSURE" and all(j["error"] for j in a["llm"].values())


def test_broken_judge_never_blocks():
    class Boom:
        thresholds, versions = THRESH, {}
        def score(self, *a): raise RuntimeError("gpu gone")
    d, imgs = with_evidence(inc())
    svc, m3 = svc_for(d, imgs, Boom())
    a = svc.assess("INC-000001")
    assert a["verdict"] == "NO_JUDGE" and "RuntimeError" in a["judge_reason"] and len(m3.posted) == 1


@pytest.mark.parametrize("out", ["garbage", RuntimeError("api down"),
                                 json.dumps({"summary": "Low severity", "recommended_actions": [], "citations": ["bogus"]})])
def test_llm_failure_falls_back_and_keeps_severity(out):
    d, imgs = with_evidence(inc())
    svc, _ = svc_for(d, imgs, StubScorer(helmet=0.9), llm_obj=BadLLM(out))
    a = svc.assess("INC-000001")
    assert a["explanation_mode"] == "template_fallback" and a["severity"] == "HIGH"


def test_no_kb_match():
    svc, _ = svc_for(inc(event_type="UNKNOWN_X", missing_ppe=[], zone="QQQ", rule_ids=[]), {}, StubScorer())
    assert not svc.assess("INC-000001")["grounded"]


def test_repeat_pattern_raises_score():
    m3 = FakeM3({"INC-1": inc(incident_id="INC-1"),
                 "INC-2": inc(incident_id="INC-2", first_event_time="2026-01-01T12:00:00Z")})
    svc = build(KB, m3=m3, scorer=StubScorer(helmet=0.9), panel_obj=DisabledPanel())
    a1, a2 = svc.assess("INC-1"), svc.assess("INC-2")
    assert a2["risk_score"] > a1["risk_score"] and any("similar" in f for f in a2["risk_factors"])


def test_handle_message_topics():
    d, imgs = with_evidence(inc())
    svc, _ = svc_for(d, imgs, StubScorer(helmet=0.9))
    assert svc.handle_message({"topic": "incident.status_changed.v1", "payload": {}}) is None
    r = svc.handle_message({"topic": "incident.created.v1", "payload": {"incident": {"incident_id": "INC-000001"}}})
    assert r["incident_id"] == "INC-000001"
