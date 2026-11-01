"""SQLite repository (dev/MVP). SQL is deliberately portable; a PostgreSQL repository with the same
public methods is the production target. All multi-row writes happen inside `transaction()`."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT UNIQUE,
  event_type TEXT NOT NULL, camera_id TEXT NOT NULL, zone TEXT NOT NULL,
  track_id TEXT, hazard_class TEXT,
  missing_ppe TEXT NOT NULL DEFAULT '[]', rule_ids TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0, confidence REAL NOT NULL DEFAULT 0,
  first_event_time TEXT NOT NULL, last_event_time TEXT NOT NULL, last_event_ts REAL NOT NULL,
  event_count INTEGER NOT NULL DEFAULT 1,
  evidence_uris TEXT NOT NULL DEFAULT '[]', thumbnail_uri TEXT, evidence_status TEXT NOT NULL DEFAULT 'none',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  reviewed_by TEXT, review_comment TEXT);
CREATE INDEX IF NOT EXISTS ix_inc_group ON incidents(camera_id, zone, track_id, hazard_class, event_type, status);
CREATE INDEX IF NOT EXISTS ix_inc_status ON incidents(status, created_at);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, incident_id TEXT NOT NULL,
  payload TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, at TEXT NOT NULL,
  actor TEXT, from_status TEXT, to_status TEXT NOT NULL, comment TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, published_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, dead INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox(published_at, dead, id);
CREATE TABLE IF NOT EXISTS labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, event_id TEXT NOT NULL, rule_id TEXT,
  label TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL, evidence_uris TEXT NOT NULL, event_payload TEXT NOT NULL);
"""

_JSON_COLS = ("missing_ppe", "rule_ids", "evidence_uris")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _inc(row: sqlite3.Row) -> dict:
    d = dict(row)
    for c in _JSON_COLS:
        d[c] = json.loads(d[c])
    d["needs_review"] = bool(d["needs_review"])
    d.pop("id", None)
    return d


class SqliteRepository:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)     # fresh volume/container: data/ may not exist yet
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _q(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args)

    # ---- events -------------------------------------------------------------------------------
    def event_incident(self, idempotency_key: str) -> Optional[str]:
        r = self._q("SELECT incident_id FROM events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return r["incident_id"] if r else None

    def insert_event(self, ev: dict, incident_id: str):
        self._q("INSERT INTO events(event_id,idempotency_key,incident_id,payload,received_at) VALUES(?,?,?,?,?)",
                (ev["event_id"], ev["idempotency_key"], incident_id, json.dumps(ev), now_iso()))

    def events_of(self, incident_id: str) -> list:
        return [json.loads(r["payload"]) for r in
                self._q("SELECT payload FROM events WHERE incident_id=? ORDER BY rowid", (incident_id,))]

    # ---- incidents ----------------------------------------------------------------------------
    def find_mergeable(self, ev: dict, active_states: tuple, since_ts: float) -> Optional[dict]:
        r = self._q(
            f"SELECT * FROM incidents WHERE camera_id=? AND zone=? AND track_id IS ? AND hazard_class IS ? "
            f"AND event_type=? AND status IN ({','.join('?' * len(active_states))}) AND last_event_ts>=? "
            f"ORDER BY id DESC LIMIT 1",
            (ev["camera_id"], ev["zone"], ev.get("track_id"), ev.get("hazard_class"), ev["event_type"],
             *active_states, since_ts)).fetchone()
        return _inc(r) if r else None

    def create_incident(self, d: dict) -> str:
        now = now_iso()
        cur = self._q(
            "INSERT INTO incidents(event_type,camera_id,zone,track_id,hazard_class,missing_ppe,rule_ids,status,"
            "needs_review,confidence,first_event_time,last_event_time,last_event_ts,event_count,evidence_uris,"
            "thumbnail_uri,evidence_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["event_type"], d["camera_id"], d["zone"], d.get("track_id"), d.get("hazard_class"),
             json.dumps(d["missing_ppe"]), json.dumps(d["rule_ids"]), d["status"], int(d["needs_review"]),
             d["confidence"], d["first_event_time"], d["last_event_time"], d["last_event_ts"], 1,
             json.dumps(d["evidence_uris"]), d.get("thumbnail_uri"), d["evidence_status"], now, now))
        iid = f"INC-{cur.lastrowid:06d}"
        self._q("UPDATE incidents SET incident_id=? WHERE id=?", (iid, cur.lastrowid))
        return iid

    def update_incident(self, incident_id: str, **f):
        f["updated_at"] = now_iso()
        for c in _JSON_COLS:
            if c in f:
                f[c] = json.dumps(f[c])
        if "needs_review" in f:
            f["needs_review"] = int(f["needs_review"])
        cols = ", ".join(f"{k}=?" for k in f)
        self._q(f"UPDATE incidents SET {cols} WHERE incident_id=?", (*f.values(), incident_id))

    def get_incident(self, incident_id: str) -> Optional[dict]:
        r = self._q("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        return _inc(r) if r else None

    def list_incidents(self, *, status=None, camera_id=None, zone=None, needs_review=None,
                       limit=50, offset=0) -> tuple:
        w, a = [], []
        for col, v in (("status", status), ("camera_id", camera_id), ("zone", zone)):
            if v:
                w.append(f"{col}=?")
                a.append(v)
        if needs_review is not None:
            w.append("needs_review=?")
            a.append(int(needs_review))
        where = ("WHERE " + " AND ".join(w)) if w else ""
        total = self._q(f"SELECT COUNT(*) c FROM incidents {where}", a).fetchone()["c"]
        rows = self._q(f"SELECT * FROM incidents {where} ORDER BY id DESC LIMIT ? OFFSET ?", (*a, limit, offset))
        return [_inc(r) for r in rows], total

    # ---- history / labels ---------------------------------------------------------------------
    def add_history(self, incident_id, actor, from_status, to_status, comment=None):
        self._q("INSERT INTO history(incident_id,at,actor,from_status,to_status,comment) VALUES(?,?,?,?,?,?)",
                (incident_id, now_iso(), actor, from_status, to_status, comment))

    def history(self, incident_id: str) -> list:
        return [dict(r) for r in self._q("SELECT at,actor,from_status,to_status,comment FROM history "
                                         "WHERE incident_id=? ORDER BY id", (incident_id,))]

    def add_label(self, incident_id, event: dict, label, reviewer, evidence_uris):
        self._q("INSERT INTO labels(incident_id,event_id,rule_id,label,reviewer,created_at,evidence_uris,event_payload)"
                " VALUES(?,?,?,?,?,?,?,?)", (incident_id, event["event_id"], event.get("rule_id"), label, reviewer,
                                             now_iso(), json.dumps(evidence_uris), json.dumps(event)))

    def labels(self) -> list:
        out = []
        for r in self._q("SELECT * FROM labels ORDER BY id"):
            d = dict(r)
            d["evidence_uris"] = json.loads(d["evidence_uris"])
            d["event_payload"] = json.loads(d["event_payload"])
            out.append(d)
        return out

    # ---- outbox -------------------------------------------------------------------------------
    def enqueue(self, topic: str, key: str, payload: dict):
        self._q("INSERT INTO outbox(topic,key,payload,created_at) VALUES(?,?,?,?)",
                (topic, key, json.dumps(payload), now_iso()))

    def pending_outbox(self, limit: int) -> list:
        rows = self._q("SELECT * FROM outbox WHERE published_at IS NULL AND dead=0 ORDER BY id LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def mark_published(self, oid: int):
        self._q("UPDATE outbox SET published_at=?, last_error=NULL WHERE id=?", (now_iso(), oid))

    def mark_failed(self, oid: int, error: str, dead: bool):
        self._q("UPDATE outbox SET attempts=attempts+1, last_error=?, dead=? WHERE id=?", (error[:500], int(dead), oid))

    def requeue_dead(self) -> int:
        return self._q("UPDATE outbox SET dead=0, attempts=0 WHERE dead=1").rowcount

    def metrics_snapshot(self, now: "datetime | None" = None) -> dict:
        """Cheap aggregate queries for /metrics (M9). Statuses are always all present so gauges drop to 0 instead of vanishing."""
        by = {s: 0 for s in ("OPEN", "UNDER_REVIEW", "CONFIRMED", "FALSE_POSITIVE", "RESOLVED")}
        for r in self._q("SELECT status, COUNT(*) n FROM incidents GROUP BY status").fetchall():
            by[r["status"]] = r["n"]
        oldest = self._q("SELECT MIN(created_at) m FROM incidents WHERE status='OPEN'").fetchone()["m"]
        age = 0.0
        if oldest:
            t = datetime.strptime(oldest, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            age = max(0.0, ((now or datetime.now(timezone.utc)) - t).total_seconds())
        labels = {r["label"]: r["n"] for r in self._q("SELECT label, COUNT(*) n FROM labels GROUP BY label").fetchall()}
        for k in ("true_positive", "false_positive"):
            labels.setdefault(k, 0)
        return {"by_status": by, "oldest_open_age_s": age, "outbox": self.outbox_stats(), "labels": labels}

    def outbox_stats(self) -> dict:
        r = self._q("SELECT SUM(published_at IS NULL AND dead=0) p, SUM(dead=1) d, COUNT(*) t FROM outbox").fetchone()
        return {"pending": r["p"] or 0, "dead": r["d"] or 0, "total": r["t"] or 0}
