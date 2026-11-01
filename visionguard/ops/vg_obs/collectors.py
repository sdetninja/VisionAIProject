"""Domain metrics for M2-M5, computed at scrape time. Prefer DB-derived values: with a worker process and an API
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


def register_agent(service, registry=None):
    # NOTE: kind="fallback" is a SUBSET of kind="analyzed" (LLM output rejected by guardrails -> template used).
    CallbackMetric("vg_agent_events_total", "Agent counters by kind (fallback is a subset of analyzed)", "counter",
                   lambda: [({"kind": k}, v) for k, v in _num_items(service.counters).items()], registry or REGISTRY)


def register_notification(service, registry=None):
    r = registry or REGISTRY
    CallbackMetric("vg_notify_events_total", "Notification counters by kind (this process)", "counter",
                   lambda: [({"kind": k}, v) for k, v in _num_items(service.counters).items()], r)
    CallbackMetric("vg_notifications", "Notification rows by status (from DB; DEAD needs operator action)", "gauge",
                   lambda: [({"status": k}, v) for k, v in service.store.stats().items()], r)


def register_rule_engine(engine, registry=None):
    CallbackMetric("vg_rule_engine_stat", "Rule-engine stats (frames, events_emitted, active_states, suppressed_duplicates)", "gauge",
                   lambda: [({"name": k}, v) for k, v in _num_items(engine.stats()).items()], registry or REGISTRY)
