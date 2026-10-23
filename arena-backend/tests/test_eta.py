import tempfile
import unittest
from arena.eta import Timing,WINDOW_MS
from arena.store import Store


class ETA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.addCleanup(self.store.db.close)
        self.timing=Timing(self.store);self.now=10_000_000
        self.grader=dict(platform='windows',runtime_id='runtime-a')

    def sample(self,seconds,age=0,kind='match',platform='windows'):
        job=self.store.create('job','test',dict(kind=kind,profile={'id':'bank10'}))
        end=self.now-age
        with self.store.lock,self.store.db:
            self.timing.record(job,dict(self.grader,platform=platform),end-int(seconds*1000),end)
        return job

    def estimate(self,**kwargs):
        return self.timing.estimate('match','bank10','windows','runtime-a',20,4,
            **{'started':True,'now_ms':self.now,**kwargs})

    def test_range_uses_real_durations_newer_samples_have_more_weight(self):
        self.sample(10,WINDOW_MS);self.sample(20)
        value=self.estimate()
        self.assertGreater(value['mean_game_seconds'],15)
        self.assertLess(value['mean_game_seconds'],20)
        self.assertLess(value['lower_seconds'],5*value['mean_game_seconds'])
        self.assertGreater(value['upper_seconds'],5*value['mean_game_seconds'])
        self.assertEqual(value['samples'],2)

    def test_no_estimate_for_unreceived_work_no_slots_or_unknown_profile(self):
        self.sample(10)
        self.assertIsNone(self.estimate(started=False))
        self.assertIsNone(self.timing.estimate('match','bank10','windows','runtime-a',20,0,started=True,now_ms=self.now))
        self.assertIsNone(self.timing.estimate('match','other','windows','runtime-a',20,4,started=True,now_ms=self.now))

    def test_platform_kind_future_and_old_samples_cannot_contaminate_mean(self):
        self.sample(1000,WINDOW_MS+1)
        self.sample(900,kind='benchmark');self.sample(800,platform='linux')
        self.sample(10);self.sample(700,age=-1000)
        self.assertEqual(self.estimate()['mean_game_seconds'],10)
        self.assertEqual(self.estimate()['samples'],1)
