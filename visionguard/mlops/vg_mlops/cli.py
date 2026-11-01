"""python -m vg_mlops <command>. State dir: --registry (default ./registry)."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import yaml

from . import drift as D, feedback as F
from .datasets import dataset_summary, leakage_report
from .gates import GateConfig
from .promote import to_production, to_staging
from .registry import Registry, RegistryError


def _gate_cfg(a) -> GateConfig:
    t = yaml.safe_load(open(a.targets))
    extra = yaml.safe_load(open(a.gate_config)) if a.gate_config else {}
    return GateConfig(targets=t[a.group], min_test_instances=t.get("min_test_instances_per_class", 30), **extra)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vg_mlops")
    ap.add_argument("--registry", default="registry")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("audit-data", help="hash splits + leakage check; writes dataset_summary.json")
    s.add_argument("--data-root", required=True); s.add_argument("--out", default="dataset_summary.json")

    s = sub.add_parser("freeze-test"); s.add_argument("--name", required=True); s.add_argument("--data-root", required=True); s.add_argument("--actor", required=True)

    s = sub.add_parser("register"); s.add_argument("--name", required=True); s.add_argument("--weights", required=True)
    s.add_argument("--metrics", required=True, help="metrics JSON (see evaluate.py)"); s.add_argument("--data-root", required=True)
    s.add_argument("--actor", default="ci"); s.add_argument("--notes", default="")

    for c in ("stage", "promote"):
        s = sub.add_parser(c); s.add_argument("--name", required=True); s.add_argument("--version", type=int, required=True)
        s.add_argument("--targets", default="../milestone0/configs/targets.yaml"); s.add_argument("--group", choices=["ppe", "hazard"], required=True)
        s.add_argument("--gate-config")
        if c == "promote":
            s.add_argument("--approver", required=True); s.add_argument("--force", action="store_true"); s.add_argument("--reason", default="")

    s = sub.add_parser("rollback"); s.add_argument("--name", required=True); s.add_argument("--actor", required=True); s.add_argument("--reason", required=True)
    s = sub.add_parser("status"); s.add_argument("--name")
    s = sub.add_parser("feedback"); s.add_argument("--labels", required=True, help="JSON from GET /api/labels"); s.add_argument("--out", default="feedback_out")
    s = sub.add_parser("drift"); s.add_argument("--baseline", required=True); s.add_argument("--current", required=True)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "audit-data":
            d = dataset_summary(a.data_root)
            leak = leakage_report(d["manifests"])
            out = {k: v for k, v in d.items() if k != "manifests"}
            out["leakage"] = leak
            Path(a.out).write_text(json.dumps(out, indent=2))
            print(json.dumps(out["counts"]), "dataset", out["dataset_hash"][:12], "leakage_ok=", leak["ok"])
            return 0 if leak["ok"] else 2
        if a.cmd == "feedback":
            labels = json.load(open(a.labels))
            st = F.rule_stats(labels)
            q = F.build_queues(labels)
            out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
            for k, v in q.items():
                (out / f"{k}.json").write_text(json.dumps(v, indent=2))
            (out / "rule_stats.json").write_text(json.dumps(st, indent=2))
            for line in F.suggest_threshold_changes(st):
                print("SUGGEST:", line)
            print({k: len(v) for k, v in q.items()}, "->", out)
            return 0
        if a.cmd == "drift":
            r = D.drift_report(json.load(open(a.baseline)), json.load(open(a.current)))
            print(json.dumps(r, indent=2))
            return 3 if r["needs_attention"] else 0
        reg = Registry(a.registry)
        if a.cmd == "freeze-test":
            h = dataset_summary(a.data_root)["hashes"]["test"]
            reg.freeze_test(a.name, h, a.actor); print("frozen", h[:12])
        elif a.cmd == "register":
            d = dataset_summary(a.data_root)
            if not leakage_report(d["manifests"])["ok"]:
                print("refusing to register: dataset leakage detected (run audit-data)", file=sys.stderr); return 2
            v = reg.register(a.name, a.weights, json.load(open(a.metrics)), d["dataset_hash"], d["hashes"]["test"], a.actor, notes=a.notes)
            print(f"registered {a.name} v{v} (candidate)")
        elif a.cmd == "stage":
            r = to_staging(reg, a.name, a.version, _gate_cfg(a)); print(r.report()); return 0 if r.passed else 1
        elif a.cmd == "promote":
            r = to_production(reg, a.name, a.version, _gate_cfg(a), a.approver, a.force, a.reason); print(r.report()); print("PROMOTED")
        elif a.cmd == "rollback":
            print("rolled back to v", reg.rollback(a.name, a.actor, a.reason))
        elif a.cmd == "status":
            for m in reg.list(a.name): print(m["name"], f"v{m['version']}", m["stage"], m["dataset_hash"][:10])
    except RegistryError as e:
        print("ERROR:", e, file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
