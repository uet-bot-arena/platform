"""Lab-only SQLite commit and outermost Store.lock measurements."""
import gc
import sys
import threading
import time


class LockMeter:
    def __init__(self, lock):
        self.lock = lock
        self.local = threading.local()
        self.longest_ms = 0
        self.sites = {}
        self.gc_gen2_max_ms = 0
        gc.callbacks.append(self.on_gc)

    def on_gc(self, phase, info):
        now = time.perf_counter()
        if phase == 'start':
            self.local.gc_started = now
        else:
            elapsed = now-getattr(self.local, 'gc_started', now)
            self.local.gc_total = getattr(self.local, 'gc_total', 0)+elapsed
            self.local.gc_started = None
            if info['generation'] == 2:
                self.gc_gen2_max_ms = max(self.gc_gen2_max_ms, elapsed*1000)

    def gc_elapsed(self):
        started = getattr(self.local, 'gc_started', None)
        return getattr(self.local, 'gc_total', 0)+(time.perf_counter()-started if started is not None else 0)

    def close(self):
        gc.callbacks.remove(self.on_gc)

    def acquire(self, *args, **kwargs):
        acquired = self.lock.acquire(*args, **kwargs)
        if acquired:
            depth = getattr(self.local, 'depth', 0)
            if not depth:
                frame = sys._getframe(1)
                sites = []
                while frame and len(sites)<2:
                    if frame.f_code.co_filename not in (__file__,) and not frame.f_code.co_filename.endswith('contextlib.py'):
                        sites.append(f'{frame.f_code.co_filename}:{frame.f_lineno} ({frame.f_code.co_name})')
                    frame = frame.f_back
                self.local.site = ' ← '.join(sites)
                self.local.gc_before = self.gc_elapsed()
                self.local.started = time.perf_counter()
            self.local.depth = depth+1
        return acquired

    def release(self):
        self.local.depth -= 1
        if not self.local.depth:
            elapsed = max(0, time.perf_counter()-self.local.started-(self.gc_elapsed()-self.local.gc_before))*1000
            self.longest_ms = max(self.longest_ms, elapsed)
            self.sites[self.local.site] = max(self.sites.get(self.local.site, 0), elapsed)
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
