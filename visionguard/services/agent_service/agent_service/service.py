"""Assessment service (v3, was the M4 agent): judge + risk + explanation for each new incident version, stored in M3.

For every incident.created / incident.updated message on M3's bus:
  1. fetch the incident from M3 (events, unannotated evidence frames)
  2. judge   PPE: ViT ensemble; fire/smoke: ViT ensemble + LLM panel        (judge/)
  3. risk    deterministic rules (risk.yaml)                                   (risk.py)
  4. explain SOP search + template or Claude                                  (pipeline.py, kb.py, llm.py)
  5. POST /incidents/{id}/assessment to M3: the incident's assessments table is the only store.
Idempotent per (incident, version): a version that already has an assessment in M3 is not judged again.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .pipeline import analyze_incident

log = logging.getLogger("assessment")
HANDLED_TOPICS = ("incident.created.v1", "incident.updated.v1")
SCHEMA = "3.0"


def _ts(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return datetime.now(timezone.utc).timestamp()


class AssessmentService:
    def __init__(self, risk_cfg, retriever, llm, judge, m3, kb_version="kb"):
        self.risk_cfg, self.retriever, self.llm, self.judge, self.m3 = risk_cfg, retriever, llm, judge, m3
        self.kb_version = kb_version
        self.counters = {"assessed": 0, "cached": 0, "skipped": 0, "failed": 0, "fallback": 0,
                         "judge_seconds": 0.0, "llm_cost_usd": 0.0}
        self.verdicts = {}

    def similar_count(self, inc) -> int:
        """Earlier incidents of the same type on the same camera+zone within the repeat window (risk signal)."""
        hours = self.risk_cfg["repeat"]["window_hours"]
        t = _ts(inc.get("first_event_time"))
        try:
            items = self.m3.incidents(camera_id=inc["camera_id"], zone=inc["zone"], limit=200)["items"]
        except Exception:
            log.warning("could not list incidents for the repeat count; using 0")
            return 0
        return sum(1 for i in items if i["incident_id"] != inc["incident_id"] and i["event_type"] == inc["event_type"]
                   and t - hours * 3600 <= _ts(i.get("first_event_time")) <= t)

    def assess(self, iid: str, force=False) -> dict:
        inc = self.m3.incident(iid)
        ver = inc.get("updated_at") or ""
        prev = inc.get("assessment")
        if prev and prev.get("incident_version") == ver and not force:
            self.counters["cached"] += 1
            return prev
        t0 = time.time()
        try:
            j = self.judge.judge(inc, self.m3.evidence)
        except Exception as e:          # a broken judge must never block the incident
            log.exception("judge failed for %s", iid)
            j = {"verdict": "NO_JUDGE", "judge_mode": "none", "vit": None, "llm": None, "reason": f"judge error: {type(e).__name__}"}
        dt = time.time() - t0
        a = analyze_incident(inc, risk_cfg=self.risk_cfg, retriever=self.retriever, llm=self.llm,
                             recent_similar=self.similar_count(inc))
        out = {
            "schema_version": SCHEMA, "incident_id": iid, "incident_version": ver, "advisory_only": True,
            "verdict": j["verdict"], "judge_mode": j["judge_mode"], "judge_reason": j.get("reason") or "",
            "vit": {**(j.get("vit") or {}), **({"items": j["items"]} if j.get("items") else {})} or None,
            "llm": j.get("llm"),
            "severity": a["severity"], "risk_score": a["risk_score"], "risk_factors": a["risk_factors"],
            "summary": a["summary"], "recommended_actions": a["recommended_actions"], "citations": a["citations"],
            "grounded": a["grounded"], "explanation_mode": a["explanation_mode"],
            "model_versions": {**self.judge.versions(), "kb": self.kb_version, "judge_seconds": round(dt, 2)},
        }
        self.m3.post_assessment(iid, out)
        self.counters["assessed"] += 1
        self.counters["fallback"] += a["explanation_mode"] == "template_fallback"
        self.counters["judge_seconds"] += dt
        self.counters["llm_cost_usd"] += sum((v or {}).get("cost_usd") or 0 for v in (j.get("llm") or {}).values())
        self.verdicts[j["verdict"]] = self.verdicts.get(j["verdict"], 0) + 1
        return out

    def handle_message(self, msg: dict):
        """msg: a bus record {'topic','key','payload'} from M3's outbox. Only created/updated are assessed."""
        if msg.get("topic") not in HANDLED_TOPICS:
            self.counters["skipped"] += 1
            return None
        try:
            return self.assess(msg["payload"]["incident"]["incident_id"])
        except Exception:
            self.counters["failed"] += 1
            raise
