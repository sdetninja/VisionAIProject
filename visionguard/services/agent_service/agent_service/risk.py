"""Deterministic risk scoring. The LLM never sets or changes severity - this module does."""
from __future__ import annotations
from dataclasses import dataclass, field
import yaml

DEFAULT = {
    "base": {"PPE_VIOLATION": 30, "RESTRICTED_AREA_ENTRY": 55, "HAZARD_DETECTED": 70},
    "ppe_weight": {"helmet": 25, "vest": 10, "gloves": 8, "goggles": 12, "mask": 6},
    "hazard_weight": {"fire": 30, "smoke": 15},
    "zone_multiplier": {},                # e.g. {"WELDING": 1.3, "FLOOR": 1.0}
    "repeat": {"window_hours": 24, "per_repeat": 8, "max_bonus": 24},
    "confidence_discount": 0.15,          # applied if needs_review (evidence is uncertain)
    "thresholds": {"MEDIUM": 30, "HIGH": 55, "CRITICAL": 80},
}


@dataclass
class RiskResult:
    score: int
    severity: str
    factors: list = field(default_factory=list)   # human-readable audit trail


def load_risk_config(path=None) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT.items()}
    if path:
        for k, v in (yaml.safe_load(open(path)) or {}).items():
            if k not in DEFAULT:
                raise ValueError(f"unknown risk config key {k}")
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) else v
    return cfg


def score_incident(inc: dict, cfg: dict, recent_similar: int = 0) -> RiskResult:
    f = []
    s = float(cfg["base"].get(inc["event_type"], 30))
    f.append(f"base {inc['event_type']}: {s:g}")
    for p in inc.get("missing_ppe") or []:
        w = cfg["ppe_weight"].get(p, 5)
        s += w
        f.append(f"missing {p}: +{w}")
    if inc.get("hazard_class"):
        w = cfg["hazard_weight"].get(inc["hazard_class"], 10)
        s += w
        f.append(f"hazard {inc['hazard_class']}: +{w}")
    m = cfg["zone_multiplier"].get(inc.get("zone"), 1.0)
    if m != 1.0:
        s *= m
        f.append(f"zone {inc['zone']} x{m}")
    if recent_similar > 0:
        r = cfg["repeat"]
        b = min(r["max_bonus"], recent_similar * r["per_repeat"])
        s += b
        f.append(f"{recent_similar} similar incident(s) in {r['window_hours']}h: +{b}")
    if inc.get("needs_review"):
        d = cfg["confidence_discount"]
        s *= 1 - d
        f.append(f"uncertain evidence (needs_review): x{1 - d:.2f}")
    score = max(0, min(100, round(s)))
    t = cfg["thresholds"]
    sev = "CRITICAL" if score >= t["CRITICAL"] else "HIGH" if score >= t["HIGH"] else "MEDIUM" if score >= t["MEDIUM"] else "LOW"
    return RiskResult(score, sev, f)
