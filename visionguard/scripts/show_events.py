"""Live, readable view of Module 2 events for demos (tails rule_engine/out/events.jsonl like `tail -f`).

  $PY visionguard/scripts/show_events.py                      # default file, waits for it to appear
  $PY visionguard/scripts/show_events.py path/to/events.jsonl
  $PY visionguard/scripts/show_events.py --all                # also replay events already in the file

One line per confirmed event: time, type, what, confidence, how it was decided, and whether it needs human review.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

DEFAULT = os.path.join(os.path.dirname(__file__), "..", "services", "rule_engine", "out", "events.jsonl")
RED, YEL, CYA, DIM, BOLD, OFF = "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"


def fmt(e: dict) -> str:
    t = e.get("timestamp", "")
    try:
        t = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        pass
    kind = e.get("event_type", "?")
    if kind == "HAZARD_DETECTED":
        head, what = f"{RED}{BOLD}HAZARD {OFF}", f"{RED}{e.get('hazard_class', '?').upper()}{OFF}"
    elif kind == "PPE_VIOLATION":
        head, what = f"{YEL}{BOLD}PPE    {OFF}", f"missing {', '.join(e.get('missing_ppe') or ['?'])}"
    else:
        head, what = f"{CYA}{BOLD}{kind[:7]:<7}{OFF}", ""
    b = e.get("evidence_basis") or {}
    basis = "explicit" if b.get("explicit", 0) >= b.get("inferred", 0) else "inferred"
    w = e.get("window") or {}
    who = (e.get("track_id") or "").split("-")[-1] or "-"
    review = f"{YEL}needs review{OFF}" if e.get("needs_review") else "auto"
    return (f"{DIM}{t}{OFF}  {head} {what:<28} person {who:<5} conf {e.get('confidence', 0):.2f}  "
            f"{basis} {w.get('violating', '?')}/{w.get('m', '?')} frames  {DIM}{e.get('rule_id', '')}{OFF}  {review}")


def follow(path: str, from_start: bool):
    if not os.path.exists(path):          # a file that appears while we wait is all new: read it from the start
        from_start = True
    while not os.path.exists(path):
        print(f"{DIM}waiting for {path} (start Module 2)...{OFF}", end="\r", flush=True)
        time.sleep(0.5)
    with open(path) as f:
        if not from_start:
            f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=DEFAULT)
    ap.add_argument("--all", action="store_true", help="show events already in the file too")
    a = ap.parse_args()
    print(f"{BOLD}VisionGuard events{OFF}  {DIM}{os.path.abspath(a.path)}{OFF}")
    try:
        for e in follow(a.path, a.all):
            print(fmt(e), flush=True)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
