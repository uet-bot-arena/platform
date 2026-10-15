from dataclasses import replace
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError
from arena import engine
from arena.adjudication import CURRENT
from arena.config import match_profile, profile, request_profile, stored_profile
from arena.protocol import Fault
from arena.service import Service
from arena.store import Store
from arena_api.schemas import Match, Benchmark, CompetitionCreate


class TimeBank(unittest.TestCase):
    def test_default_bank_preserves_exact_legacy_profiles(self):
        self.assertEqual(request_profile({}), match_profile(300000))
        for ms in (60000,300000):
            self.assertEqual(stored_profile(match_profile(ms).public()), match_profile(ms))
        for ms in (15000,30000,60000,300000):
            old=profile(ms).public()
            self.assertNotIn('match_ms', old)
            self.assertEqual(stored_profile(old).public(),old)
            self.assertEqual(request_profile({'turn_ms':ms}).public(),old)
        for invalid in (True,1,15000,120000):
            with self.assertRaises(ValueError):match_profile(invalid)
        with self.assertRaises(ValueError):request_profile({'match_ms':60000,'turn_ms':60000})

    def test_api_rejects_mixed_and_unapproved_controls(self):
        for schema,fields in ((Match,{'red_submission_id':'a'*32,'blue_submission_id':'b'*32,'delegate_timeout_claims':True}),
                              (Benchmark,{'submission_id':'a'*32}), (CompetitionCreate,{'title':'Bullet Cup'})):
            self.assertEqual(request_profile(schema(**fields).model_dump(exclude_none=True)).match_ms,300000)
            for clock in ({'match_ms':15000},{'match_ms':True},{'match_ms':60000,'turn_ms':60000}):
                with self.assertRaises(ValidationError):schema(**fields,**clock)

    def play(self,timeout=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        store=Store(tmp.name,recover_jobs=False);self.addCleanup(store.db.close)
        service=Service(store,start_worker=False)
        p=replace(match_profile(60000),max_plies=3,grace_ms=1)
        job=store.create('job','clock-test',{'kind':'match','status':'queued','profile':p.public(),'adjudication':CURRENT})
        observations=[]
        def turn(message,on_started):
            observations.append(message)
            side=message['payload']['state']['to_move']
            on_started({'budget_ms':message['payload']['clocks'][side],'estimated_started_unix_ms':1000})
            if timeout and len(observations)==3:
                fault=Fault('timeout','bank exhausted');fault.worker_data={'remaining_ms':0};raise fault
            return {'message':{'payload':message['payload']['legal_moves'][0]},'wall_ms':1250,
                    'remaining_ms':message['payload']['clocks'][side]-1250}
        worker=Mock(turn=turn);report={'samples':[]}
        service.run_match(job,[worker,worker],report,p)
        return observations,report,store.events(job['id'])

    def test_multiple_turns_deduct_same_bank_and_freeze_opponent(self):
        observations,report,events=self.play()
        self.assertEqual([o['payload']['clocks'] for o in observations],
                         [{'red':60000,'blue':60000},{'red':58750,'blue':60000},{'red':58750,'blue':58750}])
        self.assertEqual(report['clocks'],{'red':57500,'blue':58750})
        self.assertEqual(report['result']['reason'],'ply_limit')
        snapshots=[e['data']['clocks'] for e in events if e['type']=='state']
        self.assertEqual(snapshots[0],{'red':60000,'blue':60000})
        self.assertEqual(snapshots[-1],report['clocks'])

    def test_timeout_freezes_zero_and_preserves_opponent_through_claim(self):
        observations,report,events=self.play(timeout=True)
        self.assertEqual(report['result'],{'winner':'blue','reason':'timeout'})
        self.assertEqual(report['clocks'],{'red':0,'blue':58750})
        pending=next(e['data'] for e in events if e['type']=='timeout_pending')
        self.assertEqual(pending['clocks'],report['clocks'])
        self.assertEqual(len(report['samples']),2,'late action is never a successful sample')

    def test_benchmark_keeps_one_bank_across_independent_cases(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        store=Store(tmp.name,recover_jobs=False);self.addCleanup(store.db.close)
        service=Service(store,start_worker=False)
        job=store.create('job','clock-test',{'kind':'benchmark','profile':match_profile(60000).public()})
        budgets=[]
        def turn(message):
            bank=message['payload']['clocks']['red'];budgets.append(bank)
            return {'message':{'payload':message['payload']['legal_moves'][0]},'wall_ms':100,'remaining_ms':bank-100}
        report={'samples':[]};service.run_benchmark(job,Mock(turn=turn),report,match_profile(60000))
        self.assertEqual(budgets,list(range(60000,52800,-100)))
        self.assertEqual(report['clocks']['red'],52800)
        self.assertTrue(report['passed'])
