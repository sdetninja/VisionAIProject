"""Production drift signals from detection statistics (no labels needed). PSI > 0.2 is the usual 'investigate' line."""
from __future__ import annotations
import math


def histogram(values, bins=10, lo=0.0, hi=1.0):
    h = [0] * bins
    for v in values:
        i = min(bins - 1, max(0, int((v - lo) / (hi - lo) * bins)))
        h[i] += 1
    return h


def psi(expected: list, actual: list, eps=1e-4) -> float:
    te, ta = sum(expected) or 1, sum(actual) or 1
    s = 0.0
    for e, a in zip(expected, actual):
        pe, pa = max(e / te, eps), max(a / ta, eps)
        s += (pa - pe) * math.log(pa / pe)
    return s


def drift_report(baseline: dict, current: dict, psi_warn=0.1, psi_alert=0.2, min_samples=200) -> dict:
    """baseline/current: {"confidences": {class: [..]}, "counts_per_frame": {class: float}}"""
    out, alerts = {}, []
    for c, base in baseline.get("confidences", {}).items():
        cur = current.get("confidences", {}).get(c, [])
        if len(cur) < min_samples or len(base) < min_samples:
            out[c] = {"status": "insufficient_data", "n": len(cur)}
            continue
        p = psi(histogram(base), histogram(cur))
        st = "alert" if p >= psi_alert else "warn" if p >= psi_warn else "ok"
        out[c] = {"psi": round(p, 3), "status": st, "n": len(cur)}
        if st == "alert":
            alerts.append(f"{c}: confidence distribution shifted (PSI {p:.2f}) - lighting/camera change or model degradation")
    for c, b in baseline.get("counts_per_frame", {}).items():
        cur = current.get("counts_per_frame", {}).get(c)
        if cur is not None and b > 0 and (cur / b > 2.0 or cur / b < 0.5):
            alerts.append(f"{c}: detections/frame {b:.2f} -> {cur:.2f} (>2x change) - check camera view or false positives")
    return {"classes": out, "alerts": alerts, "needs_attention": bool(alerts)}
