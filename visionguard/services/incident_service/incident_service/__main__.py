"""python -m incident_service --config configs/incident.example.yaml --input events.jsonl [--follow]
Ingests M2 events from JSONL, publishes via outbox, prints a summary."""
import argparse
import logging

from .adapters import read_events_jsonl
from .config import Config
from .factory import build
from . import models as M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--input", required=True)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--metrics-port", type=int, default=0, help="serve /metrics and /healthz on this port (M9); 0 = off")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    svc, pub = build(Config.load(a.config))
    pub.start()
    if a.metrics_port:
        try:
            from vg_obs import REGISTRY, serve_metrics, setup_logging
            from vg_obs.collectors import register_incident; register_incident(svc)
            setup_logging("incident_worker")
            serve_metrics(a.metrics_port)
        except ImportError:
            logging.warning("--metrics-port ignored: vg_obs (ops package) not installed")
    try:
        for ev in read_events_jsonl(a.input, a.follow):
            try:
                svc.ingest(ev)
            except M.InvalidEvent as e:
                logging.warning("rejected event: %s", e)
    except KeyboardInterrupt:
        pass
    finally:
        pub.run_once()
        pub.stop()
    print(svc.stats())


if __name__ == "__main__":
    main()
