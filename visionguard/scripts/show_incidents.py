"""Readable view of Module 3 incidents (talks to the M3 API, default http://127.0.0.1:8003).

  $PY visionguard/scripts/show_incidents.py                 # list incidents
  $PY visionguard/scripts/show_incidents.py INC-000001      # one incident: events, evidence files, history
  $PY visionguard/scripts/show_incidents.py --watch         # refresh the list every 2 s (Ctrl+C to stop)
  $PY visionguard/scripts/show_incidents.py --url http://host:8003
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

EVIDENCE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "services", "incident_service", "data", "evidence"))


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def what(i):
    return (i.get("hazard_class") or "").upper() or "missing " + ", ".join(i.get("missing_ppe") or ["?"])


def show_list(base):
    d = get(f"{base}/incidents?limit=200")
    print(f"{d['total']} incident(s)   {time.strftime('%H:%M:%S')}")
    print(f"{'ID':<11} {'TYPE':<16} {'WHAT':<22} {'PERSON':<7} {'STATUS':<15} {'CONF':>5} {'EVENTS':>6}  EVIDENCE   REVIEW")
    for i in d["items"]:
        who = (i.get("track_id") or "-").split("-")[-1]
        print(f"{i['incident_id']:<11} {i['event_type']:<16} {what(i):<22} {who:<7} {i['status']:<15} "
              f"{i['confidence']:>5.2f} {i['event_count']:>6}  {i['evidence_status']:<3} {len(i['evidence_uris'])} img  "
              f"{'needs review' if i['needs_review'] else ''}")


def show_one(base, iid):
    d = get(f"{base}/incidents/{iid}")
    print(f"{d['incident_id']}  {d['event_type']}  {what(d)}  status={d['status']}  camera={d['camera_id']} zone={d['zone']}")
    print(f"  person {d.get('track_id') or '-'}  confidence {d['confidence']}  needs_review={d['needs_review']}  rules {d['rule_ids']}")
    print(f"  first {d['first_event_time']}  last {d['last_event_time']}  events {d['event_count']}")
    for e in d.get("events", []):
        b = e.get("evidence_basis", {})
        print(f"  event {e['rule_id']:<9} frames {e['evidence_frames']}  basis explicit={b.get('explicit')} inferred={b.get('inferred')}"
              f"  {'; '.join(e.get('review_reasons') or [])}")
    print("  evidence files:")
    for u in d["evidence_uris"]:
        print("   ", os.path.join(EVIDENCE_DIR, u.split("://", 1)[1]))
    print("  history:")
    for h in d.get("history", []):
        print(f"    {h['at']}  {h['actor']:<8} {h['from_status'] or '-'} -> {h['to_status']}  {h.get('comment') or ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("incident_id", nargs="?")
    ap.add_argument("--url", default=os.environ.get("VG_INCIDENT_URL", "http://127.0.0.1:8003"))
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    try:
        if a.incident_id:
            show_one(a.url, a.incident_id)
        elif a.watch:
            while True:
                print("\033[2J\033[H", end="")
                show_list(a.url)
                time.sleep(2)
        else:
            show_list(a.url)
    except urllib.error.HTTPError as e:
        sys.exit(f"M3 answered {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError:
        sys.exit(f"M3 API not reachable at {a.url} - is the M3 API (uvicorn, port 8003) running?")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
