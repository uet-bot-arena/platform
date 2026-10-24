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
        # Materialize the same public summary once per write, not once per page.
        summary = self._job_summary_column().replace('data', 'NEW.data')
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS job_summaries(job TEXT PRIMARY KEY,team TEXT,data TEXT);
          CREATE INDEX IF NOT EXISTS job_summaries_team ON job_summaries(team);
          CREATE TRIGGER IF NOT EXISTS job_summary_insert AFTER INSERT ON documents WHEN NEW.kind='job'
          BEGIN INSERT INTO job_summaries VALUES(NEW.id,NEW.team,""" + summary + """); END;
          CREATE TRIGGER IF NOT EXISTS job_summary_update AFTER UPDATE ON documents WHEN NEW.kind='job'
          BEGIN INSERT INTO job_summaries VALUES(NEW.id,NEW.team,""" + summary + """)
          ON CONFLICT(job) DO UPDATE SET team=excluded.team,data=excluded.data; END;
          CREATE TRIGGER IF NOT EXISTS job_summary_delete AFTER DELETE ON documents
          BEGIN DELETE FROM job_summaries WHERE job=OLD.id; END;
        """)
        self.db.execute("INSERT INTO job_summaries SELECT d.id,d.team," + self._job_summary_column().replace('data','d.data') +
            " FROM documents d LEFT JOIN job_summaries s ON s.job=d.id WHERE d.kind='job' AND s.job IS NULL")
        self.db.commit()
        for job in self.unfinished_jobs() if recover_jobs else []:
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

    def get(self, ident, team=None, kind=None, hydrate=True, *, summary=False):
        column=self._job_summary_column() if summary else 'data'
        with self.lock:
            row = self.db.execute("SELECT kind,team,"+column+" FROM documents WHERE id=?", (ident,)).fetchone()
        if row is None or (team is not None and row[1] != team) or (kind and row[0] != kind):
            raise Fault("not_found", "Record not found")
        item = json.loads(row[2])
        if hydrate and not summary and item.get('report_compacted') and item.get('report'):
            with self.lock:
                archive = self.db.execute('SELECT data FROM replay_archives WHERE job=?', (ident,)).fetchone()
            if archive:
                data = json.loads(gzip.decompress(archive[0]))
                if isinstance(data, dict):
                    item['report'] = {**item['report'], **{k:v for k,v in data.get('report', {}).items() if k in REPLAY_REPORT_FIELDS}}
        return item

    def update(self, ident, **fields):
        with self.transaction():
            item = self.get(ident, hydrate=False)
            item.update(fields)
            self.db.execute("UPDATE documents SET data=? WHERE id=?", (json.dumps(item), ident))
        return item

    @staticmethod
    def _job_summary_column():
        # Lists/status need counts, never retained replay/build diagnostics.
        column = "CASE WHEN json_type(data,'$.report.samples')='array' THEN " \
            "json_set(data,'$.report.samples',json_array_length(data,'$.report.samples')) ELSE data END"
        paths = (*REPLAY_REPORT_FIELDS, 'artifacts', 'build_log', 'startup_faults', 'fault')
        return "json_remove(" + column + "," + ",".join("'$.report." + p + "'" for p in paths if p != 'samples') + ")"

    def list(self, kind, team=None, *, summary=False):
        with self.lock:
            if summary and kind=='job':
                rows=self.db.execute("SELECT s.data FROM documents d JOIN job_summaries s ON s.job=d.id WHERE d.kind=? "
                    + ("AND d.team=? " if team is not None else '') + "ORDER BY d.rowid DESC",(kind,team) if team is not None else (kind,)).fetchall()
            else:
                rows=self.db.execute("SELECT data FROM documents WHERE kind=? " + ("AND team=? " if team is not None else '') +
                    "ORDER BY rowid DESC",(kind,team) if team is not None else (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def unfinished_jobs(self, *, queued_only=False):
        # Use the partial index; completed benchmark reports can be large and
        # are not needed to reconstruct the durable queue or restart clocks.
        with self.lock:
            rows = self.db.execute("SELECT data FROM documents INDEXED BY documents_active_team WHERE kind='job' "
                "AND json_extract(data,'$.status') NOT IN ('completed','failed','aborted') "
                + ("AND json_extract(data,'$.status')='queued' " if queued_only else "")
                + "ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, job, kind, data):
        return self.event_batch(job,[dict(type=kind,data=data)])[0]

    def event_batch(self, job, items):
        with self.transaction():
            first=self.db.execute("SELECT MAX(COALESCE((SELECT MAX(seq) FROM events WHERE job=?),0),"
                "COALESCE((SELECT json_extract(data,'$.replay_last_seq') FROM documents WHERE id=?),0))+1",(job,job)).fetchone()[0]
            now=time.time_ns()//1_000_000
            events=[dict(seq=first+i,type=item['type'],server_unix_ms=now,data=item['data']) for i,item in enumerate(items)]
            self.db.executemany("INSERT INTO events VALUES(?,?,?)",((job,e['seq'],json.dumps(e)) for e in events))
        return events

    def events(self, job, after=0):
        with self.lock:
            rows = self.db.execute("SELECT data FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT 1000", (job, after)).fetchall()
            metadata = self.db.execute("SELECT json_extract(data,'$.replay_last_seq') FROM documents WHERE id=?",(job,)).fetchone()
            end = metadata[0] if metadata else None
            archive = self.db.execute("SELECT data FROM replay_archives WHERE job=?", (job,)).fetchone() if (end is not None and after<end) or not rows else None
        values = [json.loads(row[0]) for row in rows]
        if archive:
            stored = json.loads(gzip.decompress(archive[0]))
            stored = stored['events'] if isinstance(stored,dict) else stored
            values += [e for e in stored if e['seq']>after]
        return sorted(values,key=lambda e:e['seq'])[:1000]

    def archive_events(self, job):
        with self.transaction():
            if self.get(job,hydrate=False).get('replay_last_seq') is not None:
                return  # The grader's gzip block is already durable; keep its exact bytes.
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
