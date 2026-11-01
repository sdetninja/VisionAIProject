"""python -m notification_service --config configs/notify.example.yaml --bus FILE [--bus FILE ...] [--follow]
Reads M3 (incident.*) and M4 (incident.analyzed) JSONL bus files."""
import argparse, json, logging, time
from .config import Config
from .factory import build


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--bus", action="append", required=True)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--metrics-port", type=int, default=0, help="serve /metrics and /healthz on this port (M9); 0 = off")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    svc = build(Config.load(a.config))
    if a.metrics_port:
        try:
            from vg_obs import REGISTRY, serve_metrics, setup_logging
            from vg_obs.collectors import register_notification; register_notification(svc)
            setup_logging("notification_worker")
            serve_metrics(a.metrics_port)
        except ImportError:
            logging.warning("--metrics-port ignored: vg_obs (ops package) not installed")
    files = [open(p) for p in a.bus]
    while True:
        got = False
        for f in files:
            for line in f:
                got = True
                try:
                    svc.handle_message(json.loads(line))
                except Exception:
                    logging.exception("bad message")
        svc.tick()
        if not a.follow:
            break
        if not got:
            time.sleep(1)
    print(svc.stats())


if __name__ == "__main__":
    main()
