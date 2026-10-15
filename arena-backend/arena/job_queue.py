"""Durable priority queue: official games, benchmarks, then practice."""
import queue
import time
from arena.protocol import Fault


class DurableJobQueue:
    """SQLite is the queue; only claimed jobs leave it. No global admission cap."""
    maxsize = 0

    def __init__(self, store):
        self.store = store
        self.unfinished_tasks = 0
        with store.lock, store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS job_queue(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, priority INTEGER)")
            store.db.execute("CREATE INDEX IF NOT EXISTS job_queue_order ON job_queue(priority,seq)")
            for job in reversed(store.list("job")):
                if job["status"] == "queued":
                    self.put(job["id"], official=bool(job.get("official")), kind=job.get("kind"))
                else:
                    self.remove(job["id"])

    def put(self, ident, block=True, timeout=None, *, official=False, kind=None):
        with self.store.lock, self.store.db:
            if kind is None:
                try:
                    kind = self.store.get(ident).get("kind")
                except Fault as exc:
                    if exc.code != 'not_found':
                        raise
                    kind = "match"
            self.store.db.execute("INSERT OR IGNORE INTO job_queue(id,priority) VALUES(?,?)",
                                  (ident, 0 if official else 1 if kind == "benchmark" else 2))

    def put_nowait(self, ident):
        self.put(ident, False)

    def put_official(self, ident):
        self.put(ident, official=True)

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
