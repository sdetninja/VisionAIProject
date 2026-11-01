"""Offline / tail runner.

  python -m rule_engine --config configs/rules.example.yaml \
      --input ../vision_pipeline/out/results/CAM_001.jsonl --output out/events.jsonl [--follow]
"""
import argparse
import json
import logging

from .adapters import JsonlEventWriter, read_jsonl
from .config import load_config
from .engine import RuleEngine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--input", required=True, help="M1 TrackResult JSONL")
    ap.add_argument("--output", default=None, help="append events as JSONL")
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--metrics-port", type=int, default=0, help="serve /metrics and /healthz on this port (M9); 0 = off")
    a = ap.parse_args()
    logging.basicConfig(level="INFO")
    eng = RuleEngine(load_config(a.config))
    if a.metrics_port:
        try:
            from vg_obs import REGISTRY, serve_metrics, setup_logging
            from vg_obs.collectors import register_rule_engine; register_rule_engine(eng)
            setup_logging("rule_engine")
            serve_metrics(a.metrics_port)
        except ImportError:
            logging.warning("--metrics-port ignored: vg_obs (ops package) not installed")
    write = JsonlEventWriter(a.output) if a.output else None
    for rec in read_jsonl(a.input, follow=a.follow):
        for ev in eng.process(rec):
            print(json.dumps(ev))
            if write:
                write(ev)
    print(eng.stats())


if __name__ == "__main__":
    main()
