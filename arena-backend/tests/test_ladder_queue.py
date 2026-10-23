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
