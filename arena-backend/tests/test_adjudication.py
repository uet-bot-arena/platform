from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from arena import engine
from arena.adjudication import CURRENT, LEGACY, policy, position_key, valid_result
from arena.config import profile
from arena.job_queue import JobQueue
from arena.protocol import Fault
from arena.service import Service
from arena.store import Store


class Adjudication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name, recover_jobs=False)
        self.addCleanup(self.store.db.close)
        self.service = Service(self.store, start_worker=False)

    def job(self, rules=CURRENT):
        return self.store.create('job', 'unit-team', {'kind': 'match', 'status': 'queued',
            'profile': profile().public(), 'image_id': 'unit-image', 'submission_ids': [], 'adjudication': rules})

    def play(self, rules=CURRENT, **limits):
        job = self.job(rules)
        red = iter([{'from': 'a2', 'to': 'b3'}, {'from': 'b3', 'to': 'a2'}] * 5)
        blue = iter([{'from': 'b9', 'to': 'a9'}, {'from': 'a9', 'to': 'b9'}] * 5)
        workers = [Mock(turn=lambda *_: {'message': {'payload': next(red)}, 'wall_ms': 1}),
                   Mock(turn=lambda *_: {'message': {'payload': next(blue)}, 'wall_ms': 1})]
        report = {'samples': []}
        self.service.run_match(job, workers, report, replace(profile(), **limits))
        return job, report

    def test_threefold_counts_initial_position_and_side_to_move(self):
        job, report = self.play()
        self.assertEqual(report['result'], {'winner': None, 'reason': 'threefold_repetition'})
        self.assertEqual(report['final_state']['ply'], 8)
        self.assertNotIn('aborted', report)
        states = [e['data']['state'] for e in self.store.events(job['id']) if e['type'] == 'state']
        self.assertEqual(states[-1], report['final_state'])
        initial = engine.initial_state()
        self.assertNotEqual(position_key(initial), position_key({**initial, 'to_move': 'blue'}))
        self.assertEqual(position_key(initial), position_key({**initial, 'ply': 200}))

    def test_limits_draw_but_shutdown_never_draws(self):
        for limits, reason in [({'max_plies': 2}, 'ply_limit'), ({'job_wall_ms': 0}, 'wall_limit')]:
            with self.subTest(reason=reason):
                job, report = self.play(**limits)
                self.assertEqual(report['result'], {'winner': None, 'reason': reason})
                self.assertEqual([e['data']['state'] for e in self.store.events(job['id']) if e['type']=='state'][-1], report['final_state'])
        self.service.stop.set()
        _, report = self.play()
        self.assertEqual(report['aborted'], 'server_shutdown')
        self.assertNotIn('result', report)

    def test_legacy_competitions_keep_original_limit_behavior(self):
        _, report = self.play(LEGACY, max_plies=8)
        self.assertEqual(report['aborted'], 'ply_limit')
        self.assertNotIn('result', report)
        self.assertEqual(policy(), LEGACY)

    def test_timeout_grace_proof_survives_backward_wall_clock(self):
        job, report = self.job(), {'samples': []}
        worker = Mock(turn=Mock(side_effect=Fault('timeout', 'unit deadline')))
        emit = self.store.event
        def backward_wall(ident, kind, data):
            wall = 12_000_000_000 if kind == 'timeout_pending' else 11_000_000_000
            with patch('arena.store.time.time_ns', return_value=wall):
                return emit(ident, kind, data)
        with patch.object(self.store, 'event', side_effect=backward_wall):
            self.service.run_match(job, [worker, worker], report, replace(profile(), grace_ms=20))
        events = self.store.events(job['id'])
        pending = next(e for e in events if e['type'] == 'timeout_pending')
        claimed = next(e for e in events if e['type'] == 'timeout_claimed')
        self.assertLess(claimed['server_unix_ms'], pending['server_unix_ms'])
        self.assertEqual(claimed['data']['grace_clock'], 'controller-monotonic')
        self.assertGreaterEqual(claimed['data']['grace_waited_ms'], 20)
        self.assertEqual(report['result'], {'winner': 'blue', 'reason': 'timeout'})

    def test_shutdown_during_grace_never_claims_or_awards_timeout(self):
        job, report = self.job(), {'samples': []}
        worker = Mock(turn=Mock(side_effect=Fault('timeout', 'unit deadline')))
        with patch.object(self.service.stop, 'wait', return_value=True):
            with self.assertRaises(Fault) as caught:
                self.service.run_match(job, [worker, worker], report, profile())
        self.assertEqual(caught.exception.code, 'worker_error')
        self.assertNotIn('result', report)
        self.assertFalse(any(e['type'] == 'timeout_claimed' for e in self.store.events(job['id'])))

    def test_winning_move_on_final_ply_takes_precedence_over_draw(self):
        state = engine.initial_state()
        state['board'][1][7] = {'side': 'red', 'kind': 'rock'}
        worker = Mock(turn=lambda *_: {'message': {'payload': {'from':'h8','to':'i9'}}, 'wall_ms':1})
        report = {'samples': []}
        with patch('arena.service.engine.initial_state', return_value=state):
            self.service.run_match(self.job(), [worker, worker], report, replace(profile(), max_plies=1))
        self.assertEqual(report['result'], {'winner':'red','reason':'base_capture'})

    def execute_startup(self, faults, rules=CURRENT):
        job = self.job(rules)
        a = self.store.create('submission', 'unit-team', {'source': {}})
        b = self.store.create('submission', 'unit-team', {'source': {}})
        job = self.store.update(job['id'], submission_ids=[a['id'], b['id']])
        workers = []
        for fault in faults:
            w = Mock()
            w.close.return_value = {}
            w.start.side_effect = fault
            w.start.return_value = {'wall_ms': 1}
            workers.append(w)
        with patch.object(self.service, 'ensure_artifact', return_value=(Path(self.tmp.name), {})), patch('arena.sandbox.Worker', side_effect=workers):
            self.service.execute(job)
        return self.store.get(job['id'])

    @staticmethod
    def bot_fault(code='timeout'):
        fault = Fault(code, 'unit bot failure')
        fault.worker_data = {'ok':False, 'code':code}
        return fault

    def test_single_startup_fault_awards_opponent_and_records_side(self):
        job = self.execute_startup([self.bot_fault(), None])
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['report']['result'], {'winner':'blue','reason':'startup_failure'})
        self.assertEqual(job['report']['startup_faults'][0]['side'], 'red')
        self.assertEqual(job['report']['final_state']['ply'], 0)

    def test_both_startup_faults_have_no_winner(self):
        job = self.execute_startup([self.bot_fault(), self.bot_fault('crash')])
        self.assertEqual(job['report']['result'], {'winner':None,'reason':'double_startup_failure'})
        self.assertEqual(len(job['report']['startup_faults']), 2)

    def test_infrastructure_or_untrusted_fault_never_awards_startup_win(self):
        for faults in ([self.bot_fault(), Fault('worker_unavailable', 'Docker unavailable')],
                       [Fault('timeout', 'No trusted worker response'), None]):
            with self.subTest(faults=faults), self.assertRaises(Fault):
                self.execute_startup(faults)
            # Every failed execution report must have no awarded result.
            for job in self.store.list('job'):
                self.assertNotIn('result', job.get('report') or {})

    def test_missing_locked_artifact_requires_restore_without_build(self):
        with patch('arena.sandbox.build') as build, self.assertRaises(Fault) as raised:
            self.service.ensure_artifact(self.job(), {'source':{}, 'source_path':self.tmp.name}, profile(), require_existing=True)
        self.assertEqual(raised.exception.code, 'artifact_missing')
        build.assert_not_called()

    def test_result_validation_fails_closed_and_preserves_legacy(self):
        self.assertTrue(valid_result({'winner':'red','reason':'base_capture'}, LEGACY))
        self.assertFalse(valid_result({'winner':None,'reason':'threefold_repetition'}, LEGACY))
        self.assertTrue(valid_result({'winner':None,'reason':'threefold_repetition'}, CURRENT))
        self.assertFalse(valid_result({'winner':'red','reason':'wall_limit'}, CURRENT))
        self.assertFalse(valid_result({'winner':None,'reason':'worker_error'}, CURRENT))

    def test_practice_api_cannot_consume_reserved_official_slot(self):
        submission = self.store.create('submission','unit-team',{})
        for i in range(15):
            self.service.pending.put_nowait('practice-'+str(i))
        with patch('arena.sandbox.image_id',return_value='unit-image'), self.assertRaises(Fault) as raised:
            self.service.enqueue('unit-team','benchmark',{'submission_id':submission['id']},'reserved-slot-test')
        self.assertEqual(raised.exception.code,'quota_exceeded')
        self.assertEqual(self.store.list('job'),[])


class Priority(unittest.TestCase):
    def test_official_first_fifo_with_reserved_capacity_and_accounting(self):
        pending = JobQueue()
        for i in range(15):
            pending.put_nowait('practice-' + str(i))
        self.assertTrue(pending.practice_full())
        self.assertFalse(pending.full())
        pending.put_official('official')
        self.assertTrue(pending.full())
        self.assertEqual(pending.counts(), {'official':1, 'practice':15})
        self.assertEqual(pending.get_nowait(), 'official')
        pending.task_done()
        for i in range(15):
            self.assertEqual(pending.get_nowait(), 'practice-' + str(i))
            pending.task_done()
        self.assertEqual(pending.unfinished_tasks, 0)
