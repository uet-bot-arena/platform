"""One short-lived snapshot per feed/cursor, shared by all SSE subscribers."""
from collections import OrderedDict
import asyncio
import threading
import time


class Snapshots:
    def __init__(self, ttl=0.5, capacity=1024):
        self.ttl, self.capacity = ttl, capacity
        self.lock = threading.Lock()
        self.items = OrderedDict()
        self.async_lock = asyncio.Lock()

    async def get_async(self, key, produce):
        # Hits stay on the event loop: hundreds of viewers must not occupy
        # the HTTP handler thread pool just to read the same cached value.
        old = self.items.get(key)
        if old and time.monotonic() < old[0]:
            return old[1]
        async with self.async_lock:
            old = self.items.get(key)
            if old and time.monotonic() < old[0]:
                return old[1]
            return await asyncio.to_thread(self.get, key, produce)

    def get(self, key, produce):
        with self.lock:
            old = self.items.get(key)
            if old and time.monotonic() < old[0]:
                return old[1]
            value = produce()
            self.items[key] = (time.monotonic() + self.ttl, value)
            self.items.move_to_end(key)
            while len(self.items) > self.capacity:
                self.items.popitem(last=False)
            return value
