from concurrent.futures import ThreadPoolExecutor
import asyncio
import tempfile
import threading
import time
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch

from arena.job_queue import DurableJobQueue
from arena.store import Store
from arena_api.broadcast import Snapshots
from arena_api.dependencies import peer


class Hardening(unittest.TestCase):
    def test_pch_does_not_override_source_configuration_or_custom_headers(self):
        from arena.supervisor import pch_compatible
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            bot = source/'bot.cpp'
            bot.write_text('// safe\n#include <iostream>\n#include "json.hpp"\nint main(){}')
            self.assertTrue(pch_compatible(source))
            bot.write_text('#define JSON_DIAGNOSTICS 1\n#include "json.hpp"\nint main(){}')
            self.assertFalse(pch_compatible(source))
            bot.write_text('#include "settings.h"\n#include "json.hpp"\nint main(){}')
            self.assertFalse(pch_compatible(source))
            bot.write_text('#include <iostream>\n#include "json.hpp"\nint main(){}')
            (source/'iostream').write_text('#define JSON_DIAGNOSTICS 1')
            self.assertFalse(pch_compatible(source))

    def test_slow_prepare_does_not_hold_database_lock_and_retry_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            entered, release = threading.Event(), threading.Event()
            def prepare():
                entered.set()
                self.assertTrue(release.wait(2))
                return {"value": 42}
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(store.deduplicate, "a", "test", "long-test-key", "hash",
                                     lambda data: store.create("test", "a", data), prepare)
                self.assertTrue(entered.wait(2))
                self.assertTrue(store.lock.acquire(timeout=0.1))
                store.lock.release()
                release.set()
                result = future.result()
            again = store.deduplicate("a", "test", "long-test-key", "hash", lambda: self.fail("duplicate"))
            self.assertEqual(result, again)
            store.db.close()

    def test_queue_survives_restart_and_running_job_does_not_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pending = DurableJobQueue(store)
            jobs = [store.create("job", str(i), {"status": "queued", "kind": "benchmark"}) for i in range(200)]
            for job in jobs:
                pending.put(job["id"])
            first = pending.get_nowait()
            store.db.close()
            store = Store(tmp)
            pending = DurableJobQueue(store)
            self.assertEqual(pending.qsize(), 199)
            self.assertEqual(store.get(first)["status"], "aborted")
            self.assertEqual(pending.position(jobs[1]["id"]), 1)
            self.assertEqual(pending.get_nowait(), jobs[1]["id"])
            store.db.close()

    def test_forwarding_requires_explicit_trusted_socket_peer(self):
        def request(host, forwarded, trusted):
            return SimpleNamespace(client=SimpleNamespace(host=host), headers={"x-forwarded-for": forwarded},
                                   app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(trusted_proxies=trusted))))
        self.assertEqual(peer(request("192.0.2.9", "1.2.3.4", ("127.0.0.1/32",))), "192.0.2.9")
        self.assertEqual(peer(request("127.0.0.1", "198.51.100.2", ("127.0.0.1/32",))), "198.51.100.2")
        self.assertEqual(peer(request("127.0.0.1", "1.2.3.4, 198.51.100.2", ("127.0.0.1/32",))), "198.51.100.2")
        self.assertEqual(peer(request("127.0.0.1", "invalid", ("127.0.0.1/32",))), "127.0.0.1")

    def test_broadcast_builds_one_snapshot_for_many_subscribers(self):
        cache, calls = Snapshots(), []
        with ThreadPoolExecutor(40) as pool:
            values = list(pool.map(lambda _: cache.get("feed", lambda: calls.append(1) or {"ok": True}), range(200)))
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(value == {"ok": True} for value in values))

    def test_async_subscribers_share_slow_producer_without_blocking_event_loop(self):
        async def check():
            cache, calls = Snapshots(), []
            def produce():
                calls.append(1)
                time.sleep(.05)
                return 42
            pending = [asyncio.create_task(cache.get_async('feed', produce)) for _ in range(200)]
            await asyncio.sleep(.01)
            self.assertFalse(pending[0].done())
            self.assertEqual(await asyncio.gather(*pending), [42] * 200)
            self.assertEqual(calls, [1])
        asyncio.run(check())

    def test_image_lookup_cache_and_owner_labels(self):
        from arena import sandbox
        sandbox.invalidate_images()
        image = "sha256:" + "a" * 64
        with patch("arena.sandbox.command", side_effect=[b"linux", image.encode(),b""]) as command:
            self.assertEqual(sandbox.image_id("test-only-image"), image)
            self.assertEqual(sandbox.image_id("test-only-image"), image)
            self.assertEqual(command.call_count, 3)
        sandbox.controller_scope("/tmp/arena-test-owner")
        args = sandbox.options("test", 256, image, [], "run")
        self.assertTrue(any(arg.startswith("uet-arena.owner=") for arg in args))
        sandbox.invalidate_images()
