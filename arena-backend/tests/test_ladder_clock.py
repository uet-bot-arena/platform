import unittest
from datetime import datetime
from arena.ladder_clock import VN,boundary


def ms(value):return int(datetime.fromisoformat(value).replace(tzinfo=VN).timestamp()*1000)


class LadderClock(unittest.TestCase):
    def test_even_vietnam_hours_and_midnight_rollover(self):
        for value,current,nxt in (
            ('2026-10-23T00:00:00','2026-10-23T00:00:00','2026-10-23T02:00:00'),
            ('2026-10-23T01:59:59.999','2026-10-23T00:00:00','2026-10-23T02:00:00'),
            ('2026-10-23T02:00:00','2026-10-23T02:00:00','2026-10-23T04:00:00'),
            ('2026-10-23T23:59:59','2026-10-23T22:00:00','2026-10-24T00:00:00')):
            self.assertEqual(boundary(ms(value)),ms(current))
            self.assertEqual(boundary(ms(value),next=True),ms(nxt))

    def test_overrun_skips_missed_boundary_instead_of_starting_on_completion(self):
        # Previous round finished at 04:10, so scheduler must wait until 06:00.
        self.assertEqual(boundary(ms('2026-10-23T04:10:00'),next=True),ms('2026-10-23T06:00:00'))
        self.assertEqual(boundary(ms('2026-10-23T23:00:00'),5,next=True),ms('2026-10-24T00:00:00'))
        for value in (0,25,True,2.5):
            with self.assertRaises(ValueError):boundary(0,value)
