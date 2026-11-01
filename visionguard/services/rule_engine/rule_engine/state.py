"""Debouncing state machine: N-of-M confirmation, clearing, cooldown, spatial de-duplication.

Per (camera, subject, zone, rule):
  OBSERVING --(>=N violating of last M)--> CONFIRMED  (event emitted exactly once per episode)
  CONFIRMED --(clear_frames consecutive compliant)--> OBSERVING (+cooldown after emission)
UNKNOWN observations never enter the window.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .geometry import iou


@dataclass
class Sample:
    frame_id: int
    ts: float
    violating: bool
    conf: float
    basis: str
    bbox: tuple


@dataclass
class RuleState:
    n: int
    m: int
    window: deque = field(default_factory=deque)
    confirmed: bool = False
    compliant_streak: int = 0
    cooldown_until: float = 0.0
    last_seen_ts: float = 0.0
    last_bbox: tuple = (0, 0, 0, 0)


@dataclass
class Confirmation:
    first_seen_frame: int
    first_seen_ts: float
    confirmed_frame: int
    confirmed_ts: float
    violating: list          # Samples
    window_len: int
    bbox: tuple


class StateMachine:
    def __init__(self, dedup_iou: float = 0.30, recent_keep: int = 500):
        self.states: dict = {}
        self.recent: deque = deque(maxlen=recent_keep)   # (ts, dedup_key, bbox, cooldown_s)
        self.dedup_iou = dedup_iou
        self.suppressed = 0

    def observe(self, key, rule, sample: Optional[Sample], *, create: bool = True) -> Optional[Confirmation]:
        """sample=None means UNKNOWN (ignored except for bookkeeping). Returns a Confirmation when a new
        episode is confirmed on this call."""
        st = self.states.get(key)
        if st is None:
            if sample is None or not sample.violating or not create:
                return None
            st = self.states[key] = RuleState(rule.confirm_n, rule.confirm_m)
        if sample is None:
            return None
        st.last_seen_ts = sample.ts
        st.last_bbox = sample.bbox

        if not sample.violating:
            st.compliant_streak += 1
            st.window.append(sample)
            while len(st.window) > st.m:
                st.window.popleft()
            if st.confirmed and st.compliant_streak >= rule.clear_frames:
                st.confirmed = False
                st.window.clear()
            return None

        st.compliant_streak = 0
        st.window.append(sample)
        while len(st.window) > st.m:
            st.window.popleft()
        if st.confirmed:
            return None
        viol = [s for s in st.window if s.violating]
        if len(viol) < st.n or sample.ts < st.cooldown_until:
            return None
        dkey = (key[0], key[2], key[3])   # camera, zone, rule
        for ts, k, bb, cd in self.recent:
            if k == dkey and sample.ts - ts < cd and iou(bb, sample.bbox) >= self.dedup_iou:
                self.suppressed += 1       # same spot, same rule, recently reported (e.g. after a track-ID switch)
                st.confirmed = True        # treat as already-reported episode; clears normally
                return None
        st.confirmed = True
        st.cooldown_until = sample.ts + rule.cooldown_s
        self.recent.append((sample.ts, dkey, sample.bbox, rule.cooldown_s))
        return Confirmation(viol[0].frame_id, viol[0].ts, sample.frame_id, sample.ts, viol, len(st.window), sample.bbox)

    def has(self, key) -> bool:
        return key in self.states

    def expire(self, now_ts: float, ttl: float) -> int:
        dead = [k for k, s in self.states.items()
                if now_ts - s.last_seen_ts > ttl and now_ts >= s.cooldown_until]   # keep cooldowns alive
        for k in dead:
            del self.states[k]
        return len(dead)

    def retain_only(self, valid_rule_keys: set) -> None:
        """After a hot reload drop states of rules/zones that no longer exist."""
        for k in [k for k in self.states if (k[2], k[3]) not in valid_rule_keys]:
            del self.states[k]
