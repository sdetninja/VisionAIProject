"""Turn M3 reviewer verdicts (GET /api/labels) into (a) per-rule precision stats and (b) work queues.
SAFETY RULE: verdicts never go straight into training. A PPE false positive could mean the model missed a helmet
OR the rule logic was wrong, so those frames go to a human RELABEL queue. Only hazard false positives (e.g. welding
glare, steam) become hard negatives, because 'no fire/smoke in this image' is exactly what the reviewer asserted."""
from __future__ import annotations
from collections import defaultdict

HAZARD = {"HAZARD_DETECTED"}


def rule_stats(labels: list) -> dict:
    s = defaultdict(lambda: {"tp": 0, "fp": 0})
    for l in labels:
        s[l.get("rule_id") or "UNKNOWN"]["tp" if l["label"] == "true_positive" else "fp"] += 1
    out = {}
    for r, v in s.items():
        n = v["tp"] + v["fp"]
        out[r] = {**v, "n": n, "precision": round(v["tp"] / n, 3) if n else None}
    return out


def suggest_threshold_changes(stats: dict, min_n=20, low=0.7) -> list:
    return [f"{r}: precision {v['precision']} over {v['n']} reviewed - raise confirmation window (N of M) or detector threshold"
            for r, v in sorted(stats.items()) if v["n"] >= min_n and v["precision"] is not None and v["precision"] < low]


def build_queues(labels: list) -> dict:
    relabel, hard_neg, confirmed, seen = [], [], [], set()
    for l in labels:
        uris = l.get("evidence_uris") or []
        if not uris:
            continue
        ev = l["event_payload"]
        key = (l["incident_id"], tuple(uris))
        if key in seen:
            continue
        seen.add(key)
        item = {"incident_id": l["incident_id"], "evidence_uris": uris, "rule_id": l.get("rule_id"), "event_type": ev.get("event_type"),
                "camera_id": ev.get("camera_id"), "reviewer": l.get("reviewer")}
        if l["label"] == "false_positive":
            if ev.get("event_type") in HAZARD:
                hard_neg.append({**item, "hazard_class": ev.get("hazard_class")})
            else:
                relabel.append({**item, "reason": "reviewer says PPE violation was wrong: re-annotate this frame"})
        else:
            confirmed.append({**item, "reason": "confirmed violation: candidate for the own-camera test/val set after annotation"})
    return {"relabel": relabel, "hard_negatives": hard_neg, "confirmed": confirmed}
