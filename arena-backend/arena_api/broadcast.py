"""One short-lived snapshot per feed/cursor, shared by all SSE subscribers."""
from collections import OrderedDict
import asyncio
import hashlib
import threading
from time import monotonic
import uuid

from arena.protocol import Fault


class Snapshots:
    def __init__(self, ttl=0.5, capacity=1024, max_streams=4):
        self.ttl, self.capacity = ttl, capacity
        self.lock = threading.Lock()
        self.items = OrderedDict()
        self.async_lock = asyncio.Lock()
        self.max_streams = max_streams
        self.streams = {}
        self.stream_lock = threading.Lock()

    async def get_async(self, key, produce, ttl=None):
        # Hits stay on the event loop: hundreds of viewers must not occupy
        # the HTTP handler thread pool just to read the same cached value.
        old = self.items.get(key)
        if old and monotonic() < old[0]:
            return old[1]
        async with self.async_lock:
            old = self.items.get(key)
            if old and monotonic() < old[0]:
                return old[1]
            return await asyncio.to_thread(self.get, key, produce, ttl)

    def get(self, key, produce, ttl=None):
        with self.lock:
            old = self.items.get(key)
            if old and monotonic() < old[0]:
                return old[1]
            value = produce()
            self.items[key] = (monotonic() + (self.ttl if ttl is None else ttl), value)
            self.items.move_to_end(key)
            while len(self.items) > self.capacity:
                self.items.popitem(last=False)
            return value

    async def principal(self, accounts, cookie):
        key = ('session', hashlib.sha256(cookie.encode()).hexdigest())
        return await self.get_async(key, lambda: accounts.principal(cookie), ttl=14)  # plus the one-second heartbeat: revocation within 15 seconds

    def limited(self, account, stream):
        now = monotonic()
        ticket = uuid.uuid4().hex
        with self.stream_lock:
            # A response disconnected before its generator starts must not leak a slot.
            self.streams = {k:v for k,v in self.streams.items() if v[1] > now}
            if sum(v[0] == account for v in self.streams.values()) >= self.max_streams:
                raise Fault('rate_limited', 'Đã mở đủ luồng trực tiếp; hãy đóng bớt tab')
            self.streams[ticket] = (account, now+65)
        async def chunks():
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                with self.stream_lock:
                    self.streams.pop(ticket, None)
        return chunks()
