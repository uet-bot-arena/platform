import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from arena.protocol import Fault


class Store:
    def __init__(self, root, recover_jobs=True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.pending_idempotency = None
        self.db = sqlite3.connect(self.root / "arena.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, kind TEXT, team TEXT, data TEXT);
          CREATE TABLE IF NOT EXISTS events(job TEXT, seq INTEGER, data TEXT, PRIMARY KEY(job,seq));
          CREATE TABLE IF NOT EXISTS idempotency(team TEXT, operation TEXT, key TEXT, hash TEXT, id TEXT,
             PRIMARY KEY(team,operation,key));
        """)
        self.db.commit()
        for job in self.list("job") if recover_jobs else []:
            if job["status"] not in ("completed", "failed", "aborted"):
                self.update(job["id"], status="aborted", error={"code": "server_restart", "detail": "Controller restarted; no competitive result awarded"})
                self.event(job["id"], "aborted", {"code": "server_restart"})

    def create(self, kind, team, data):
        item = {"id": uuid.uuid4().hex, "team_id": team, "created_unix_ms": time.time_ns() // 1_000_000, **data}
        with self.lock, self.db:
            self.db.execute("INSERT INTO documents VALUES(?,?,?,?)", (item["id"], kind, team, json.dumps(item)))
            if self.pending_idempotency:
                owner, operation, key, digest = self.pending_idempotency
                if owner != team:
                    raise Fault("worker_error", "Idempotency scope mismatch")
                self.db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?)", (team, operation, key, digest, item["id"]))
                self.pending_idempotency = None
        return item

    def get(self, ident, team=None, kind=None):
        with self.lock:
            row = self.db.execute("SELECT kind,team,data FROM documents WHERE id=?", (ident,)).fetchone()
        if row is None or (team is not None and row[1] != team) or (kind and row[0] != kind):
            raise Fault("not_found", "Record not found")
        return json.loads(row[2])

    def update(self, ident, **fields):
        with self.lock, self.db:
            item = self.get(ident)
            item.update(fields)
            self.db.execute("UPDATE documents SET data=? WHERE id=?", (json.dumps(item), ident))
        return item

    def list(self, kind, team=None):
        with self.lock:
            rows = self.db.execute("SELECT data FROM documents WHERE kind=?" + (" AND team=?" if team is not None else "") + " ORDER BY rowid DESC",
                                   (kind, team) if team is not None else (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, job, kind, data):
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE job=?", (job,)).fetchone()[0]
            event = {"seq": seq, "type": kind, "server_unix_ms": time.time_ns() // 1_000_000, "data": data}
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (job, seq, json.dumps(event)))
        return event

    def events(self, job, after=0):
        with self.lock:
            rows = self.db.execute("SELECT data FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT 1000", (job, after)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def deduplicate(self, team, operation, key, digest, create):
        if not isinstance(key, str) or not (8 <= len(key) <= 128):
            raise Fault("invalid_request", "Idempotency-Key of 8..128 characters required")
        with self.lock:
            row = self.db.execute("SELECT hash,id FROM idempotency WHERE team=? AND operation=? AND key=?", (team, operation, key)).fetchone()
            if row:
                if row[0] != digest:
                    raise Fault("idempotency_conflict", "Key already used for different input")
                return self.get(row[1], team)
            # Record + idempotency key commit together in create(). A crash
            # cannot persist one without the other; filesystem staging may be
            # left orphaned, but never causes duplicate versions/jobs.
            self.pending_idempotency = (team, operation, key, digest)
            try:
                return create()
            finally:
                self.pending_idempotency = None
