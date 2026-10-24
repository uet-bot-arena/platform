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
    def test_sse_staggered_cursors_share_one_read_and_old_cursors_recover_full_history(self):
        async def check():
            feeds=Snapshots(ttl=100)
            calls=[]
            events=[dict(seq=i,type='state',data=dict(n=i)) for i in range(1,1501)]
            def load(after):
                calls.append(after);batch=events[after:after+1000]
                return batch,batch[-1]['seq'] if batch else after,after+len(batch)>=len(events)
            for cursor in range(20):
                payload,end,done=await feeds.event_chunk('game',cursor,load)
                self.assertEqual(payload.count('event: arena'),1000-cursor)
                self.assertEqual(end,1000)
            self.assertEqual(calls,[0])
            feeds.items.clear()
            payload,end,done=await feeds.event_chunk('game',1000,load)
            self.assertEqual(payload.count('event: arena'),500);self.assertTrue(done)
            payload,end,done=await feeds.event_chunk('game',0,load)
            self.assertEqual(payload.count('event: arena'),1000);self.assertEqual(end,1000)
            self.assertEqual(calls,[0,1000,0])
            payload,end,done=await feeds.event_chunk('game',end,load)
            self.assertEqual(payload.count('event: arena'),500);self.assertEqual(end,1500)
        asyncio.run(check())

    def test_materialized_summaries_follow_direct_writes_rollback_and_deletion(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            try:
                job=store.create('job','team',dict(kind='benchmark',status='completed',report=dict(samples=[{},{}],build_log='private')))
                summary=store.list('job','team',summary=True)[0]
                self.assertEqual(summary['report'],dict(samples=2))
                with self.assertRaises(RuntimeError):
                    with store.transaction():
                        store.update(job['id'],status='aborted')
                        raise RuntimeError('rollback')
                self.assertEqual(store.list('job','team',summary=True)[0]['status'],'completed')
                updated=dict(job,report=dict(samples=[{}]))
                with store.transaction():store.db.execute('UPDATE documents SET data=? WHERE id=?',(json.dumps(updated),job['id']))
                self.assertEqual(store.list('job','team',summary=True)[0]['report']['samples'],1)
                with store.transaction():store.db.execute('DELETE FROM documents WHERE id=?',(job['id'],))
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM job_summaries').fetchone()[0],0)
            finally:store.db.close()

    def test_stream_watch_subscription_controls_batch_interval_and_releases_on_close(self):
        async def check():
            feeds=Snapshots()
            async def stream():
                yield 'one'
                yield 'two'
            chunks=feeds.limited('user',stream(),job='game')
            self.assertTrue(feeds.watched('game'))
            self.assertFalse(feeds.watched('other'))
            self.assertEqual(await anext(chunks),'one')
            await chunks.aclose()
            self.assertFalse(feeds.watched('game'))
        asyncio.run(check())

    def test_guest_interest_expires_and_is_bounded(self):
        feeds=Snapshots(capacity=2)
        with patch('arena_api.broadcast.monotonic',return_value=1):
            feeds.interest('a');feeds.interest('b');feeds.interest('c')
            self.assertFalse(feeds.watched('a'));self.assertTrue(feeds.watched('c'))
        with patch('arena_api.broadcast.monotonic',return_value=14):
            self.assertFalse(feeds.watched('c'))

    def test_restart_memory_does_not_grow_with_completed_report_history(self):
        import tracemalloc
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            heavy=dict(samples=[dict(wall_ms=1,output='x'*128)]*720)
            with store.transaction():
                for _ in range(300):store.create('job','team',dict(kind='benchmark',status='completed',report=heavy))
                queued=store.create('job','team',dict(kind='benchmark',status='queued',report=None))
            steps=[]
            store.db.set_progress_handler(lambda:steps.append(1) or 0,100)
            self.assertEqual([j['id'] for j in store.unfinished_jobs(queued_only=True)],[queued['id']])
            store.db.set_progress_handler(None,0)
            self.assertLess(len(steps)*100,500,'Historical completed reports were scanned')
            store.db.close()
            tracemalloc.start()
            try:
                recovered=Store(tmp);pending=DurableJobQueue(recovered)
                _,peak=tracemalloc.get_traced_memory()
                self.assertEqual(pending.qsize(),1)
                self.assertLess(peak,5*1024**2,'Restart decoded completed reports instead of unfinished work')
                self.assertEqual(pending.get_nowait(),queued['id'])
            finally:
                tracemalloc.stop()
                if 'recovered' in locals():recovered.db.close()

    def test_shared_connection_readers_wait_for_transaction_rollback(self):
        from arena_api.accounts import Accounts
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);accounts=Accounts(store)
            store.db.execute('CREATE TABLE atomic_read(value TEXT)')
            store.db.execute("INSERT INTO atomic_read VALUES('original')");store.db.commit()
            started=threading.Barrier(3)
            def reader(many):
                started.wait(3)
                return (accounts.rows if many else accounts.one)('SELECT value FROM atomic_read')
            try:
                with ThreadPoolExecutor(2) as pool:
                    with self.assertRaisesRegex(RuntimeError,'rollback fixture'):
                        with store.transaction():
                            store.db.execute("UPDATE atomic_read SET value='uncommitted'")
                            futures=[pool.submit(reader,many) for many in (False,True)]
                            started.wait(3);time.sleep(.1)
                            self.assertFalse(any(f.done() for f in futures),'A concurrent reader observed an unfinished transaction')
                            raise RuntimeError('rollback fixture')
                    self.assertEqual(futures[0].result(3),dict(value='original'))
                    self.assertEqual(futures[1].result(3),[dict(value='original')])
            finally:store.db.close()

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

    def test_queue_survives_restart_and_interrupted_job_starts_from_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pending = DurableJobQueue(store)
            jobs = [store.create("job", str(i), {"status": "queued", "kind": "benchmark"}) for i in range(200)]
            for job in jobs:
                pending.put(job["id"])
            first = pending.get_nowait()
            store.update(first, report={"samples": ["partial-old-run"]})
            store.db.close()
            store = Store(tmp)
            pending = DurableJobQueue(store)
            self.assertEqual(store.db.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(pending.qsize(), 200)
            self.assertEqual(store.get(first)["status"], "queued")
            self.assertIsNone(store.get(first)["report"])
            self.assertEqual(store.get(first)["restarts"], 1)
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

    def test_local_runner_constructs_a_native_backend_only_when_needed(self):
        from arena.runner import LocalRunner
        from arena.store import Store
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch("arena.sandbox.get_backend") as factory:
            store = Store(directory, recover_jobs=False)
            try:
                runner = LocalRunner(store)
                factory.assert_not_called()
                runner.new_worker(Path(directory), None, {})
                runner.new_worker(Path(directory), None, {})
                factory.assert_called_once_with("windows" if os.name == "nt" else "bwrap")
                self.assertEqual(factory.return_value.worker.call_count, 2)
                identity = {"backend": "bwrap", "platform": "linux", "runtime_id": "sha256:" + "a" * 64}
                factory.return_value.identity.return_value = identity
                self.assertEqual(runner.runtime_report({"image_id": "legacy-image"}), identity)
                self.assertEqual(runner.runtime_report({"runtime": {"runtime_id": "other"}}), identity)
            finally:
                store.db.close()
