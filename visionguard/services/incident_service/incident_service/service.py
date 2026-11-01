from __future__ import annotations
import logging
import threading
from datetime import datetime
from typing import Optional

from . import models as M
from .config import Config
from .evidence import FrameFetcher, EvidenceStore, collect_evidence

log = logging.getLogger("incident_service")


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        raise M.InvalidEvent(f"bad timestamp {iso!r}")


class IncidentService:
    def __init__(self, cfg: Config, repo, store: EvidenceStore, fetch: Optional[FrameFetcher] = None):
        self.cfg, self.repo, self.store, self.fetch = cfg, repo, store, fetch
        self._lock = threading.Lock()   # serialises ingest so grouping is race-free in one process
        self.counters = {"events": 0, "duplicates": 0, "created": 0, "merged": 0, "rejected": 0}

    # ------------------------------------------------------------------ ingest
    def ingest(self, ev: dict) -> dict:
        """Idempotent. Returns {"incident_id", "action": created|merged|duplicate}."""
        try:
            M.validate_event(ev)
            ets = _ts(ev["timestamp"])
        except M.InvalidEvent:
            self.counters["rejected"] += 1
            raise
        with self._lock:
            self.counters["events"] += 1
            dup = self.repo.event_incident(ev["idempotency_key"])
            if dup:
                self.counters["duplicates"] += 1
                return {"incident_id": dup, "action": "duplicate"}

            # Evidence fetched BEFORE the transaction: slow I/O must not hold the DB lock.
            # Orphaned JPEGs after a rollback are harmless; keys are deterministic per event.
            uris = []
            if self.fetch:
                try:
                    uris = collect_evidence(ev, self.fetch, self.store, ev["idempotency_key"].replace("|", "_")[:60],
                                            self.cfg.max_evidence_frames, self.cfg.jpeg_quality,
                                            self.cfg.draw_boxes, self.cfg.blur_faces)
                except Exception:
                    log.exception("evidence collection failed; continuing without")
            ev_status = "ok" if uris else ("missing" if ev.get("evidence_frames") else "none")

            with self.repo.transaction():
                inc = self.repo.find_mergeable(ev, M.ACTIVE, ets - self.cfg.merge_window_sec)
                if inc:
                    iid = self._merge(inc, ev, ets, uris, ev_status)
                    action, topic = "merged", M.TOPIC_UPDATED
                else:
                    iid = self._create(ev, ets, uris, ev_status)
                    action, topic = "created", M.TOPIC_CREATED
                self.repo.insert_event(ev, iid)
                self.repo.enqueue(topic, iid, self._envelope(iid, topic))
            self.counters["created" if action == "created" else "merged"] += 1
            return {"incident_id": iid, "action": action}

    def _create(self, ev, ets, uris, ev_status) -> str:
        review = bool(ev.get("needs_review")) or ev["event_type"] in self.cfg.review_required_types
        iid = self.repo.create_incident({
            "event_type": ev["event_type"], "camera_id": ev["camera_id"], "zone": ev["zone"],
            "track_id": ev.get("track_id"), "hazard_class": ev.get("hazard_class"),
            "missing_ppe": sorted(set(ev.get("missing_ppe") or [])), "rule_ids": [ev["rule_id"]] if ev.get("rule_id") else [],
            "status": M.OPEN, "needs_review": review, "confidence": float(ev.get("confidence") or 0),
            "first_event_time": ev.get("first_seen_at") or ev["timestamp"], "last_event_time": ev["timestamp"],
            "last_event_ts": ets, "evidence_uris": uris, "thumbnail_uri": uris[0] if uris else None,
            "evidence_status": ev_status})
        self.repo.add_history(iid, "system", None, M.OPEN, "created from " + ev["event_id"])
        return iid

    def _merge(self, inc, ev, ets, uris, ev_status) -> str:
        iid = inc["incident_id"]
        rules = inc["rule_ids"] + ([ev["rule_id"]] if ev.get("rule_id") and ev["rule_id"] not in inc["rule_ids"] else [])
        all_uris = inc["evidence_uris"] + uris
        self.repo.update_incident(
            iid, missing_ppe=sorted(set(inc["missing_ppe"]) | set(ev.get("missing_ppe") or [])), rule_ids=rules,
            needs_review=inc["needs_review"] or bool(ev.get("needs_review")),
            confidence=max(inc["confidence"], float(ev.get("confidence") or 0)),
            last_event_time=ev["timestamp"], last_event_ts=max(inc["last_event_ts"], ets),
            event_count=inc["event_count"] + 1, evidence_uris=all_uris,
            thumbnail_uri=inc["thumbnail_uri"] or (uris[0] if uris else None),
            evidence_status="ok" if all_uris else ev_status)
        return iid

    def _envelope(self, iid: str, topic: str, extra: dict | None = None) -> dict:
        inc = self.repo.get_incident(iid)
        return {"schema_version": M.SCHEMA_VERSION, "topic": topic, "incident": inc, **(extra or {})}

    # ------------------------------------------------------------------ review
    def transition(self, iid: str, action: str, actor: str, comment: str | None = None) -> dict:
        if action not in M.ACTIONS:
            raise M.InvalidTransition(f"unknown action {action!r}")
        if not actor:
            raise M.IncidentError("actor required")
        allowed, target = M.ACTIONS[action]
        with self._lock, self.repo.transaction():
            inc = self.repo.get_incident(iid)
            if not inc:
                raise M.NotFound(iid)
            if inc["status"] not in allowed:
                raise M.InvalidTransition(f"{action} not allowed from {inc['status']}")
            self.repo.update_incident(iid, status=target, reviewed_by=actor if action != "start_review" else inc["reviewed_by"],
                                      review_comment=comment or inc["review_comment"])
            self.repo.add_history(iid, actor, inc["status"], target, comment)
            if target in (M.CONFIRMED, M.FALSE_POSITIVE):
                # Review verdicts are the feedback loop for MLOps (M8): keep labelled events + evidence.
                for e in self.repo.events_of(iid):
                    self.repo.add_label(iid, e, "true_positive" if target == M.CONFIRMED else "false_positive",
                                        actor, inc["evidence_uris"])
            self.repo.enqueue(M.TOPIC_STATUS, iid, self._envelope(iid, M.TOPIC_STATUS, {"from": inc["status"], "to": target}))
        return self.repo.get_incident(iid)

    # ------------------------------------------------------------------ queries
    def get(self, iid: str) -> dict:
        inc = self.repo.get_incident(iid)
        if not inc:
            raise M.NotFound(iid)
        inc["history"] = self.repo.history(iid)
        inc["events"] = self.repo.events_of(iid)
        return inc

    def list(self, **kw):
        return self.repo.list_incidents(**kw)

    def stats(self) -> dict:
        return {**self.counters, "outbox": self.repo.outbox_stats()}
