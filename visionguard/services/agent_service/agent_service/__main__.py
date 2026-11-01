"""python -m agent_service --bus ../incident_service/data/bus/events.jsonl --kb knowledge_base [--follow]"""
import argparse, json, logging, time
from .bus import JsonlPublisher
from .factory import build


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", required=True, help="M3 JSONL bus file to consume")
    ap.add_argument("--kb", default="knowledge_base")
    ap.add_argument("--risk-config")
    ap.add_argument("--out", default="data/analyses.jsonl")
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--llm", default="template", choices=["template", "anthropic"])
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--metrics-port", type=int, default=0, help="serve /metrics and /healthz on this port (M9); 0 = off")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    svc = build(a.kb, a.risk_config, a.llm, a.db, JsonlPublisher(a.out))
    if a.metrics_port:
        try:
            from vg_obs import REGISTRY, serve_metrics, setup_logging
            from vg_obs.collectors import register_agent; register_agent(svc)
            setup_logging("agent_worker")
            serve_metrics(a.metrics_port)
        except ImportError:
            logging.warning("--metrics-port ignored: vg_obs (ops package) not installed")
    with open(a.bus) as f:
        while True:
            line = f.readline()
            if not line:
                if not a.follow:
                    break
                time.sleep(0.5)
                continue
            try:
                r = svc.handle_message(json.loads(line))
                if r:
                    print(f"{r['incident_id']}: {r['severity']} ({r['risk_score']}) grounded={r['grounded']} mode={r['explanation_mode']}")
            except Exception:
                logging.exception("failed message")
    print(svc.counters)


if __name__ == "__main__":
    main()
