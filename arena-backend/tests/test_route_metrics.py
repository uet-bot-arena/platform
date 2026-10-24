import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent/'load_lab'))
from route_metrics import LockMeter


class Metrics(unittest.TestCase):
    def test_nested_lock_subtracts_only_gc_inside_hold_and_reports_site(self):
        now=[0.0];meter=LockMeter(threading.RLock())
        try:
            with patch('route_metrics.time.perf_counter',side_effect=lambda:now[0]):
                meter.acquire();meter.acquire()
                now[0]=2;meter.on_gc('start',dict(generation=2))
                now[0]=5;meter.on_gc('stop',dict(generation=2))
                now[0]=6;meter.release()
                self.assertEqual(meter.longest_ms,0)
                now[0]=7;meter.release()
            self.assertEqual(meter.longest_ms,4000)
            self.assertEqual(meter.gc_gen2_max_ms,3000)
            self.assertEqual(len(meter.sites),1)
            self.assertIn('test_route_metrics.py',next(iter(meter.sites)))
        finally:meter.close()
