"""Lab-only SQLite commit and outermost Store.lock measurements."""
import threading
import time


class LockMeter:
    def __init__(self, lock):
        self.lock = lock
        self.local = threading.local()
        self.longest_ms = 0

    def acquire(self, *args, **kwargs):
        acquired = self.lock.acquire(*args, **kwargs)
        if acquired:
            depth = getattr(self.local, 'depth', 0)
            if not depth:
                self.local.started = time.perf_counter()
            self.local.depth = depth+1
        return acquired

    def release(self):
        self.local.depth -= 1
        if not self.local.depth:
            self.longest_ms = max(self.longest_ms, (time.perf_counter()-self.local.started)*1000)
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def instrument(store):
    meter = LockMeter(store.lock)
    store.lock = meter
    commits = [0]
    def trace(sql):
        if sql == 'COMMIT':
            commits[0] += 1
    store.db.set_trace_callback(trace)
    return meter, commits
