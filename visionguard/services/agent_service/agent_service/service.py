from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .pipeline import analyze_incident

TOPIC_ANALYZED = "incident.analyzed.v1"
HANDLED_TOPICS = ("incident.created.v1", "incident.updated.v1")


class AnalysisStore:
    def __init__(self, path=":memory:"):
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)     # fresh volume: parent dir may not exist
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.execute("CREATE TABLE IF NOT EXISTS analyses(incident_id TEXT, version TEXT, payload TEXT, "
                       "PRIMARY KEY(incident_id, version))")
        self.c.execute("CREATE TABLE IF NOT EXISTS seen(incident_id TEXT, camera_id TEXT, zone TEXT, event_type TEXT, "
                       "at REAL, PRIMARY KEY(incident_id))")
        self.l = threading.Lock()

    def get(self, iid, version=None):
        with self.l:
            r = (self.c.execute("SELECT payload FROM analyses WHERE incident_id=? AND version=?", (iid, version)) if version is not None
                 else self.c.execute("SELECT payload FROM analyses WHERE incident_id=? ORDER BY rowid DESC LIMIT 1", (iid,))).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, a):
        with self.l:
            self.c.execute("INSERT OR REPLACE INTO analyses VALUES(?,?,?)", (a["incident_id"], a["incident_version"] or "", json.dumps(a)))

    def similar_count(self, inc, hours, now_ts):
        """Incidents on same camera+zone+type in the window, excluding this one (repeat-pattern signal)."""
        with self.l:
            return self.c.execute("SELECT COUNT(*) FROM seen WHERE camera_id=? AND zone=? AND event_type=? AND at>=? AND incident_id!=?",
                                  (inc["camera_id"], inc["zone"], inc["event_type"], now_ts - hours * 3600, inc["incident_id"])).fetchone()[0]

    def remember(self, inc, ts):
        with self.l:
            self.c.execute("INSERT OR REPLACE INTO seen VALUES(?,?,?,?,?)", (inc["incident_id"], inc["camera_id"], inc["zone"], inc["event_type"], ts))


def _ts(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return datetime.now(timezone.utc).timestamp()


class AgentService:
    def __init__(self, risk_cfg, retriever, llm, store: AnalysisStore, bus=None):
        self.risk_cfg, self.retriever, self.llm, self.store, self.bus = risk_cfg, retriever, llm, store, bus
        self.counters = {"analyzed": 0, "cached": 0, "skipped": 0, "fallback": 0}

    def analyze(self, inc: dict, force=False) -> dict:
        if not inc.get("incident_id"):
            raise ValueError("incident_id required")
        ver = inc.get("updated_at") or ""
        if not force:
            hit = self.store.get(inc["incident_id"], ver)
            if hit:
                self.counters["cached"] += 1
                return hit
        ts = _ts(inc.get("first_event_time") or "")
        n = self.store.similar_count(inc, self.risk_cfg["repeat"]["window_hours"], ts)
        a = analyze_incident(inc, risk_cfg=self.risk_cfg, retriever=self.retriever, llm=self.llm, recent_similar=n)
        self.store.remember(inc, ts)
        self.store.put(a)
        self.counters["analyzed"] += 1
        self.counters["fallback"] += a["explanation_mode"] == "template_fallback"
        if self.bus:
            self.bus.publish(TOPIC_ANALYZED, a["incident_id"], a)
        return a

    def handle_message(self, msg: dict):
        """msg is a bus record {'topic','key','payload'} from M3's outbox. Only created/updated are analysed."""
        if msg.get("topic") not in HANDLED_TOPICS:
            self.counters["skipped"] += 1
            return None
        return self.analyze(msg["payload"]["incident"])
