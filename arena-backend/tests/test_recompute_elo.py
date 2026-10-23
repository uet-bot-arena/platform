import importlib.util
from pathlib import Path
import unittest
from arena.protocol import Fault

spec=importlib.util.spec_from_file_location('recompute_elo',Path(__file__).resolve().parents[2]/'tools/recompute_elo.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class RecomputeElo(unittest.TestCase):
    def record(self):
        game=dict(id='001',red='a',blue='b',red_score=.5,status='completed')
        return dict(initial_ratings={'a':1500,'b':1500},rounds=[
            dict(id='ladder',phase='ladder',games=[dict(game,id=str(i)) for i in range(20)]),
            dict(id='general',phase='general',games=[dict(game,red_score=1)]),
            dict(id='top',phase='top',games=[dict(game,red_score=.5)])])

    def test_official_counter_resets_after_ladder_but_continues_into_top_group(self):
        result=module.recompute(self.record())
        self.assertEqual(result['ladder_played'],{'a':20,'b':20})
        self.assertEqual(result['official_played'],{'a':2,'b':2})
        self.assertAlmostEqual(result['ratings']['a'],1514.5304984710245)
        self.assertAlmostEqual(result['ratings']['b'],1485.4695015289755)
        self.assertEqual(result['standings'][0]['team_id'],'a')

    def test_round_robin_scores_points_and_keeps_general_ratings_and_counts(self):
        from arena.elo import round_robin_rank
        record=self.record();record['rounds']=record['rounds'][:2]
        before=module.recompute(record)
        games=[dict(id='1',red='a',blue='b',red_score=0,status='completed'),
               dict(id='2',red='b',blue='a',red_score=1,status='completed')]
        record['rounds'].append(dict(id='rr',phase='round_robin',teams=['a','b'],games=games))
        result=module.recompute(record)
        self.assertEqual(result['ratings'],before['ratings'])
        self.assertEqual(result['official_played'],before['official_played'])
        self.assertEqual(result['standings'],round_robin_rank(before['ratings'],games))
        self.assertEqual(result['standings'][0]['team_id'],'b')

    def test_refuses_incomplete_round_duplicate_round_and_wrong_stage_order(self):
        record=self.record();record['rounds'][1]['games'][0]['status']='queued'
        with self.assertRaises(Fault):module.recompute(record)
        record=self.record();record['rounds'][2]['id']='general'
        with self.assertRaises(ValueError):module.recompute(record)
        record=self.record();record['rounds'].append(dict(id='late',phase='ladder',games=[]))
        with self.assertRaises(ValueError):module.recompute(record)
