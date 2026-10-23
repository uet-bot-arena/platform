import random
import unittest

from arena.elo import apply_round, expected, k_factor, pair, top_group, general_pair, rank, select, round_robin_schedule, round_robin_rank, official_phase
from arena.protocol import Fault


class Elo(unittest.TestCase):
    def test_official_k_counts_games_not_rounds_or_ladder_games(self):
        self.assertEqual([k_factor(n, 10, True) for n in (0,19,20,59,60,200)], [32,32,16,16,16,16])
        games = [dict(id=str(i),red='a',blue='b',red_score=.5,status='completed') for i in range(2)]
        _, counts, _ = apply_round({'a':1500,'b':1500}, games, {'a':19,'b':59}, official=True)
        self.assertEqual(counts, {'a':21,'b':61})

    def test_small_season_skips_general_at_configured_group_threshold(self):
        self.assertEqual(official_phase(64),'round_robin')
        self.assertEqual(official_phase(20),'round_robin')
        self.assertEqual(official_phase(65),'general')
        self.assertEqual(official_phase(1000,19),'general')
        self.assertEqual(official_phase(1000,20),'round_robin')
        self.assertEqual(official_phase(20,0,8,3),'general')
        self.assertEqual(official_phase(20,3,8,3),'round_robin')

    def test_round_robin_schedule_has_both_colors_exactly_once_without_byes(self):
        from collections import Counter
        for n in (2,7,20,64):
            names=[str(i) for i in range(n)]
            schedule=round_robin_schedule(reversed(names))
            self.assertEqual(len(schedule),n*(n-1))
            self.assertEqual(len(set(schedule)),len(schedule))
            self.assertTrue(all((b,a) in schedule and a!=b for a,b in schedule))
            self.assertEqual(set(Counter(t for g in schedule for t in g).values()),{2*(n-1)})

    def test_round_robin_points_head_to_head_wins_and_frozen_elo(self):
        def game(i,a,b,score):
            return dict(id=str(i),red=a,blue=b,red_score=score,status='completed')
        ratings=dict(a=1600,b=1500,c=1400,d=1700)
        # a/b have equal points and wins; a wins their direct encounter.
        games=[game(1,'a','b',1),game(2,'b','c',1),game(3,'c','d',1)]
        rows=round_robin_rank(ratings,games)
        self.assertEqual([r['team_id'] for r in rows],['a','b','c','d'])
        self.assertEqual([r['points'] for r in rows],[1,1,1,0])
        self.assertEqual([r['head_to_head'] for r in rows],[1,1,0,0])
        self.assertEqual(rows,round_robin_rank(ratings,reversed(games)))
        self.assertEqual(ratings,dict(a=1600,b=1500,c=1400,d=1700))
        tied=round_robin_rank(dict(a=1500,b=1500),[game(1,'a','b',.5)])
        with self.assertRaises(Fault):select(tied,1)
        wins=round_robin_rank(dict(a=1400,b=1800,c=1300,d=1200),
            [game(1,'a','c',1),game(2,'b','c',.5),game(3,'b','d',.5)])
        self.assertEqual(wins[0]['team_id'],'a')
        frozen=round_robin_rank(dict(a=1400,b=1800),[game(1,'a','b',.5)])
        self.assertEqual(frozen[0]['team_id'],'b')
        with self.assertRaises(Fault):round_robin_rank(ratings,[dict(games[0],status='pending')])

    def test_cut_group_and_small_season(self):
        ratings = {str(i):i for i in range(100)}
        self.assertEqual(len(top_group(ratings)),64)
        self.assertEqual(min(top_group(ratings).values()),36)
        self.assertEqual(top_group({'a':1500,'b':1400}),{'a':1500,'b':1400})
        with self.assertRaises(ValueError): top_group(ratings,0)

    def test_general_round_never_repeats_even_if_no_pairing_exists(self):
        ratings={'a':1500,'b':1500,'c':1500,'d':1500}
        meetings={}
        for _ in range(3):
            pairs,_=pair(ratings,meetings=meetings,official=True,no_repeat=True)
            for p in pairs:
                self.assertNotIn(p,meetings)
                meetings[p]=1
        with self.assertRaises(Fault): pair(ratings,meetings=meetings,official=True,no_repeat=True)

    def test_general_widens_only_to_smallest_feasible_band(self):
        ratings={str(i):1500-i for i in range(8)}
        meetings={('0','1'):1,('0','2'):1}
        pairs,bye,radius=general_pair(ratings,meetings,neighbor_ranks=2)
        self.assertEqual(radius,3)
        self.assertIsNone(bye)
        self.assertEqual(len(pairs),4)
        self.assertTrue(all(p not in meetings for p in pairs))
        self.assertEqual(general_pair(ratings,neighbor_ranks=2)[2],2)
        with self.assertRaises(Fault): general_pair({'a':1500,'b':1500},{('a','b'):1})

    def test_augmented_pairings_cover_every_team_and_general_never_repeats(self):
        rng=random.Random(20261023)
        ratings={str(i):rng.gauss(1500,200) for i in range(40)}
        meetings={}
        for _ in range(20):
            pairs,bye,radius=general_pair(ratings,meetings)
            self.assertIsNone(bye)
            self.assertEqual(sorted(t for p in pairs for t in p),sorted(ratings))
            positions={t:i for i,t in enumerate(sorted(ratings,key=lambda t:(-ratings[t],t)))}
            self.assertTrue(all(abs(positions[a]-positions[b])<=radius for a,b in pairs))
            if radius>10:
                with self.assertRaises(Fault):
                    pair(ratings,meetings=meetings,neighbor_ranks=radius-1,official=True,no_repeat=True)
            for a,b in pairs:
                self.assertNotIn((a,b),meetings)
                meetings[a,b]=1
                ratings[a]+=rng.uniform(-20,20);ratings[b]+=rng.uniform(-20,20)

    def test_legacy_pairing_repeat_cost_matches_exact_blossom(self):
        import networkx as nx
        rng=random.Random(31415)
        for _ in range(40):
            ratings={str(i):rng.random()*2000 for i in range(12)}
            positions={t:i for i,t in enumerate(sorted(ratings,key=lambda t:(-ratings[t],t)))}
            meetings={}
            graph=nx.Graph(); graph.add_nodes_from(ratings)
            names=sorted(ratings)
            for i,a in enumerate(names):
                for b in names[i+1:]:
                    meetings[a,b]=rng.randrange(4)
                    if abs(positions[a]-positions[b])<=10:
                        graph.add_edge(a,b,weight=-meetings[a,b])
            wanted=nx.max_weight_matching(graph,maxcardinality=True)
            actual,_=pair(ratings,meetings=meetings,official=True)
            self.assertEqual(sum(meetings[tuple(sorted(p))] for p in actual),
                             sum(meetings[tuple(sorted(p))] for p in wanted))

    def test_exact_float_weights_preserve_blossom_matching_including_uniform_ties(self):
        import networkx as nx
        rng=random.Random(271828)
        for uniform in (False,True):
            for _ in range(40):
                integer=nx.Graph(); integer.add_nodes_from(range(32))
                floating=nx.Graph(); floating.add_nodes_from(range(32))
                for a in range(32):
                    for b in range(a+1,min(32,a+11)):
                        if rng.random()<.15:continue
                        weight=1 if uniform else -rng.randrange(8)
                        integer.add_edge(a,b,weight=weight)
                        floating.add_edge(a,b,weight=float(weight))
                self.assertEqual(nx.max_weight_matching(integer,maxcardinality=True),
                                 nx.max_weight_matching(floating,maxcardinality=True))

    def test_tie_breaks_use_wins_then_head_to_head_and_never_id_for_a_place(self):
        ratings=dict(a=1500,b=1500,c=1500)
        games=[dict(red='a',blue='b',red_score=1),dict(red='b',blue='c',red_score=1)]
        rows=rank(ratings,games)
        self.assertEqual([r['team_id'] for r in rows],['a','b','c'])
        self.assertEqual([r['rank'] for r in rows],[1,2,3])
        self.assertEqual(select(rows,2),rows[:2])
        tied=rank(dict(a=1500,b=1500,c=1400),[])
        self.assertEqual([r['rank'] for r in tied],[1,1,3])
        with self.assertRaises(Fault): select(tied,1)
        self.assertEqual(len(select(tied,64)),3)

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
