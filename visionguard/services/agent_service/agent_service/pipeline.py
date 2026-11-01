"""Analysis graph: context -> score -> retrieve -> explain -> validate -> assemble.
Plain ordered steps (each logged in `trace`) so behaviour is testable; the same nodes can be wired into LangGraph."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone

from .guardrails import validate
from .risk import score_incident

SCHEMA = "1.0"


def build_query(inc: dict) -> str:
    parts = [inc["event_type"].replace("_", " ")]
    parts += [f"missing {p}" for p in inc.get("missing_ppe") or []]
    if inc.get("hazard_class"):
        parts.append(inc["hazard_class"])
    parts.append(f"zone {inc['zone']}")
    parts += inc.get("rule_ids") or []
    return " ".join(parts)


def analyze_incident(inc: dict, *, risk_cfg, retriever, llm, recent_similar=0, k=4) -> dict:
    trace = []
    risk = score_incident(inc, risk_cfg, recent_similar)
    trace.append(f"risk={risk.score}/{risk.severity}")
    hits = retriever.search(build_query(inc), k)
    passages = [{"id": c.id, "source": c.source, "heading": c.heading, "text": c.text[:700], "score": s} for c, s in hits]
    trace.append(f"retrieved={len(passages)}")
    facts = {"incident": {k_: inc.get(k_) for k_ in ("incident_id", "event_type", "camera_id", "zone", "missing_ppe",
                                                     "hazard_class", "needs_review", "event_count", "first_event_time")},
             "severity": risk.severity, "score": risk.score, "risk_factors": risk.factors}
    allowed = {p["id"] for p in passages}
    from .llm import TemplateLLM
    explanation, mode, problems = None, ("template" if isinstance(llm, TemplateLLM) else "llm"), []
    try:
        explanation, problems = validate(llm.explain(facts, passages), facts, allowed)
    except Exception as e:      # network/API errors must never block an incident
        problems = [f"llm error: {type(e).__name__}"]
    if explanation is None:
        mode = "template_fallback"
        explanation, p2 = validate(TemplateLLM().explain(facts, passages), facts, allowed)
        assert explanation is not None, p2
    trace.append(f"explain={mode}" + (f" ({'; '.join(problems)})" if problems else ""))
    cited = [p for p in passages if p["id"] in set(explanation.get("citations", []))]
    return {
        "schema_version": SCHEMA, "incident_id": inc["incident_id"], "incident_version": inc.get("updated_at"),
        "advisory_only": True,   # no automatic enforcement based on this output
        "severity": risk.severity, "risk_score": risk.score, "risk_factors": risk.factors,
        "summary": explanation["summary"], "recommended_actions": explanation["recommended_actions"],
        "citations": [{"id": p["id"], "source": p["source"], "heading": p["heading"]} for p in cited],
        "grounded": bool(cited), "explanation_mode": mode, "guardrail_problems": problems,
        "needs_human_review": bool(inc.get("needs_review")) or not cited,
        "trace": trace, "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
