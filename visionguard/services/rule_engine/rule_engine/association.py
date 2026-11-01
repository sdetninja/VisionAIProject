"""PPE-to-person association and per-frame observation (PRESENT / ABSENT / UNKNOWN).

Why three states: a missing detection is NOT proof of absence. We only call something ABSENT when
  (a) the model explicitly detected `no_<item>` on that person, or
  (b) absence is allowed to be inferred for that item AND the person is reliably visible.
Everything else is UNKNOWN and is ignored by the state machine (it neither confirms nor clears).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .config import EngineConfig, Rule, TOP_EDGE_ITEMS
from .geometry import area, center, point_in_box, region_box
from .models import Det, FrameInput, TrackObj


class Obs(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Observation:
    kind: Obs
    confidence: float
    basis: str            # explicit | inferred | detected | not_visible | conflict | low_conf
    reason: str = ""


def negative_names(item: str) -> tuple:
    base = {"goggles": ("goggle",)}.get(item, ())
    return tuple(f"no_{x}" for x in (item, *base))


def assign(persons: list, dets: list, region: tuple) -> dict:
    """Greedy one-to-one-ish assignment: each detection goes to ONE person whose expected region contains
    the detection centre; ties are broken by distance to the region centre (normalised by person height)."""
    out = {p.track_id: [] for p in persons}
    for d in dets:
        c = center(d.bbox)
        best, best_score = None, math.inf
        for p in persons:
            rb = region_box(p.bbox, region)
            if not point_in_box(c, rb):
                continue
            rc = center(rb)
            h = max(p.bbox[3] - p.bbox[1], 1.0)
            score = math.hypot(c[0] - rc[0], c[1] - rc[1]) / h
            if score < best_score:
                best, best_score = p, score
        if best is not None:
            out[best.track_id].append(d)
    return out


def person_visibility(p: TrackObj, rule: Rule, frame: FrameInput):
    """Return (is_reliable, reason)."""
    if p.conf < rule.min_person_conf:
        return False, "low_person_conf"
    if p.age_frames < rule.min_track_age:
        return False, "track_too_young"
    if frame.height and (p.bbox[3] - p.bbox[1]) < rule.min_person_height_frac * frame.height:
        return False, "person_too_small"
    if rule.item in TOP_EDGE_ITEMS and p.bbox[1] <= rule.edge_margin_px:
        return False, "head_cut_off_by_frame_edge"
    return True, ""


def evaluate_item(frame: FrameInput, persons: list, rule: Rule, cfg: EngineConfig) -> dict:
    """track_id -> Observation for one PPE item across all given persons in this frame."""
    item = rule.item
    region = cfg.regions.get(item, (-0.1, -0.1, 1.1, 1.1))
    pos = [d for d in frame.ppe if d.cls == item and d.conf >= rule.pos_conf]
    negs = set(negative_names(item))
    neg = [d for d in frame.ppe if d.cls in negs and d.conf >= rule.neg_conf]
    pos_by, neg_by = assign(persons, pos, region), assign(persons, neg, region)

    res = {}
    for p in persons:
        reliable, why = person_visibility(p, rule, frame)
        pp, nn = pos_by[p.track_id], neg_by[p.track_id]
        if pp and nn:
            kind = {"violating": Obs.ABSENT, "compliant": Obs.PRESENT, "unknown": Obs.UNKNOWN}[rule.on_conflict]
            res[p.track_id] = Observation(kind, max(d.conf for d in nn), "conflict", "present and no_ both detected")
        elif pp:
            res[p.track_id] = Observation(Obs.PRESENT, max(d.conf for d in pp), "detected")
        elif nn:
            res[p.track_id] = Observation(Obs.ABSENT, max(d.conf for d in nn), "explicit")
        elif rule.inferred_absence and reliable:
            # weaker evidence: scale by person confidence and cap, so inferred events trend towards human review
            res[p.track_id] = Observation(Obs.ABSENT, round(min(0.65, 0.7 * p.conf), 3), "inferred")
        else:
            res[p.track_id] = Observation(Obs.UNKNOWN, 0.0, "not_visible", why or "no detection, absence not inferable")
    return res
