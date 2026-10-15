"""Engine mechanics and API boundaries; real Docker coverage lives separately."""
from copy import deepcopy
import json
import random
import unittest
import uuid
from unittest.mock import patch

from arena import engine, experimental
from arena.protocol import Fault, response
from arena.service import fixtures
from test_web_api import APIHarness


class ExperimentalEngine(unittest.TestCase):
    def setUp(self):
        self.d = deepcopy(experimental.PRESETS['domination'])
        self.r = experimental.Rules(self.d)

    def sparse(self, pieces):
        s = self.r.initial_state()
        s['board'] = [[None for _ in range(9)] for _ in range(9)]
        for square, side, kind in pieces:
            row, col = engine.position(square)
            s['board'][row][col] = dict(side=side, kind=kind, id=side+'-'+square, cooldowns={}, shield_until=0)
        s['energy'] = dict(red=5, blue=5)
        return s

    def act(self, s, **fields):
        a = next(a for a in self.r.legal_actions(s) if all(a.get(k)==v for k,v in fields.items()))
        return self.r.transition(s, {'action_id': a['id']})

    def test_schema_bounds_and_warnings(self):
        self.assertTrue(experimental.validate(self.d)['valid'])
        changes = [lambda d:d.update(code='exec'), lambda d:d['energy'].update(cap=True),
                   lambda d:d['zones'].append(dict(square='a1',kind='energy',gain=1)),
                   lambda d:d['modifiers'][0]['filter'].update(kinds=[]),
                   lambda d:d['modifiers'][0].pop('filter'),
                   lambda d:d['modifiers'][0].update(pattern='teleport'),
                   lambda d:d.update(modifiers=[dict(id='wide',filter={},pattern='king',range=3,cost=0,cooldown=0)])]
        for change in changes:
            d=deepcopy(self.d);change(d)
            with self.subTest(d=d), self.assertRaises(Fault):experimental.validate(d)
        d=deepcopy(self.d);d['zones'].pop(0)
        self.assertTrue(experimental.validate(d)['warnings'])

    def test_dash_cost_path_filter_and_cooldown(self):
        s=self.sparse([('d4','red','scissors'),('a7','blue','rock')])
        out,trace=self.act(s, type='move', **{'from':'d4','to':'d6','ability':'dash'})
        self.assertEqual(out['energy']['red'],3)
        self.assertEqual(out['board'][3][3]['cooldowns'],{'dash':2})
        self.assertEqual(trace[0]['ability'],'dash')
        out['to_move']='red'
        self.assertFalse(any(a.get('ability')=='dash' for a in self.r.legal_actions(out)))
        out['turns']['red']=2
        self.assertTrue(any(a.get('ability')=='dash' for a in self.r.legal_actions(out)))
        s['board'][4][3]=dict(side='red',kind='paper',id='red-c2',cooldowns={},shield_until=0)
        self.assertFalse(any(a.get('from')=='d4' and a.get('to')=='d6' for a in self.r.legal_actions(s)))
        s['energy']['red']=1
        self.assertFalse(any(a.get('ability')=='dash' for a in self.r.legal_actions(s)))
        self.assertFalse(self.r.matches(dict(kind='scissors',id='red-a2'),{'kinds':['scissors'],'piece_ids':['blue-i8']}))

    def test_shield_blocks_capture_and_expires_by_owner_turns(self):
        s=self.sparse([('d4','red','scissors'),('e5','blue','paper')]);s['to_move']='blue'
        s,_=self.act(s,type='shield', **{'from':'e5'})
        self.assertEqual(s['board'][4][4]['shield_until'],3)
        s,trace=self.act(s,type='move', **{'from':'d4','to':'e5','ability':''})
        self.assertEqual(s['board'][5][3]['side'],'red')
        self.assertEqual(s['board'][4][4]['side'],'blue')
        self.assertEqual(s['board'][4][4]['shield_until'],0)
        self.assertTrue(trace[0]['blocked']);self.assertEqual(trace[1]['type'],'shield_broken')
        s=self.sparse([('d4','red','scissors'),('h7','blue','paper')])
        s,_=self.act(s,type='shield', **{'from':'d4'})
        for origin,dest in [('h7','h8'),('d4','d5'),('h8','h7'),('d5','d4')]:
            s,_=self.act(s,type='move', **{'from':origin,'to':dest,'ability':''})
        self.assertEqual(s['board'][5][3]['shield_until'],0)

    def test_resource_reward_control_win_and_capture_priority(self):
        s=self.sparse([('c4','red','rock'),('h7','blue','paper')]);s['energy']['red']=4
        s,trace=self.act(s,type='move', **{'from':'c4','to':'c5','ability':''})
        self.assertEqual(s['energy'],dict(red=5,blue=5))
        self.assertEqual(trace[-1],dict(type='energy_gained',side='red',square='c5',amount=1))
        s=self.sparse([('e4','red','rock'),('h7','blue','paper')]);s['control']['red']=5
        s,_=self.act(s,type='move', **{'from':'e4','to':'e5','ability':''})
        self.assertEqual(s['result'],dict(winner='red',reason='control_target'))
        s=self.sparse([('h8','red','rock'),('b8','blue','paper'),('e5','red','scissors')]);s['control']['red']=5
        s,trace=self.act(s,type='move', **{'from':'h8','to':'i9','ability':''})
        self.assertEqual(s['result']['reason'],'base_capture')
        self.assertFalse(any(e['type']=='control_gained' for e in trace))

    def test_rejection_is_pure_and_position_key_keeps_relative_rights(self):
        s=self.r.initial_state();before=deepcopy(s)
        for a in ({'action_id':'a999'},{'from':'a2','to':'a3'},{'action_id':'a0','extra':0}):
            with self.assertRaises(ValueError):self.r.apply(s,a)
        self.assertEqual(s,before)
        t=deepcopy(s);t['turns']=dict(red=10,blue=10);t['ply']=20
        self.assertEqual(self.r.position_key(s),self.r.position_key(t))
        t['board'][7][0]['shield_until']=11
        self.assertNotEqual(self.r.position_key(s),self.r.position_key(t))
        t=deepcopy(s);t['energy']['red']-=1
        self.assertNotEqual(self.r.position_key(s),self.r.position_key(t))

    def test_fixtures_deterministic_and_observations_bounded(self):
        rng=random.Random(42);s=self.r.initial_state()
        for _ in range(200):
            legal=self.r.legal_actions(s)
            if not legal:s=self.r.initial_state();continue
            self.assertLess(len(json.dumps(self.r.observation(s)).encode()),60000)
            a={'action_id':rng.choice(legal)['id']}
            self.assertEqual(self.r.apply(s,a),self.r.apply(s,a));s=self.r.apply(s,a)
        self.assertEqual(fixtures(self.r),fixtures(self.r))
        self.assertIs(experimental.provider(),engine)

    def test_v3_ready_requires_hash_capability_and_stale_action_rejected(self):
        expected=dict(protocol_version=3,match_id='m',payload={'ruleset':{'hash':'h','requires':['rules-v1']}})
        ready=dict(protocol_version=3,type='ready',match_id='m',ruleset_hash='h',capabilities=['rules-v1'])
        self.assertEqual(response(json.dumps(ready),expected,'ready'),ready)
        for change in ({'ruleset_hash':'other'},{'capabilities':[]},{'protocol_version':2}):
            with self.assertRaises(Fault):response(json.dumps({**ready,**change}),expected,'ready')
        expected.update(turn_id=2,state_version=2)
        action=dict(protocol_version=3,type='action',match_id='m',turn_id=1,state_version=2,payload={'action_id':'a0'})
        with self.assertRaises(Fault):response(json.dumps(action),expected)


class ExperimentalAPI(APIHarness):
    def post(self,path,body=None,status=200,key=None,client=None):
        r=(client or self.client).post('/api/v1'+path,json=body or {},headers={'Idempotency-Key':key or uuid.uuid4().hex})
        self.assertEqual(r.status_code,status,r.text);return r.json()

    def publish(self):
        r=self.post('/admin/rulesets',{'definition':deepcopy(experimental.PRESETS['domination'])},201)
        for verb in ('validate','publish'):
            r=self.post('/admin/rulesets/'+r['id']+'/'+verb,{'expected_revision':r['revision']})
        return r

    def test_default_off_rbac_csrf_preview_and_lifecycle(self):
        self.assertFalse(self.client.get('/api/v1/admin/rulesets').json()['enabled'])
        guest=self.other_client();self.assertEqual(guest.get('/api/v1/rulesets').status_code,401)
        self.assertEqual(guest.get('/api/v1/experimental/guide').status_code,200)
        team,_=self.create_team()
        self.assertEqual(team.get('/api/v1/admin/rulesets').status_code,403)
        self.assertEqual(self.client.post('/api/v1/admin/experimental',json={'enabled':True,'expected_revision':0},headers={'X-CSRF-Token':'bad','Idempotency-Key':uuid.uuid4().hex}).status_code,403)
        d=deepcopy(experimental.PRESETS['domination'])
        preview=self.post('/admin/rulesets/preview',{'definition':d,'actions':['a0']})
        self.assertEqual(preview['observation']['state']['ply'],1)
        self.post('/admin/rulesets/preview',{'definition':d,'actions':['expired']},400)
        self.assertFalse(self.app.state.store.list('job'))
        key=uuid.uuid4().hex
        first=self.post('/admin/rulesets',{'definition':d},201,key)
        self.assertEqual(first,self.post('/admin/rulesets',{'definition':d},201,key))
        self.post('/admin/rulesets/'+first['id']+'/publish',{'expected_revision':1},409)
        r=self.publish();saved=deepcopy(r['definition'])
        self.post('/admin/rulesets/'+r['id']+'/edit',{'expected_revision':r['revision'],'definition':d},409)
        clone=self.post('/admin/rulesets/'+r['id']+'/clone',{'expected_revision':r['revision']})
        self.assertEqual(clone['version'],2);self.assertEqual(clone['parent_id'],r['id'])
        self.post('/admin/rulesets/'+r['id']+'/archive',{'expected_revision':r['revision']})
        self.assertEqual(self.app.state.store.get(r['id'])['definition'],saved)
        self.assertFalse(team.get('/api/v1/rulesets').json()['items'])

    def test_pinning_flag_qualification_and_submission_protocol(self):
        r=self.publish();catalog=self.app.state.rulesets
        with self.assertRaises(Fault):catalog.resolve(r['id'])
        self.post('/admin/experimental',{'enabled':True,'expected_revision':0})
        self.post('/admin/experimental',{'enabled':True,'expected_revision':0},409)
        team,credential=self.create_team();team_id=credential['account']['team_id']
        store=self.app.state.store
        starter=team.get('/api/v1/experimental/starter.zip')
        self.assertEqual(starter.status_code,200)
        source=team.post('/api/v1/submissions',content=starter.content,headers={'Idempotency-Key':uuid.uuid4().hex,'Content-Type':'application/zip','X-Submission-Version':'v1.0.0','X-Arena-Protocol':'3'})
        self.assertEqual(source.status_code,201,source.text);source=source.json()
        self.assertEqual(source['protocol_version'],3)
        with patch('arena.sandbox.image_id',return_value='sha256:'+'1'*64):
            job=self.post('/benchmarks',{'submission_id':source['id'],'ruleset_id':r['id']},202,client=team)
            comp=self.post('/admin/competitions',{'title':'Experimental test','max_teams':2,'ruleset_id':r['id']},201)
        self.assertEqual(job['ruleset'],comp['config']['ruleset'])
        self.assertEqual(comp['config']['protocol_version'],3)
        self.post('/admin/experimental',{'enabled':False,'expected_revision':1})
        self.post('/benchmarks',{'submission_id':source['id'],'ruleset_id':r['id']},409,client=team)
        self.assertTrue(experimental.provider(job['ruleset']).legal_actions(experimental.provider(job['ruleset']).initial_state()))
        tampered=deepcopy(job['ruleset']);tampered['definition']['energy']['cap']=9
        with self.assertRaises(Fault):experimental.provider(tampered)
        # Even matching runtime metrics must not qualify a different ruleset.
        config=comp['config'];artifact=dict(source=source['source'],image_id=config['image_id'],sdk_sha256=config['sdk_sha256'],flags=config['build_flags'],artifact_sha256='a'*64)
        report=dict(passed=True,samples=[{}]*72,turn_wall_ms={'samples':72},profile=config['profile'],image_id=config['image_id'],engine_sha256=config['engine_sha256'],suite_id=config['suite_id'],suite_sha256=config['suite_sha256'],artifacts=[artifact],ruleset=job['ruleset'])
        store.update(job['id'],status='completed',report=report)
        self.app.state.competitions.qualification(config,team_id,source['id'],job['id'])
        report['ruleset']={**job['ruleset'],'hash':'0'*64};store.update(job['id'],report=report)
        with self.assertRaises(Fault):self.app.state.competitions.qualification(config,team_id,source['id'],job['id'])
        self.post('/admin/experimental',{'enabled':True,'expected_revision':2})
        store.update(source['id'],protocol_version=2)
        self.post('/benchmarks',{'submission_id':source['id'],'ruleset_id':r['id']},400,client=team)


if __name__=='__main__':unittest.main()
