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
  reviewed_by TEXT, review_comment TEXT,
  raw_evidence TEXT NOT NULL DEFAULT '[]',
  judge_verdict TEXT, severity TEXT, risk_score INTEGER, assessed_version TEXT);
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
CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT NOT NULL, incident_version TEXT NOT NULL, created_at TEXT NOT NULL,
  verdict TEXT NOT NULL, judge_mode TEXT NOT NULL, judge_reason TEXT, vit TEXT, llm TEXT,
  severity TEXT, risk_score INTEGER, risk_factors TEXT,
  summary TEXT, recommended_actions TEXT, citations TEXT, grounded INTEGER, explanation_mode TEXT,
  model_versions TEXT,
  UNIQUE(incident_id, incident_version));
"""
# Columns added after the first release: ALTER TABLE for databases created by an older version.
_MIGRATIONS = {"incidents": [("raw_evidence", "TEXT NOT NULL DEFAULT '[]'"), ("judge_verdict", "TEXT"), ("severity", "TEXT"),
                             ("risk_score", "INTEGER"), ("assessed_version", "TEXT")]}
_A_JSON = ("vit", "llm", "risk_factors", "recommended_actions", "citations", "model_versions")
_A_COLS = ("incident_id", "incident_version", "verdict", "judge_mode", "judge_reason", "severity", "risk_score", "summary",
           "explanation_mode") + _A_JSON

_JSON_COLS = ("missing_ppe", "rule_ids", "evidence_uris", "raw_evidence")


# Review queue order. "priority": highest risk first; PPE incidents the judge thinks are false alarms go to the bottom.
# Fire/smoke is never moved down, whatever the judge says (ARCHITECTURE_V3_ASSESSMENT.md, decision 6).
_ORDER = {
    "newest": "id DESC",
    "priority": "(COALESCE(judge_verdict,'')='LIKELY_FALSE' AND event_type!='HAZARD_DETECTED') ASC, COALESCE(risk_score,-1) DESC, id DESC",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _inc(row: sqlite3.Row) -> dict:
    d = dict(row)
    for c in _JSON_COLS:
        d[c] = json.loads(d[c])
    d["needs_review"] = bool(d["needs_review"])
    d.pop("id", None)
    return d


class _Result:
    """Fully-read query result with the cursor methods callers use (fetchone/fetchall/iteration/lastrowid/rowcount)."""
    __slots__ = ("rows", "lastrowid", "rowcount", "_i")

    def __init__(self, rows, lastrowid, rowcount):
        self.rows, self.lastrowid, self.rowcount, self._i = rows, lastrowid, rowcount, 0

    def fetchone(self):
        if self._i < len(self.rows):
            self._i += 1
            return self.rows[self._i - 1]
        return None

    def fetchall(self):
        rest, self._i = self.rows[self._i:], len(self.rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


class SqliteRepository:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)     # fresh volume/container: data/ may not exist yet
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        for table, cols in _MIGRATIONS.items():        # before SCHEMA: its indexes may use the new columns
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if have and name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
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
        # Rows are read INSIDE the lock. Returning the live cursor let another thread run a statement on the shared
        # connection before the caller fetched, so concurrent requests (the dashboard loads evidence in parallel)
        # intermittently saw an empty result and answered 404.
        with self._lock:
            cur = self._conn.execute(sql, args)
            return _Result(cur.fetchall(), cur.lastrowid, cur.rowcount)

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
            "thumbnail_uri,evidence_status,created_at,updated_at,raw_evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["event_type"], d["camera_id"], d["zone"], d.get("track_id"), d.get("hazard_class"),
             json.dumps(d["missing_ppe"]), json.dumps(d["rule_ids"]), d["status"], int(d["needs_review"]),
             d["confidence"], d["first_event_time"], d["last_event_time"], d["last_event_ts"], 1,
             json.dumps(d["evidence_uris"]), d.get("thumbnail_uri"), d["evidence_status"], now, now,
             json.dumps(d.get("raw_evidence") or [])))
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

    def list_incidents(self, *, status=None, camera_id=None, zone=None, needs_review=None, judge_verdict=None,
                       sort="newest", limit=50, offset=0) -> tuple:
        w, a = [], []
        for col, v in (("status", status), ("camera_id", camera_id), ("zone", zone), ("judge_verdict", judge_verdict)):
            if v:
                w.append(f"{col}=?")
                a.append(v)
        if needs_review is not None:
            w.append("needs_review=?")
            a.append(int(needs_review))
        where = ("WHERE " + " AND ".join(w)) if w else ""
        total = self._q(f"SELECT COUNT(*) c FROM incidents {where}", a).fetchone()["c"]
        rows = self._q(f"SELECT * FROM incidents {where} ORDER BY {_ORDER[sort]} LIMIT ? OFFSET ?", (*a, limit, offset))
        return [_inc(r) for r in rows], total

    # ---- assessments (written by the assessment service through the API) --------------------------
    def add_assessment(self, a: dict) -> bool:
        """Insert one assessment per (incident, version); a repeat for the same version replaces it. Copies verdict,
        severity and score onto the incident unless a newer version was already assessed. Returns True if copied."""
        row = {c: (json.dumps(a.get(c)) if c in _A_JSON else a.get(c)) for c in _A_COLS}
        row["grounded"] = None if a.get("grounded") is None else int(bool(a["grounded"]))
        row["created_at"] = now_iso()
        with self.transaction():
            self._q(f"INSERT OR REPLACE INTO assessments({','.join(row)}) VALUES({','.join('?' * len(row))})",
                    tuple(row.values()))
            # NOT update_incident(): that bumps updated_at, which is the version being assessed.
            return self._q("UPDATE incidents SET judge_verdict=?, severity=?, risk_score=?, assessed_version=? "
                           "WHERE incident_id=? AND (assessed_version IS NULL OR assessed_version<=?)",
                           (a["verdict"], a.get("severity"), a.get("risk_score"), a["incident_version"],
                            a["incident_id"], a["incident_version"])).rowcount > 0

    def latest_assessment(self, incident_id: str) -> Optional[dict]:
        r = self._q("SELECT * FROM assessments WHERE incident_id=? ORDER BY incident_version DESC, id DESC LIMIT 1",
                    (incident_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d.pop("id")
        for c in _A_JSON:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        d["grounded"] = None if d["grounded"] is None else bool(d["grounded"])
        return d

    def assessment_counts(self) -> dict:
        return {r["verdict"]: r["n"] for r in self._q(
            "SELECT judge_verdict verdict, COUNT(*) n FROM incidents WHERE judge_verdict IS NOT NULL GROUP BY judge_verdict")}

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
