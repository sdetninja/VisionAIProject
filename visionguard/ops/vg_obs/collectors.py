"""Domain metrics for M2-M4 (v3: M5 removed), computed at scrape time. Prefer DB-derived values: with a worker process and an API
process sharing one database, only the DB gives a consistent picture (in-memory counters are per-process)."""
from __future__ import annotations

from .metrics import REGISTRY, CallbackMetric


def _num_items(d: dict):
    return {k: v for k, v in d.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def register_incident(service, registry=None):
    r = registry or REGISTRY
    snap = lambda: service.repo.metrics_snapshot()
    CallbackMetric("vg_incident_events_total", "Events handled by this process, by outcome", "counter",
                   lambda: [({"outcome": o}, service.counters.get(k, 0)) for o, k in
                            (("created", "created"), ("merged", "merged"), ("duplicate", "duplicates"), ("rejected", "rejected"))], r)
    CallbackMetric("vg_incidents", "Incidents by status (from DB)", "gauge",
                   lambda: [({"status": s}, n) for s, n in snap()["by_status"].items()], r)
    CallbackMetric("vg_incident_oldest_open_age_seconds", "Age of the oldest incident still OPEN (0 if none)", "gauge",
                   lambda: [({}, snap()["oldest_open_age_s"])], r)
    CallbackMetric("vg_incident_outbox", "Outbox rows by state (dead = needs operator action)", "gauge",
                   lambda: [({"state": k}, v) for k, v in snap()["outbox"].items() if k in ("pending", "dead")], r)
    CallbackMetric("vg_review_labels_total", "Reviewer verdicts recorded (monotonic)", "counter",
                   lambda: [({"label": k}, v) for k, v in snap()["labels"].items()], r)
    CallbackMetric("vg_incident_judge_verdicts", "Incidents by latest judge verdict (from DB)", "gauge",
                   lambda: [({"verdict": k}, v) for k, v in service.repo.assessment_counts().items()], r)


_ASSESS_KINDS = ("assessed", "cached", "skipped", "failed", "fallback")


def register_assessment(service, registry=None):
    """Assessment service (v3, was the M4 agent). kind="fallback" is a SUBSET of kind="assessed" (LLM explanation
    rejected by guardrails -> template used)."""
    r = registry or REGISTRY
    CallbackMetric("vg_assessment_events_total", "Assessment counters by kind (fallback is a subset of assessed)", "counter",
                   lambda: [({"kind": k}, service.counters.get(k, 0)) for k in _ASSESS_KINDS], r)
    CallbackMetric("vg_assessment_verdicts_total", "Judge verdicts given by this process", "counter",
                   lambda: [({"verdict": k}, v) for k, v in service.verdicts.items()], r)
    CallbackMetric("vg_assessment_judge_seconds_total", "Time spent judging (ViT + LLM panel); divide by assessed", "counter",
                   lambda: [({}, service.counters.get("judge_seconds", 0.0))], r)
    CallbackMetric("vg_assessment_llm_cost_usd_total", "Estimated LLM panel cost (fire/smoke only)", "counter",
                   lambda: [({}, service.counters.get("llm_cost_usd", 0.0))], r)


def register_rule_engine(engine, registry=None):
    CallbackMetric("vg_rule_engine_stat", "Rule-engine stats (frames, events_emitted, active_states, suppressed_duplicates)", "gauge",
                   lambda: [({"name": k}, v) for k, v in _num_items(engine.stats()).items()], registry or REGISTRY)
