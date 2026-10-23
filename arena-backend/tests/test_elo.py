import random
import unittest

from arena.elo import apply_round, expected, k_factor, pair
from arena.protocol import Fault


class Elo(unittest.TestCase):
    def test_formula_and_k_boundaries(self):
        self.assertEqual(expected(1500, 1500), .5)
        self.assertAlmostEqual(expected(1900, 1500), 10 / 11)
        self.assertEqual([k_factor(19), k_factor(20), k_factor(100, 1), k_factor(100, 0), k_factor(0, 10, 24)], [32, 16, 32, 16, 24])
        values, counts, changed = apply_round({'a':1500,'b':1500}, [{'id':'1','red':'a','blue':'b','red_score':1,'status':'completed'}])
        self.assertEqual(values, {'a':1516,'b':1484})
        self.assertEqual(counts, {'a':1,'b':1})

    def test_round_is_all_or_nothing_and_finish_order_does_not_matter(self):
        ratings = {'a':1500,'b':1500,'c':1500,'d':1500}
        games = [{'id':'1','red':'a','blue':'b','red_score':1,'status':'completed'}, {'id':'2','red':'b','blue':'a','red_score':.5,'status':'completed'}]
        self.assertEqual(apply_round(ratings, games), apply_round(ratings, reversed(games)))
        with self.assertRaises(Fault):
            apply_round(ratings, [games[0], dict(games[1], status='infrastructure_retry')])
        self.assertEqual(ratings['a'],1500)
        self.assertEqual(apply_round(ratings, [], official=24)[0], ratings)

    def test_bot_change_counter_and_double_startup_loss(self):
        games = [{'id':'1','red':'a','blue':'b','red_score':0,'blue_score':0,'status':'completed'}]
        values, counts, changed = apply_round({'a':1500,'b':1500},games,{'a':30,'b':30},{'a':1,'b':0})
        self.assertEqual(values, {'a':1484,'b':1492})
        self.assertEqual(changed, {'a':0,'b':0})

    def test_pairing_is_near_rank_deterministic_and_avoids_recent(self):
        ratings = {str(i):1500-i for i in range(16)}
        pairs, bye = pair(ratings)
        self.assertIsNone(bye)
        recent = {team:set() for team in ratings}
        for a,b in pairs:
            recent[a].add(b);recent[b].add(a)
        again, _ = pair(ratings,recent=recent)
        self.assertTrue(set(again).isdisjoint(pairs))
        positions = {team:i for i,team in enumerate(ratings)}
        self.assertTrue(all(abs(positions[a]-positions[b])<=10 for a,b in again))
        self.assertEqual(pair(dict(reversed(list(ratings.items()))),recent=recent)[0],again)

    def test_official_avoids_repeats_then_uses_fewest_meetings(self):
        ratings = {str(i):1500 for i in range(4)}
        meetings = {}
        for _ in range(3):
            pairs,_=pair(ratings,meetings=meetings,official=True)
            self.assertTrue(all(p not in meetings for p in pairs))
            for p in pairs:meetings[p]=1
        pairs,_=pair(ratings,meetings=meetings,official=True)
        self.assertTrue(all(meetings[p]==1 for p in pairs))

    def test_odd_bye_rotates_and_unpairable_input_is_explicit(self):
        ratings = {str(i):1500 for i in range(5)}
        byes = {}
        seen=[]
        for n in range(5):
            pairs,bye=pair(ratings,byes=byes)
            self.assertEqual(len(pairs),2)
            seen.append(bye);byes[bye]=(1,n)
        self.assertEqual(len(set(seen)),5)
        with self.assertRaises(Fault):
            pair({'a':1500,'b':1500},recent={'a':{'b'}})


if __name__ == '__main__':
    unittest.main()
