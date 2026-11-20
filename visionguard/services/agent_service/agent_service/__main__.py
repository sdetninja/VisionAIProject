"""Assessment service worker: follows M3's bus and writes one assessment per incident version back to M3.

  python -m agent_service --bus ../incident_service/data/bus/events.jsonl --m3-url http://127.0.0.1:8003 \\
      --judge ../../../milestone0/runs/judge/thresholds.yaml [--panel anthropic] [--port 8004] [--follow]

--port also serves the small HTTP API (health, /metrics, SOP search, re-assess) from the same process, so the ViT
models are loaded once.
"""
import argparse
import json
import logging
import os
import threading
import time

from .factory import build


def serve_api(svc, port, host="127.0.0.1"):
    import uvicorn
    from .app import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(svc), host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="api").start()
    logging.info("assessment API on http://%s:%d", host, port)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", required=True, help="M3 JSONL bus file to consume")
    ap.add_argument("--m3-url", default="http://127.0.0.1:8003", help="M3 API (incidents, evidence, assessments)")
    ap.add_argument("--kb", default="knowledge_base")
    ap.add_argument("--risk-config")
    ap.add_argument("--judge", help="thresholds.yaml from milestone0/scripts/12_eval_vit_judges.py (ViT checkpoints + thresholds)")
    ap.add_argument("--panel", default="none", choices=["none", "anthropic"], help="LLM panel for fire/smoke (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--llm", default="template", choices=["template", "anthropic"], help="explanation writer")
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--port", type=int, default=0, help="also serve the HTTP API (+ /metrics) on this port; 0 = off")
    ap.add_argument("--host", default="127.0.0.1", help="API bind address (0.0.0.0 in a container)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    svc = build(a.kb, a.risk_config, a.llm, a.m3_url, a.judge, a.panel)
    if a.port:
        serve_api(svc, a.port, a.host)
    if a.follow and not os.path.exists(a.bus):                  # M3 creates the bus file on its first event
        logging.info("waiting for %s to appear (it is created by the incident service)", a.bus)
        while not os.path.exists(a.bus):
            time.sleep(0.5)
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
                    print(f"{r['incident_id']}: judge {r['verdict']} ({r['judge_mode']}) | {r['severity']} ({r['risk_score']}) "
                          f"grounded={r['grounded']} | {r.get('judge_reason', '')}", flush=True)
            except Exception:
                logging.exception("failed message")
    print(svc.counters, svc.verdicts)


if __name__ == "__main__":
    main()
