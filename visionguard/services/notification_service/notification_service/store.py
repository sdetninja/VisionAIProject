from __future__ import annotations
import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS state(
  incident_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, severity TEXT, analyzed INTEGER NOT NULL DEFAULT 0,
  first_seen REAL NOT NULL, notified_at REAL, notified_sev TEXT, acked INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE NOT NULL, incident_id TEXT NOT NULL, kind TEXT NOT NULL,
  channel TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_at REAL NOT NULL, last_error TEXT, created_at REAL NOT NULL, sent_at REAL);
CREATE INDEX IF NOT EXISTS ix_n_due ON notifications(status, next_at);
"""


class Store:
    def __init__(self, path=":memory:"):
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)     # fresh volume: parent dir may not exist
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.l = threading.RLock()

    def q(self, sql, a=()):
        with self.l:
            return self.c.execute(sql, a)

    # state
    def get_state(self, iid):
        r = self.q("SELECT * FROM state WHERE incident_id=?", (iid,)).fetchone()
        if not r:
            return None
        d = dict(r); d["snapshot"] = json.loads(d["snapshot"]); return d

    def upsert_snapshot(self, iid, snap, now):
        if self.get_state(iid):
            self.q("UPDATE state SET snapshot=? WHERE incident_id=?", (json.dumps(snap), iid))
        else:
            self.q("INSERT INTO state(incident_id,snapshot,first_seen) VALUES(?,?,?)", (iid, json.dumps(snap), now))

    def set_state(self, iid, **f):
        self.q(f"UPDATE state SET {', '.join(k + '=?' for k in f)} WHERE incident_id=?", (*f.values(), iid))

    def all_states(self, where="1=1"):
        out = []
        for r in self.q(f"SELECT * FROM state WHERE {where}"):
            d = dict(r); d["snapshot"] = json.loads(d["snapshot"]); out.append(d)
        return out

    # notifications
    def enqueue(self, key, iid, kind, channel, message, now) -> bool:
        cur = self.q("INSERT OR IGNORE INTO notifications(dedupe_key,incident_id,kind,channel,message,status,next_at,created_at) "
                     "VALUES(?,?,?,?,?,'PENDING',?,?)", (key, iid, kind, channel, json.dumps(message), now, now))
        return cur.rowcount == 1

    def due(self, now, limit=100):
        return [dict(r, message=json.loads(r["message"])) for r in
                self.q("SELECT * FROM notifications WHERE status='PENDING' AND next_at<=? ORDER BY id LIMIT ?", (now, limit))]

    def sent_since(self, channel, since):
        return self.q("SELECT COUNT(*) FROM notifications WHERE channel=? AND status='SENT' AND sent_at>=?", (channel, since)).fetchone()[0]

    def mark(self, nid, status, now=None, error=None, next_at=None, attempts_inc=0):
        self.q("UPDATE notifications SET status=?, sent_at=COALESCE(?,sent_at), last_error=?, next_at=COALESCE(?,next_at), "
               "attempts=attempts+? WHERE id=?", (status, now if status == "SENT" else None, error, next_at, attempts_inc, nid))

    def cancel_pending(self, iid, kind_prefix):
        return self.q("UPDATE notifications SET status='CANCELLED' WHERE incident_id=? AND status='PENDING' AND kind LIKE ?",
                      (iid, kind_prefix + "%")).rowcount

    def list(self, status=None, limit=100):
        w, a = ("WHERE status=?", (status,)) if status else ("", ())
        return [dict(r, message=json.loads(r["message"])) for r in
                self.q(f"SELECT * FROM notifications {w} ORDER BY id DESC LIMIT ?", (*a, limit))]

    def requeue_dead(self, now):
        return self.q("UPDATE notifications SET status='PENDING', attempts=0, next_at=? WHERE status='DEAD'", (now,)).rowcount

    def stats(self):
        return {r[0]: r[1] for r in self.q("SELECT status, COUNT(*) FROM notifications GROUP BY status")}
