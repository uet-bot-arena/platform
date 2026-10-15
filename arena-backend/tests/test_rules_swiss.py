import copy
import unittest
from arena import engine, basic_rules
from arena.config import request_profile, scale_profile, stored_profile
from arena_api.swiss import pair


class BasicRules(unittest.TestCase):
    def test_default_is_exactly_classic_and_scaled_profiles_round_trip(self):
        self.assertEqual(basic_rules.Rules({}).initial_state(), engine.initial_state())
        for size in (7,8,10,15):
            p = scale_profile(request_profile({}),size*size)
            self.assertEqual(stored_profile(p.public()),p)

    def test_all_board_sizes_coordinates_legal_moves_and_observation(self):
        for size in (7,8,10,15):
            rules = basic_rules.Rules({"rows":size,"cols":size})
            state = rules.initial_state()
            self.assertEqual(len(state["board"]),size)
            self.assertEqual(engine.counts(state),{s:{k:3 for k in engine.BEATS} for s in engine.SIDES})
            for r in range(size):
                for c in range(size):
                    self.assertEqual(engine.position(engine.square(r,c,size),size,size),(r,c))
            for _ in range(10):
                if state["result"]:break
                obs=rules.observation(state)
                state=rules.apply(state,obs["legal_moves"][0])
            self.assertEqual(rules.observation(state)["bases"]["blue"],chr(96+size)+str(size))

    def test_two_types_and_disabled_or_relocated_base(self):
        state={"board":[[None]*10 for _ in range(10)],"to_move":"red","ply":0,"result":None,
               "basic_rules":basic_rules.normalize({"rows":10,"cols":10,"bases":{"red":"b1","blue":"f6"},"eliminate_types":2,"base_capture":False})}
        for cell,side,kind in (("e5","red","rock"),("f6","blue","scissors"),("a10","blue","rock"),("b10","blue","paper")):
            r,c=engine.position(cell,10,10);state["board"][r][c]={"side":side,"kind":kind}
        self.assertIsNone(engine.apply(state,{"from":"e5","to":"f6"})["result"])
        state["basic_rules"]["base_capture"]=True
        self.assertEqual(engine.apply(state,{"from":"e5","to":"f6"})["result"]["reason"],"base_capture")
        state["basic_rules"]["base_capture"]=False
        state["board"][0][1]=None
        self.assertEqual(engine.apply(state,{"from":"e5","to":"f6"})["result"]["reason"],"type_eliminated")

    def test_invalid_manual_placements_and_tampered_hash(self):
        for definition in ({"rows":True},{"bases":{"red":"a1","blue":"a1"}},{"eliminate_types":3},{"placement":[]},{"rows":7,"bases":{"red":"a1","blue":"h8"}}):
            with self.assertRaises(ValueError):basic_rules.normalize(definition)
        pack=basic_rules.snapshot({"rows":10,"cols":10})
        pack["definition"]["rows"]=9
        with self.assertRaises(ValueError):basic_rules.provider(pack)

    def test_manual_placement_rejects_a_side_with_no_initial_move(self):
        red={(1,1):'rock',(3,3):'paper',(5,5):'scissors'}
        placement=[]
        for r in range(7):
            for c in range(7):
                cell=engine.square(r,c,7)
                if cell in ('a1','g7'):continue
                if (r,c) in red:
                    side,kind='red',red[r,c]
                else:
                    forbidden={engine.BEATS[k] for (rr,cc),k in red.items() if max(abs(r-rr),abs(c-cc))<=1}
                    side,kind='blue',next(k for k in engine.BEATS if k not in forbidden)
                placement.append({'square':cell,'side':side,'kind':kind})
        with self.assertRaisesRegex(ValueError,'legal move'):
            basic_rules.normalize({'rows':7,'cols':7,'placement':placement})


class Swiss(unittest.TestCase):
    def test_multiple_rounds_never_repeat_opponent_or_bye(self):
        for count in (5,16,40):
            teams=[f"team-{i:03}" for i in range(count)]
            points={t:0 for t in teams};opponents={t:set() for t in teams};byes=set()
            for round_number in range(min(5,count-1)):
                pairs,bye=pair(teams,points,opponents,byes)
                self.assertEqual(len(pairs),count//2)
                if bye:
                    self.assertNotIn(bye,byes);byes.add(bye);points[bye]+=3
                for a,b in pairs:
                    self.assertNotIn(b,opponents[a]);opponents[a].add(b);opponents[b].add(a)
                    points[a]+=6 if round_number%2 else 3
                    points[b]+=0 if round_number%2 else 3
            self.assertEqual(len(byes),min(5,count-1) if count%2 else 0)
