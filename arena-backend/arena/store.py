from contextlib import contextmanager
import gzip
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from arena.protocol import Fault

REPLAY_REPORT_FIELDS = ('moves', 'samples', 'final_state', 'sandbox_metrics', 'startup')


class Store:
    def __init__(self, root, recover_jobs=True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.pending_idempotency = None
        self.db = sqlite3.connect(self.root / "arena.sqlite", check_same_thread=False)
        self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, kind TEXT, team TEXT, data TEXT);
          CREATE INDEX IF NOT EXISTS documents_kind_team ON documents(kind,team);
          CREATE INDEX IF NOT EXISTS documents_active_team ON documents(team)
             WHERE kind='job' AND json_extract(data,'$.status') NOT IN ('completed','failed','aborted');
          CREATE TABLE IF NOT EXISTS events(job TEXT, seq INTEGER, data TEXT, PRIMARY KEY(job,seq));
          CREATE TABLE IF NOT EXISTS replay_archives(job TEXT PRIMARY KEY, data BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS controller_clock(id INTEGER PRIMARY KEY, epoch_ms INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS replay_owners(job TEXT, team TEXT, created_ms INTEGER, ladder INTEGER,
             PRIMARY KEY(job,team));
          CREATE INDEX IF NOT EXISTS replay_owners_recent ON replay_owners(team,created_ms DESC,job);
          CREATE TABLE IF NOT EXISTS idempotency(team TEXT, operation TEXT, key TEXT, hash TEXT, id TEXT,
             PRIMARY KEY(team,operation,key));
        """)
        self.db.commit()
        for job in self.list("job") if recover_jobs else []:
            # The service invalidates remote leases after its tables are available.
            if job["status"] not in ("queued", "completed", "failed", "aborted") and not job.get("lease_id"):
                if job.get("official"):
                    self.update(job["id"], status="aborted", report=None, error={"code": "server_restart", "detail": "Hub restarted; a new attempt is required"})
                    self.event(job["id"], "aborted", {"code": "server_restart"})
                else:
                    self.update(job["id"], status="queued", report=None, error=None, restarts=job.get("restarts", 0) + 1)
                    self.event(job["id"], "requeued", {"reason": "server_restart"})

    @contextmanager
    def transaction(self):
        """Reserve the SQLite writer before read/check/write; nested calls share the commit."""
        with self.lock:
            own = not self.db.in_transaction
            if own:
                self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                if own:
                    self.db.commit()
            except BaseException:
                if own:
                    self.db.rollback()
                raise

    def create(self, kind, team, data):
        item = {"id": uuid.uuid4().hex, "team_id": team, "created_unix_ms": time.time_ns() // 1_000_000, **data}
        with self.transaction():
            self.db.execute("INSERT INTO documents VALUES(?,?,?,?)", (item["id"], kind, team, json.dumps(item)))
            if self.pending_idempotency:
                owner, operation, key, digest = self.pending_idempotency
                if owner != team:
                    raise Fault("worker_error", "Idempotency scope mismatch")
                self.db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?)", (team, operation, key, digest, item["id"]))
                self.pending_idempotency = None
        return item

    def get(self, ident, team=None, kind=None, hydrate=True):
        with self.lock:
            row = self.db.execute("SELECT kind,team,data FROM documents WHERE id=?", (ident,)).fetchone()
        if row is None or (team is not None and row[1] != team) or (kind and row[0] != kind):
            raise Fault("not_found", "Record not found")
        item = json.loads(row[2])
        if hydrate and item.get('report_compacted') and item.get('report'):
            with self.lock:
                archive = self.db.execute('SELECT data FROM replay_archives WHERE job=?', (ident,)).fetchone()
            if archive:
                data = json.loads(gzip.decompress(archive[0]))
                if isinstance(data, dict):
                    item['report'] = {**item['report'], **data.get('report', {})}
        return item

    def update(self, ident, **fields):
        with self.transaction():
            item = self.get(ident, hydrate=False)
            item.update(fields)
            self.db.execute("UPDATE documents SET data=? WHERE id=?", (json.dumps(item), ident))
        return item

    def list(self, kind, team=None):
        with self.lock:
            rows = self.db.execute("SELECT data FROM documents WHERE kind=?" + (" AND team=?" if team is not None else "") + " ORDER BY rowid DESC",
                                   (kind, team) if team is not None else (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, job, kind, data):
        with self.transaction():
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE job=?", (job,)).fetchone()[0]
            event = {"seq": seq, "type": kind, "server_unix_ms": time.time_ns() // 1_000_000, "data": data}
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (job, seq, json.dumps(event)))
        return event

    def events(self, job, after=0):
        with self.lock:
            rows = self.db.execute("SELECT data FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT 1000", (job, after)).fetchall()
            archive = self.db.execute("SELECT data FROM replay_archives WHERE job=?", (job,)).fetchone() if not rows else None
        if archive:
            values = json.loads(gzip.decompress(archive[0]))
            if isinstance(values, dict):
                values = values['events']
            return [e for e in values if e['seq'] > after][:1000]
        return [json.loads(row[0]) for row in rows]

    def archive_events(self, job):
        with self.transaction():
            rows = self.db.execute('SELECT data FROM events WHERE job=? ORDER BY seq', (job,)).fetchall()
            if rows:
                report = self.get(job, hydrate=False).get('report') or {}
                raw = json.dumps(dict(events=[json.loads(row[0]) for row in rows],
                    report={k:report[k] for k in REPLAY_REPORT_FIELDS if k in report}))
                self.db.execute('INSERT OR REPLACE INTO replay_archives VALUES(?,?)', (job, gzip.compress(raw.encode(), mtime=0)))
                self.db.execute('DELETE FROM events WHERE job=?', (job,))

    def deduplicate(self, team, operation, key, digest, create, prepare=None):
        if not isinstance(key, str) or not (8 <= len(key) <= 128):
            raise Fault("invalid_request", "Idempotency-Key of 8..128 characters required")
        def existing():
            row = self.db.execute("SELECT hash,id FROM idempotency WHERE team=? AND operation=? AND key=?", (team, operation, key)).fetchone()
            if row:
                if row[0] != digest:
                    raise Fault("idempotency_conflict", "Key already used for different input")
                return self.get(row[1], team)
        with self.lock:
            old = existing()
            if old:
                return old
        prepared = prepare() if prepare else None
        with self.lock:
            old = existing()
            if old:
                return old
            # Record + idempotency key commit together in create(). A crash
            # cannot persist one without the other; filesystem staging may be
            # left orphaned, but never causes duplicate versions/jobs.
            self.pending_idempotency = (team, operation, key, digest)
            try:
                return create(prepared) if prepare else create()
            finally:
                self.pending_idempotency = None
