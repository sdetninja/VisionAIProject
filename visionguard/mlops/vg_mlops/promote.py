"""Orchestrates gates + registry. Policy:
 candidate -> staging : automatic if gates pass
 staging -> production: gates re-run + human approver + minimum staging soak (force is audited)"""
from __future__ import annotations
from .gates import GateConfig, evaluate_candidate
from .registry import Registry, RegistryError


def to_staging(reg: Registry, name, version, cfg: GateConfig, actor="ci"):
    c = reg.get(name, version)
    if c["stage"] != "candidate":
        raise RegistryError(f"v{version} is {c['stage']}, expected candidate")
    prod = reg.current(name)
    res = evaluate_candidate(c["metrics"], prod["metrics"] if prod else None, cfg, c["test_hash"], reg.frozen_hash(name))
    if res.passed:
        reg.set_stage(name, version, "staging", actor, "gates passed")
    else:
        reg._ev(name, version, "gate_failed", actor, "; ".join(f"{n}: {m}" for n, ok, m in res.failures())[:900])
    return res


def to_production(reg: Registry, name, version, cfg: GateConfig, approver, force=False, reason="", now=None):
    if not approver:
        raise RegistryError("an approver is required for production")
    c = reg.get(name, version)
    if c["stage"] != "staging":
        raise RegistryError(f"v{version} is {c['stage']}, must be in staging first")
    prod = reg.current(name)
    res = evaluate_candidate(c["metrics"], prod["metrics"] if prod else None, cfg, c["test_hash"], reg.frozen_hash(name))
    if not res.passed:
        raise RegistryError("gates failed on re-check:\n" + res.report())      # force never overrides failed metrics gates
    since, now = reg.staged_since(name, version), (now if now is not None else reg.clock())
    soak_h = (now - since) / 3600 if since else 0
    if soak_h < cfg.min_staging_hours:
        if not (force and reason):
            raise RegistryError(f"staging soak {soak_h:.1f}h < {cfg.min_staging_hours}h (force requires a reason)")
        reg._ev(name, version, "soak_override", approver, reason)
    reg.set_stage(name, version, "production", approver, f"approved; soak {soak_h:.1f}h")
    return res
