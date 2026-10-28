"""Synthetic state-machine fixtures here; the LAN acceptance executes real bots."""
import json,uuid
from pathlib import Path
from unittest.mock import patch
from arena_api.finals import Finals, snake, STAGES
from arena_api.ranking import head_to_head, group_order, pair_winner
from test_web_api import APIHarness, registration
import test_competitions as competition_fixture

class FinalsTests(APIHarness):
    qualified=competition_fixture.CompetitionAPI.qualified
    post=competition_fixture.CompetitionAPI.post
    def setUp(self):
        super().setUp();self.comp=self.app.state.competitions;self.store=self.app.state.store;self.finals=self.comp.finals
        self.runtime=patch('arena.service.Service.runtime_for',return_value='sha256:'+'1'*64);self.runtime.start();self.addCleanup(self.runtime.stop)

    def create_team(self,name='Team One',username='team-one',change=True):
        # Each synthetic entrant has its own contact, as real teams do.
        with patch('test_web_api.registration',side_effect=lambda name:registration(name,username+'@example.test')):
            return super().create_team(name,username,change)

    def prepared(self,pause=False):
        config=self.comp.config_for(dict(max_teams=16,match_ms=300000))
        self.teams=[];self.bots={}
        for i in range(1,17):
            _,credential=self.create_team('Final Team '+str(i),'final-team-'+str(i));tid=credential['account']['team_id']
            self.teams.append(dict(team_id=tid,online_rank=i));self.bots[tid]=self.qualified(tid,config)
        self.body=dict(name='Chung kết thử',teams=self.teams,match_ms=300000,pause_between_rounds=pause)
        result=self.post(self.client,'/admin/finals',self.body,expected=201)
        self.ident=result['id'];return result

    def start(self):return self.post(self.client,'/admin/finals/'+self.ident+'/start-groups')
    def complete(self,slot):
        cid=self.finals.get(self.ident)['state']['slots'][slot]['competition_id'];rank={t['team_id']:t['online_rank'] for t in self.teams}
        with self.store.db:
            for f in self.comp.rows('SELECT * FROM competition_fixtures WHERE competition_id=?',(cid,)):
                winner='red' if rank[f['red_team_id']]<rank[f['blue_team_id']] else 'blue'
                self.store.db.execute("UPDATE competition_fixtures SET status='completed',result=? WHERE id=?",(json.dumps(dict(winner=winner,reason='base_capture')),f['id']))
            self.store.db.execute("UPDATE competitions SET status='completed' WHERE id=?",(cid,))

    def test_snake_exact(self):
        self.assertEqual(snake([dict(team_id=str(i),online_rank=i) for i in range(1,17)]),dict(A=['1','8','9','16'],B=['2','7','10','15'],C=['3','6','11','14'],D=['4','5','12','13']))

    def test_validation_and_non_admin(self):
        self.prepared();self.body['teams'][0]['online_rank']=2
        self.post(self.client,'/admin/finals',self.body,expected=422)
        self.body['teams'][0]['online_rank']=1;self.body['teams'][0]['team_id']=self.body['teams'][1]['team_id']
        self.post(self.client,'/admin/finals',self.body,expected=422)
        self.post(self.other_client(),'/admin/finals',{},expected=403)
        team,_=self.create_team('Other Team','other-team');self.post(team,'/admin/finals',{},expected=403)

    def test_missing_pass_reports_team_and_guest_cannot_enter(self):
        self.prepared();tid=self.teams[0]['team_id']
        self.store.update(self.bots[tid]['benchmark_id'],status='failed')
        response=self.post(self.client,'/admin/finals',self.body,expected=409)
        self.assertIn('Final Team 1',response['error']['detail'])
        with self.store.db:self.store.db.execute('UPDATE teams SET is_guest=1 WHERE id=?',(tid,))
        response=self.post(self.client,'/admin/finals',self.body,expected=409)
        self.assertIn('đội khách',response['error']['detail'])

    def test_regular_rr_reproduces_the_real_demo(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/demo_rr_results.json').read_text())
        names=sorted({g['red'] for g in fixture['games']}|{g['blue'] for g in fixture['games']});ids={}
        cup=self.comp.create('test',dict(title='Demo results unit fixture',max_teams=8),'demo-rr-unit-create')
        for i,name in enumerate(names):
            _,c=self.create_team(name,'demo-fixture-'+str(i));tid=c['account']['team_id'];ids[name]=tid
            self.qualified(tid,cup['config']);self.comp.add_team('test',tid,cup['id'],'demo-enter-'+str(i))
        self.comp.transition('test',cup['id'],'open','demo-open-key');self.comp.lock('test',cup['id'],'demo-lock-key')
        with self.store.db:
            for game in fixture['games']:
                self.store.db.execute("UPDATE competition_fixtures SET status='completed',result=? WHERE competition_id=? AND red_team_id=? AND blue_team_id=?",(json.dumps(game['result']),cup['id'],ids[game['red']],ids[game['blue']]))
            self.store.db.execute("UPDATE competitions SET status='completed' WHERE id=?",(cup['id'],))
        actual={r['name']:r['rank'] for r in self.comp.standings(cup['id'])['items']}
        self.assertEqual(actual,{'DEMO 4':1,'DEMO 7':2,'DEMO 5':3,'DEMO 3':4,'DEMO 6':5,'DEMO 1':5,'DEMO 2':7,'DEMO 8':8})

    def test_full_bracket_pins_sources_and_duplicate_ticks(self):
        self.prepared()
        first=self.teams[0]['team_id']
        self.bots[first]=self.qualified(first,self.comp.config_for(dict(max_teams=16,match_ms=300000)))
        self.start();self.start();self.finals.tick()
        self.assertEqual(self.finals.get(self.ident)['state']['bots'][first]['submission_id'],self.bots[first]['submission_id'])
        self.assertEqual(len(self.comp.rows('SELECT * FROM competitions')),4)
        for slot in STAGES['groups']:self.complete(slot)
        tid=self.teams[0]['team_id'];new=self.qualified(tid,self.comp.config_for(dict(max_teams=16,match_ms=300000)))
        self.assertNotEqual(new['submission_id'],self.bots[tid]['submission_id'])
        self.assertIn(self.bots[tid]['submission_id'],self.app.state.retention.roles(tid)[1])
        self.finals.tick();self.finals.tick();state=self.finals.get(self.ident)['state']
        cid=state['slots']['TK1']['competition_id']
        self.assertEqual(self.comp.one('SELECT submission_id FROM competition_entries WHERE competition_id=? AND team_id=?',(cid,tid))['submission_id'],self.bots[tid]['submission_id'])
        groups=state['groups'];t=lambda g,n:groups[g][n-1]['team_id']
        self.assertEqual(state['slots']['TK1']['team_ids'],[t('A',1),t('B',2)])
        self.assertEqual(state['slots']['TK4']['team_ids'],[t('D',1),t('C',2)])
        for slot in STAGES['quarter']:self.complete(slot)
        self.finals.tick();state=self.finals.get(self.ident)['state']
        self.assertEqual(state['slots']['BK1']['team_ids'],[state['results'][k]['winner'] for k in ('TK1','TK2')])
        for slot in STAGES['semi']:self.complete(slot)
        self.finals.tick();state=self.finals.get(self.ident)['state']
        self.assertEqual(state['slots']['BRONZE']['team_ids'],[state['results'][k]['loser'] for k in ('BK1','BK2')])
        for slot in STAGES['medals']:self.complete(slot)
        self.finals.tick();self.finals.tick();view=self.finals.view(self.ident)
        self.assertEqual(view['status'],'completed');self.assertEqual(len(view['podium']),3)
        self.assertEqual(len(self.comp.rows('SELECT * FROM competitions')),12)
        self.assertEqual(self.comp.one('SELECT COUNT(*) n FROM competition_fixtures')['n'],64)
        self.assertNotIn('artifact',json.dumps(view));self.assertNotIn('source_path',json.dumps(view))

    def test_attention_waits_and_stop_preserves_children(self):
        self.prepared();self.start()
        for slot in STAGES['groups']:self.complete(slot)
        cid=self.finals.get(self.ident)['state']['slots']['A']['competition_id']
        with self.store.db:
            self.store.db.execute("UPDATE competitions SET status='paused' WHERE id=?",(cid,))
            self.store.db.execute("UPDATE competition_fixtures SET status='attention' WHERE id=(SELECT id FROM competition_fixtures WHERE competition_id=? LIMIT 1)",(cid,))
        self.finals.tick();self.assertEqual(len(self.comp.rows('SELECT * FROM competitions')),4)
        self.finals.stop('test',self.ident)
        with self.store.db:self.store.db.execute("UPDATE competitions SET status='completed' WHERE id=?",(cid,))
        self.finals.tick();self.assertEqual(len(self.comp.rows('SELECT * FROM competitions')),4)
        self.assertEqual(self.finals.get(self.ident)['status'],'stopped')

    def test_pause_between_rounds(self):
        self.prepared(True);self.start()
        for stage in ('groups','quarter','semi','medals'):
            if stage!='groups':
                state=self.finals.view(self.ident,True)
                self.assertTrue(all(state['slots'][key]['status']=='locked' for key in STAGES[stage]))
                self.finals.start_round('test',self.ident)
            for slot in STAGES[stage]:self.complete(slot)
            self.finals.tick()
        self.assertEqual(self.finals.get(self.ident)['status'],'completed')

    def test_restart_after_quarter_create_before_lock(self):
        self.prepared();self.start()
        for slot in STAGES['groups']:self.complete(slot)
        original=self.comp.lock
        def crash(actor,cid,key):
            if ':TK1:lock' in key:raise RuntimeError('synthetic crash after create before lock')
            return original(actor,cid,key)
        with patch.object(self.comp,'lock',side_effect=crash):
            with self.assertRaises(RuntimeError):self.finals.tick()
        self.finals=Finals(self.comp);self.comp.finals=self.finals
        self.finals.tick();self.finals.tick()
        self.assertEqual(len(self.comp.rows('SELECT * FROM competitions')),8)
        for stage in ('quarter','semi','medals'):
            for slot in STAGES[stage]:self.complete(slot)
            self.finals.tick()
        self.assertEqual(self.finals.get(self.ident)['status'],'completed')
        self.assertEqual(len(self.comp.rows('SELECT * FROM competitions')),12)

class RankingTests(APIHarness):
    def test_head_to_head_uses_entire_original_tie_group(self):
        scores={t:dict(team_id=t,points=10,wins=3) for t in 'abc'}
        fixtures=[dict(red_team_id=a,blue_team_id=b,result=dict(winner=w,reason='base_capture')) for a,b,w in [('a','b','red'),('b','c','red'),('a','c','red')]]
        head_to_head(scores,fixtures,dict(win=3,draw=1,loss=0))
        self.assertEqual([scores[t]['head_to_head'] for t in 'abc'],[6,3,0])
        self.assertEqual([r['team_id'] for r in group_order(list(scores.values()),dict(a=3,b=2,c=1))],list('abc'))
        scores['b']['points']=9;head_to_head(scores,fixtures,dict(win=1,draw=.5,loss=0))
        self.assertEqual([scores[t]['head_to_head'] for t in 'abc'],[1,0,0])

    def test_wins_then_unique_online_rank(self):
        rows=[dict(team_id='a',points=3,head_to_head=1,wins=2),dict(team_id='b',points=3,head_to_head=1,wins=1)]
        self.assertEqual(group_order(rows,dict(a=2,b=1))[0]['team_id'],'a')
        rows[1]['wins']=2;self.assertEqual(group_order(rows,dict(a=2,b=1))[0]['team_id'],'b')

    def test_pair_uses_every_tie_break_in_order(self):
        rows=[dict(team_id='a',points=2),dict(team_id='b',points=0)];groups=dict(a=dict(rank=2,points=1),b=dict(rank=1,points=6));online=dict(a=1,b=2)
        self.assertEqual(pair_winner(rows,groups,online),dict(winner='a',loser='b',reason='hơn điểm'))
        rows[1]['points']=2;self.assertEqual(pair_winner(rows,groups,online)['reason'],'đứng cao hơn ở vòng bảng')
        groups['a']['rank']=1;self.assertEqual(pair_winner(rows,groups,online)['reason'],'điểm vòng bảng')
        groups['a']['points']=6;self.assertEqual(pair_winner(rows,groups,online),dict(winner='a',loser='b',reason='hạng online'))
