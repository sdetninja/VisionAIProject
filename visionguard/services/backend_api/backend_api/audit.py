"""Append-only, hash-chained audit log (tamper-evident: editing/deleting a row breaks verify())."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
import time


class AuditLog:
    def __init__(self, path=":memory:", clock=time.time):
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.execute("CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, actor TEXT, role TEXT, "
                       "action TEXT, target TEXT, outcome TEXT, detail TEXT, prev TEXT, hash TEXT)")
        self.l, self.clock = threading.Lock(), clock

    def record(self, actor, role, action, target="", outcome="ok", detail=""):
        with self.l:
            r = self.c.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev, at = (r[0] if r else ""), self.clock()
            body = json.dumps([at, actor, role, action, target, outcome, detail, prev])
            self.c.execute("INSERT INTO audit(at,actor,role,action,target,outcome,detail,prev,hash) VALUES(?,?,?,?,?,?,?,?,?)",
                           (at, actor, role, action, target, outcome, detail[:500], prev, hashlib.sha256(body.encode()).hexdigest()))

    def verify(self) -> bool:
        prev = ""
        for at, actor, role, action, target, outcome, detail, p, h in self.c.execute(
                "SELECT at,actor,role,action,target,outcome,detail,prev,hash FROM audit ORDER BY id"):
            if p != prev or h != hashlib.sha256(json.dumps([at, actor, role, action, target, outcome, detail, p]).encode()).hexdigest():
                return False
            prev = h
        return True

    def list(self, limit=100, actor=None):
        q, a = "SELECT at,actor,role,action,target,outcome,detail FROM audit", ()
        if actor:
            q, a = q + " WHERE actor=?", (actor,)
        return [dict(zip(("at", "actor", "role", "action", "target", "outcome", "detail"), r))
                for r in self.c.execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit))]
