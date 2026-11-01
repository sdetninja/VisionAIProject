"""RuleEngine: TrackResult in -> confirmed ViolationEvents out. Deterministic and side-effect free
(besides its own in-memory debounce state), driven only by frame timestamps (replays give identical output)."""
from __future__ import annotations

import threading
import uuid
from typing import Any, Optional

from .association import Obs, evaluate_item
from .config import HAZARD, PPE_REQUIRED, RESTRICTED_ENTRY, EngineConfig, Rule, Zone
from .geometry import foot_point
from .models import SCHEMA_VERSION, FrameInput
from .state import Confirmation, Sample, StateMachine

_NS = uuid.UUID("6f1c6a52-6b0f-4d6f-9d57-0c9a2f5b8e11")
EVENT_TYPE = {PPE_REQUIRED: "PPE_VIOLATION", RESTRICTED_ENTRY: "RESTRICTED_AREA_ENTRY", HAZARD: "HAZARD_DETECTED"}


class RuleEngine:
    def __init__(self, config: EngineConfig):
        self._lock = threading.RLock()
        self.sm = StateMachine()
        self.config = config
        self.frames = 0
        self.events_emitted = 0
        self.last_observations: dict = {}     # camera_id -> list of dicts, for explainability / debugging
        self._last_expire = 0.0

    # ---- config -----------------------------------------------------------------------------
    def reload(self, config: EngineConfig) -> None:
        with self._lock:
            self.config = config
            self.sm.retain_only({(z.zone_id, r.id) for z in config.zones for r in z.rules})

    # ---- main entry -------------------------------------------------------------------------
    def process(self, track_result: Any) -> list:
        frame = FrameInput.coerce(track_result)
        with self._lock:
            self.frames += 1
            events, obs_log = [], []
            persons = [t for t in frame.tracks if t.cls == "person"]
            for zone in self.config.zones_for(frame.camera_id):
                for rule in zone.rules:
                    if rule.type == HAZARD:
                        self._hazard(frame, zone, rule, events, obs_log)
                    else:
                        self._person_rule(frame, persons, zone, rule, events, obs_log)
            if frame.ts - self._last_expire > 5.0:
                self.sm.expire(frame.ts, self.config.track_expiry_s)
                self._last_expire = frame.ts
            self.last_observations[frame.camera_id] = obs_log
            self.events_emitted += len(events)
            return events

    # ---- per rule family --------------------------------------------------------------------
    def _person_rule(self, frame, persons, zone: Zone, rule: Rule, events, obs_log):
        in_zone = [p for p in persons if zone.contains(foot_point(p.bbox), frame.width, frame.height)]
        out_zone = [p for p in persons if p not in in_zone]

        if rule.type == PPE_REQUIRED:
            obs = evaluate_item(frame, in_zone, rule, self.config)
        else:  # RESTRICTED_ENTRY: presence in zone is the violation (if the person detection is trustworthy)
            from .association import Observation, person_visibility
            obs = {}
            for p in in_zone:
                ok, why = person_visibility(p, rule, frame)
                obs[p.track_id] = (Observation(Obs.ABSENT, p.conf, "explicit", "in restricted zone")
                                   if ok else Observation(Obs.UNKNOWN, 0.0, "not_visible", why))

        for p in in_zone:
            o = obs[p.track_id]
            key = (frame.camera_id, p.track_id, zone.zone_id, rule.id)
            obs_log.append({"track_id": p.track_id, "zone": zone.zone_id, "rule": rule.id,
                            "observation": o.kind.value, "basis": o.basis, "confidence": o.confidence, "reason": o.reason})
            if o.kind == Obs.UNKNOWN:
                self.sm.observe(key, rule, None)
                continue
            s = Sample(frame.frame_id, frame.ts, o.kind == Obs.ABSENT, o.confidence, o.basis, p.bbox)
            conf = self.sm.observe(key, rule, s)
            if conf:
                events.append(self._event(frame, zone, rule, p.track_id, conf))
        # a tracked person seen OUTSIDE the zone counts as compliant for clearing existing episodes only
        for p in out_zone:
            key = (frame.camera_id, p.track_id, zone.zone_id, rule.id)
            if self.sm.has(key):
                self.sm.observe(key, rule, Sample(frame.frame_id, frame.ts, False, 1.0, "outside_zone", p.bbox), create=False)

    def _hazard(self, frame, zone: Zone, rule: Rule, events, obs_log):
        for hc in rule.hazard_classes:
            hits = [d for d in frame.hazards + frame.ppe if d.cls == hc and d.conf >= rule.pos_conf
                    and zone.contains(foot_point(d.bbox), frame.width, frame.height)]
            key = (frame.camera_id, f"hazard:{hc}", zone.zone_id, rule.id)
            if hits:
                best = max(hits, key=lambda d: d.conf)
                obs_log.append({"hazard": hc, "zone": zone.zone_id, "rule": rule.id, "observation": "absent",
                                "basis": "explicit", "confidence": best.conf})
                conf = self.sm.observe(key, rule, Sample(frame.frame_id, frame.ts, True, best.conf, "explicit", best.bbox))
                if conf:
                    events.append(self._event(frame, zone, rule, None, conf, hazard_class=hc))
            elif self.sm.has(key):
                self.sm.observe(key, rule, Sample(frame.frame_id, frame.ts, False, 1.0, "clear", (0, 0, 0, 0)), create=False)

    # ---- event construction -----------------------------------------------------------------
    def _event(self, frame: FrameInput, zone: Zone, rule: Rule, track_id: Optional[str], c: Confirmation,
               hazard_class: Optional[str] = None) -> dict:
        viol = c.violating
        inferred = sum(1 for s in viol if s.basis == "inferred")
        mean_conf = sum(s.conf for s in viol) / len(viol)
        subject = track_id or f"hazard:{hazard_class}"
        idem = f"{frame.camera_id}|{subject}|{zone.zone_id}|{rule.id}|{c.first_seen_frame}"
        pick = sorted({viol[0].frame_id, viol[len(viol) // 2].frame_id, c.confirmed_frame})
        review_reasons = []
        if mean_conf < rule.review_below:
            review_reasons.append(f"mean confidence {mean_conf:.2f} below {rule.review_below}")
        if inferred > len(viol) / 2:
            review_reasons.append("absence was inferred rather than explicitly detected")
        ev = {
            "schema_version": SCHEMA_VERSION,
            "event_id": str(uuid.uuid5(_NS, idem)),
            "idempotency_key": idem,
            "event_type": EVENT_TYPE[rule.type],
            "rule_id": rule.id,
            "rule_description": rule.description,
            "camera_id": frame.camera_id,
            "track_id": track_id,
            "zone": zone.zone_id,
            "missing_ppe": [rule.item] if rule.type == PPE_REQUIRED else [],
            "hazard_class": hazard_class,
            "confidence": round(mean_conf, 3),
            "evidence_basis": {"explicit": len(viol) - inferred, "inferred": inferred},
            "needs_review": bool(review_reasons),
            "review_reasons": review_reasons,
            "window": {"n": rule.confirm_n, "m": rule.confirm_m, "violating": len(viol), "observed": c.window_len},
            "first_seen_frame": c.first_seen_frame,
            "confirmed_frame": c.confirmed_frame,
            "evidence_frames": pick,
            "bbox": [round(v, 1) for v in c.bbox],
            "first_seen_at": _iso(c.first_seen_ts),
            "timestamp": frame.timestamp,
            "model_version": frame.model_version,
            "config_version": self.config.version,
        }
        return ev

    def stats(self) -> dict:
        return {"frames": self.frames, "events_emitted": self.events_emitted, "active_states": len(self.sm.states),
                "suppressed_duplicates": self.sm.suppressed}


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
