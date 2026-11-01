"""Model registry (SQLite + content-addressed weights). Stages: candidate -> staging -> production -> archived.
Serving reads an atomic pointer file and verifies the weights' sha256 before loading."""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .datasets import sha256_file

STAGES = ("candidate", "staging", "production", "archived")
SCHEMA = """
CREATE TABLE IF NOT EXISTS models(name TEXT, version INTEGER, stage TEXT, metrics TEXT, dataset_hash TEXT, test_hash TEXT,
  weights_path TEXT, weights_sha256 TEXT, created_at REAL, parent_version INTEGER, notes TEXT, PRIMARY KEY(name, version));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, name TEXT, version INTEGER, action TEXT, actor TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS frozen(name TEXT PRIMARY KEY, test_hash TEXT NOT NULL, set_at REAL, actor TEXT);
"""


class RegistryError(Exception):
    pass


class Registry:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.root / "registry.db"), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _ev(self, name, version, action, actor, detail=""):
        self.db.execute("INSERT INTO events(at,name,version,action,actor,detail) VALUES(?,?,?,?,?,?)",
                        (self.clock(), name, version, action, actor, detail))

    # ---- frozen test set
    def freeze_test(self, name, test_hash, actor):
        cur = self.frozen_hash(name)
        if cur and cur != test_hash:
            raise RegistryError("test set already frozen with a different hash; use a new model name or rebase_test()")
        self.db.execute("INSERT OR REPLACE INTO frozen VALUES(?,?,?,?)", (name, test_hash, self.clock(), actor))
        self._ev(name, None, "freeze_test", actor, test_hash)

    def rebase_test(self, name, new_hash, actor, reason):
        """Deliberately replace the frozen test set. Old/new metrics are no longer comparable: logged loudly."""
        if not reason:
            raise RegistryError("reason required")
        self.db.execute("INSERT OR REPLACE INTO frozen VALUES(?,?,?,?)", (name, new_hash, self.clock(), actor))
        self._ev(name, None, "rebase_test", actor, reason)

    def frozen_hash(self, name):
        r = self.db.execute("SELECT test_hash FROM frozen WHERE name=?", (name,)).fetchone()
        return r[0] if r else None

    # ---- register
    def register(self, name, weights, metrics, dataset_hash, test_hash, actor, parent=None, notes=""):
        weights = Path(weights)
        if not weights.is_file():
            raise RegistryError(f"weights not found: {weights}")
        v = (self.db.execute("SELECT MAX(version) FROM models WHERE name=?", (name,)).fetchone()[0] or 0) + 1
        dest = self.root / "models" / name / f"v{v}" / weights.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(weights, dest)
        sha = sha256_file(dest)
        self.db.execute("INSERT INTO models VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (name, v, "candidate", json.dumps(metrics), dataset_hash, test_hash, str(dest), sha, self.clock(), parent, notes))
        self._ev(name, v, "register", actor, f"sha256={sha[:12]}")
        return v

    def get(self, name, version):
        r = self.db.execute("SELECT * FROM models WHERE name=? AND version=?", (name, version)).fetchone()
        if not r:
            raise RegistryError(f"{name} v{version} not found")
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"])
        return d

    def current(self, name, stage="production"):
        r = self.db.execute("SELECT version FROM models WHERE name=? AND stage=? ORDER BY version DESC LIMIT 1", (name, stage)).fetchone()
        return self.get(name, r[0]) if r else None

    def list(self, name=None):
        q, a = "SELECT name,version,stage,dataset_hash,created_at,notes FROM models", ()
        if name:
            q, a = q + " WHERE name=?", (name,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY name, version", a)]

    def events(self, name=None, limit=100):
        q, a = "SELECT * FROM events", ()
        if name:
            q, a = q + " WHERE name=?", (name,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit))]

    # ---- stage moves (gate checks happen in promote.py; this layer enforces structure + integrity)
    def set_stage(self, name, version, stage, actor, detail=""):
        m = self.get(name, version)
        if stage not in STAGES:
            raise RegistryError(f"bad stage {stage}")
        order = {"candidate": 0, "staging": 1, "production": 2}
        if m["stage"] == "archived":
            raise RegistryError("archived models cannot be moved; use rollback() or retrain")
        if stage in ("staging", "production") and order[stage] != order[m["stage"]] + 1:
            raise RegistryError(f"cannot move {m['stage']} -> {stage} (no skipping stages)")
        if sha256_file(m["weights_path"]) != m["weights_sha256"]:
            raise RegistryError("weights file changed since registration (sha256 mismatch)")
        if stage == "production":
            for r in self.db.execute("SELECT version FROM models WHERE name=? AND stage='production'", (name,)).fetchall():
                self.db.execute("UPDATE models SET stage='archived' WHERE name=? AND version=?", (name, r[0]))
                self._ev(name, r[0], "archive", actor, f"replaced by v{version}")
        self.db.execute("UPDATE models SET stage=? WHERE name=? AND version=?", (stage, name, version))
        self._ev(name, version, f"stage:{stage}", actor, detail)
        if stage == "production":
            self.write_pointer(name)

    def staged_since(self, name, version):
        r = self.db.execute("SELECT at FROM events WHERE name=? AND version=? AND action='stage:staging' ORDER BY id DESC LIMIT 1", (name, version)).fetchone()
        return r[0] if r else None

    def rollback(self, name, actor, reason):
        if not reason:
            raise RegistryError("reason required")
        cur = self.current(name)
        prev = self.db.execute("SELECT version FROM models WHERE name=? AND stage='archived' AND version < ? "
                               "AND EXISTS(SELECT 1 FROM events e WHERE e.name=models.name AND e.version=models.version AND e.action='stage:production') "
                               "ORDER BY version DESC LIMIT 1", (name, cur["version"] if cur else 10**9)).fetchone()
        if not prev:
            raise RegistryError("no previous production version to roll back to")
        if sha256_file(self.get(name, prev[0])["weights_path"]) != self.get(name, prev[0])["weights_sha256"]:
            raise RegistryError("previous weights corrupted")
        if cur:
            self.db.execute("UPDATE models SET stage='archived' WHERE name=? AND version=?", (name, cur["version"]))
            self._ev(name, cur["version"], "archive", actor, f"rollback: {reason}")
        self.db.execute("UPDATE models SET stage='production' WHERE name=? AND version=?", (name, prev[0]))
        self._ev(name, prev[0], "rollback", actor, reason)
        self.write_pointer(name)
        return prev[0]

    # ---- serving pointer
    def pointer_path(self, name):
        return self.root / "serving" / f"{name}.json"

    def write_pointer(self, name):
        m = self.current(name)
        p = self.pointer_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"name": name, "version": m["version"], "weights_path": m["weights_path"],
                                   "weights_sha256": m["weights_sha256"], "updated_at": self.clock()}, indent=2))
        os.replace(tmp, p)    # atomic: serving never sees a half-written pointer


def load_pointer(path) -> dict:
    """For M1: returns pointer if the weights' hash matches; raises otherwise (keep serving the old model)."""
    d = json.loads(Path(path).read_text())
    if sha256_file(d["weights_path"]) != d["weights_sha256"]:
        raise RegistryError("served weights failed integrity check")
    return d
