import tempfile
import unittest
from arena.store import Store
from arena.job_queue import DurableJobQueue


class LadderQueue(unittest.TestCase):
    def test_ladder_does_not_block_checks_and_priority_survives_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            try:
                ladder=store.create('job','competition:test',dict(kind='match',status='queued',official={'phase':'ladder'}))
                check=store.create('job','team',dict(kind='benchmark',status='queued'))
                queue=DurableJobQueue(store)
                self.assertEqual(queue.position(check['id']),1)
                # Recovery and direct insertion must assign the same priority.
                queue.remove(ladder['id']);queue.put(ladder['id'])
                queue=DurableJobQueue(store)
                self.assertEqual(queue.position(check['id']),1)
                self.assertEqual(queue.position(ladder['id']),2)
                official=store.create('job','competition:test',dict(kind='match',status='queued',official={'phase':'general'}))
                queue.put(official['id'])
                self.assertEqual(queue.position(official['id']),1)
                self.assertEqual(queue.position(check['id']),2)
            finally:store.db.close()

    def test_private_running_cap_skips_busy_team_without_blocking_other_teams_or_official(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            try:
                busy=[store.create('job','a',dict(kind='benchmark',status='starting')) for _ in range(4)]
                first=store.create('job','a',dict(kind='benchmark',status='queued'))
                other=store.create('job','b',dict(kind='benchmark',status='queued'))
                queue=DurableJobQueue(store)
                queue.remove(first['id']);queue.remove(other['id'])
                queue.put(first['id']);queue.put(other['id'])
                self.assertEqual(queue.claim('windows','runtime'),other['id'])
                self.assertIsNone(queue.claim('windows','runtime'))
                store.update(busy[0]['id'],status='completed')
                self.assertEqual(queue.claim('windows','runtime'),first['id'])
                for _ in range(5):store.create('job','competition:c',dict(kind='match',status='playing'))
                official=store.create('job','competition:c',dict(kind='match',status='queued',official={'phase':'round_robin'}))
                queue.put(official['id'])
                self.assertEqual(queue.claim('windows','runtime'),official['id'])
            finally:store.db.close()
