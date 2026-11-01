"""Promotion gates. A candidate is promoted only if ALL checks pass. Pure functions: easy to audit and test.
Metrics shape: {"per_class": {name: {"recall","precision","map50","n"}}, "latency_ms": float}"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class GateConfig:
    targets: dict                         # {class: {recall, map50}} from milestone0/configs/targets.yaml (one group)
    min_test_instances: int = 30
    required_classes: list = None         # classes the enabled rules depend on; default = all in targets
    critical_classes: tuple = ("helmet", "no_helmet", "fire", "smoke", "person")
    max_recall_drop: float = 0.03         # vs production, any required class
    max_recall_drop_critical: float = 0.01
    max_map50_drop: float = 0.03
    latency_budget_ms: float = 80.0
    max_latency_regression: float = 1.25  # x production latency
    min_staging_hours: float = 24.0


@dataclass
class GateResult:
    passed: bool
    checks: list = field(default_factory=list)       # (name, ok, message)

    def failures(self):
        return [c for c in self.checks if not c[1]]

    def report(self):
        return "\n".join(f"[{'PASS' if ok else 'FAIL'}] {n}: {m}" for n, ok, m in self.checks)


def evaluate_candidate(cand: dict, prod: dict | None, cfg: GateConfig, cand_test_hash: str, frozen_test_hash: str | None) -> GateResult:
    checks = []
    add = lambda n, ok, m: checks.append((n, bool(ok), m))

    add("frozen_test_set", frozen_test_hash is not None and cand_test_hash == frozen_test_hash,
        "evaluated on the frozen test set" if cand_test_hash == frozen_test_hash and frozen_test_hash else
        f"candidate test hash {str(cand_test_hash)[:10]} != frozen {str(frozen_test_hash)[:10]} (metrics not comparable)")

    req = cfg.required_classes if cfg.required_classes is not None else list(cfg.targets)
    pc = cand.get("per_class", {})
    for c in req:
        t, m = cfg.targets.get(c), pc.get(c)
        if m is None:
            add(f"target:{c}", False, "class missing from evaluation")
        elif m.get("n", 0) < cfg.min_test_instances:
            add(f"target:{c}", False, f"only {m.get('n', 0)} test instances (<{cfg.min_test_instances}); UNRELIABLE")
        elif t and (m["recall"] < t["recall"] or m["map50"] < t["map50"]):
            add(f"target:{c}", False, f"recall {m['recall']:.2f}/{t['recall']} map50 {m['map50']:.2f}/{t['map50']}")
        else:
            add(f"target:{c}", True, f"recall {m['recall']:.2f} map50 {m['map50']:.2f}")

    if prod:
        pp = prod.get("per_class", {})
        for c in req:
            if c in pp and c in pc and pp[c].get("n", 0) >= cfg.min_test_instances:
                tol = cfg.max_recall_drop_critical if c in cfg.critical_classes else cfg.max_recall_drop
                dr, dm = pp[c]["recall"] - pc[c]["recall"], pp[c]["map50"] - pc[c]["map50"]
                ok = dr <= tol and dm <= cfg.max_map50_drop
                add(f"no_regression:{c}", ok, f"recall {pp[c]['recall']:.2f}->{pc[c]['recall']:.2f} (tol {tol}), map50 {pp[c]['map50']:.2f}->{pc[c]['map50']:.2f}")
        pl = prod.get("latency_ms")
        if pl:
            add("latency_regression", cand.get("latency_ms", 1e9) <= pl * cfg.max_latency_regression,
                f"{cand.get('latency_ms')}ms vs prod {pl}ms (max x{cfg.max_latency_regression})")
    add("latency_budget", cand.get("latency_ms", 1e9) <= cfg.latency_budget_ms, f"{cand.get('latency_ms')}ms (budget {cfg.latency_budget_ms}ms)")
    return GateResult(all(c[1] for c in checks), checks)
