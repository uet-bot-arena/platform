"""Durable priority queue: official games, benchmarks, then practice.

Each waiting job may require a grader platform (linux/windows) and runtime_id; graders claim
only jobs they are allowed to run (Service.try_lease).
"""
import queue
import sqlite3
import time
from arena.protocol import Fault


MAX_RUNNING_PER_TEAM = 4

def official_priority(job):
    official=job.get('official')
    return bool(official) and (not isinstance(official,dict) or official.get('phase')!='ladder')


class DurableJobQueue:
    """SQLite is the queue; only claimed jobs leave it. No global admission cap."""
    maxsize = 0

    def __init__(self, store):
        self.store = store
        self.unfinished_tasks = 0
        with store.lock, store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS job_queue(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, priority INTEGER)")
            store.db.execute("CREATE INDEX IF NOT EXISTS job_queue_order ON job_queue(priority,seq)")
            for column in ("platform", "runtime_id"):
                try:
                    store.db.execute(f"ALTER TABLE job_queue ADD COLUMN {column} TEXT")
                except sqlite3.OperationalError:
                    pass  # already migrated
            store.db.execute("DELETE FROM job_queue AS q WHERE EXISTS (SELECT 1 FROM documents d "
                "WHERE d.id=q.id AND d.kind='job' "
                "AND json_extract(d.data,'$.status') IS NOT 'queued')")
            for job in store.unfinished_jobs(queued_only=True):
                requires = job.get("requires") or {}
                self.put(job["id"], official=official_priority(job), kind=job.get("kind"),
                         platform=requires.get("platform"), runtime_id=requires.get("runtime_id"))

    def put(self, ident, block=True, timeout=None, *, official=False, kind=None, platform=None, runtime_id=None, front=False):
        with self.store.lock, self.store.db:
            if kind is None:
                try:
                    job = self.store.get(ident)
                    kind, official = job.get("kind"), official or official_priority(job)
                except Fault as exc:
                    if exc.code != 'not_found':
                        raise
                    kind = "match"
            priority = 0 if official else 1 if kind == "benchmark" else 2
            if front:
                # A job handed back by a lost grader keeps its turn instead of rejoining the end.
                first = self.store.db.execute("SELECT MIN(seq) FROM job_queue").fetchone()[0]
                self.store.db.execute("INSERT OR IGNORE INTO job_queue(seq,id,priority,platform,runtime_id) VALUES(?,?,?,?,?)",
                                      ((first or 1) - 1, ident, priority, platform, runtime_id))
            else:
                self.store.db.execute("INSERT OR IGNORE INTO job_queue(id,priority,platform,runtime_id) VALUES(?,?,?,?)",
                                      (ident, priority, platform, runtime_id))

    def put_nowait(self, ident):
        self.put(ident, False)

    def put_official(self, ident, platform=None, runtime_id=None):
        self.put(ident, official=True, platform=platform, runtime_id=runtime_id)

    def claim(self, platform, runtime_id):
        """Remove and return the best job this grader may run, or None. Caller holds store.lock."""
        row = self.store.db.execute(
            "SELECT q.id FROM job_queue q LEFT JOIN documents d ON d.id=q.id "
            "WHERE (q.platform IS NULL OR q.platform=?) AND (q.runtime_id IS NULL OR q.runtime_id=?) "
            "AND (d.id IS NULL OR d.team LIKE 'competition:%' OR (SELECT COUNT(*) FROM documents r "
            "WHERE r.team=d.team AND r.kind='job' AND json_extract(r.data,'$.status') NOT IN ('completed','failed','aborted') "
            "AND json_extract(r.data,'$.status')<>'queued')<?) ORDER BY q.priority,q.seq LIMIT 1",
            (platform, runtime_id, MAX_RUNNING_PER_TEAM)).fetchone()
        if not row:
            return None
        self.store.db.execute("DELETE FROM job_queue WHERE id=?", (row[0],))
        return row[0]

    def get(self, block=True, timeout=None):
        until = time.monotonic() + timeout if timeout is not None else None
        while True:
            with self.store.lock, self.store.db:
                row = self.store.db.execute("SELECT id FROM job_queue ORDER BY priority,seq LIMIT 1").fetchone()
                if row:
                    self.remove(row[0])
                    try:
                        self.store.update(row[0], status="starting", lease_until_ms=int(time.time()*1000)+30000)
                    except Fault as exc:
                        if exc.code != 'not_found':
                            raise
                        # Queue-only records are supported by scheduler unit tests.
                    self.unfinished_tasks += 1
                    return row[0]
            if not block or until is not None and time.monotonic() >= until:
                raise queue.Empty
            time.sleep(0.02)

    def get_nowait(self):
        return self.get(False)

    def remove(self, ident):
        with self.store.lock, self.store.db:
            self.store.db.execute("DELETE FROM job_queue WHERE id=?", (ident,))

    def task_done(self):
        with self.store.lock:
            self.unfinished_tasks = max(0, self.unfinished_tasks - 1)

    def qsize(self):
        with self.store.lock:
            return self.store.db.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0]

    def empty(self):
        return self.qsize() == 0

    def full(self):
        return False

    def practice_full(self):
        return False

    def counts(self):
        with self.store.lock:
            rows = dict(self.store.db.execute("SELECT priority,COUNT(*) FROM job_queue GROUP BY priority"))
        return {"official": rows.get(0, 0), "practice": rows.get(1, 0)+rows.get(2, 0)}

    def position(self, ident):
        with self.store.lock:
            row = self.store.db.execute("SELECT priority,seq FROM job_queue WHERE id=?", (ident,)).fetchone()
            if not row:
                return None
            return self.store.db.execute("SELECT COUNT(*) FROM job_queue WHERE priority<? OR (priority=? AND seq<=?)", (row[0], *row)).fetchone()[0]
