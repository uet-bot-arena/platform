"""Publisher acceptance uses synthetic public games and privacy canaries."""
import gzip
import json
import os
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch
import uuid

from arena import engine
from arena_api.publisher import Publisher
from test_web_api import APIHarness


class PublisherTests(APIHarness):
    def setUp(self):
        super().setUp()
        self.store,self.comp=self.app.state.store,self.app.state.competitions
        self.item=self.comp.create('test',dict(title='Public fixtures',max_teams=2000,runtime_id='sha256:'+'1'*64),'publisher-create')
        self.ident=self.item['id']
        self.comp.transition('test',self.ident,'open','publisher-open')

    def read(self,name):
        return json.loads(gzip.decompress((self.settings.data/'snapshots'/(name+'.gz')).read_bytes()))

    def publisher(self):
        value=Publisher(self.settings)
        self.addCleanup(value.close)
        return value

    def test_read_only_privacy_gzip_etag_and_visibility_tombstone(self):
        publisher=self.publisher()
        sentinel='PRIVATE-CANARY-do-not-publish'
        with self.store.transaction():
            config=self.comp.get(self.ident)['config'];config['private_canary']=sentinel
            self.store.db.execute('UPDATE competitions SET config=? WHERE id=?',(json.dumps(config),self.ident))
        job=self.store.create('job','competition:'+self.ident,dict(kind='match',status='completed',report={'stderr':sentinel}))
        fixture=uuid.uuid4().hex
        # Public names and contact fields are independent; no real team/account is created.
        with self.store.transaction():
            for team in ('team-a','team-b'):
                self.store.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,?,?,?,?)",(team,team,team,sentinel,sentinel,'[]',1))
                self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(self.ident,team,job['id'],job['id'],json.dumps({'version':'fixture-v1'}),1))
            self.store.db.execute("INSERT INTO competition_fixtures(id,competition_id,ordinal,red_team_id,blue_team_id,status,current_job_id,result) VALUES(?,?,1,'team-a','team-b','completed',?,?)",(fixture,self.ident,job['id'],json.dumps(dict(winner='red',reason='base_capture'))))
        self.store.event(job['id'],'build',{'stderr':sentinel})
        for n in range(6):
            self.store.event(job['id'],'state',{'state':dict(engine.initial_state(),ply=n),'stderr':sentinel})
        self.store.event(job['id'],'completed',{'result':{'winner':'red','reason':'base_capture'},'private':sentinel})
        with self.assertRaises(sqlite3.OperationalError):
            publisher.db.execute("UPDATE competitions SET title='must not write'")
        publisher.db.rollback()
        result=publisher.publish();self.assertLess(result['seconds'],10)
        latest=self.read('competitions.json')['items'][0]['recent_completed']
        self.assertEqual(latest['id'],fixture)
        self.assertGreater(latest['completed_ms'],0)
        for path in publisher.output.rglob('*.gz'):
            self.assertNotIn(sentinel,gzip.decompress(path.read_bytes()).decode(),path)
        self.assertFalse(list(publisher.output.rglob('*.tmp')))
        guest=self.other_client()
        response=guest.get('/snapshots/competitions.json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-encoding'],'gzip')
        self.assertEqual(response.headers['cache-control'],'public, max-age=1, s-maxage=5')
        etag=response.headers['etag']
        self.assertEqual(guest.get('/snapshots/competitions.json',headers={'If-None-Match':etag}).status_code,304)
        self.assertEqual(guest.get('/snapshots/.cursor').status_code,404)
        self.assertEqual(guest.get('/snapshots/../arena.sqlite').status_code,404)
        board=self.read(f'fixtures/{self.ident}/{fixture}.json')
        self.assertEqual([e['type'] for e in board['items']],['state']*6+['completed'])
        featured=self.read('featured.json')['boards'][0]
        self.assertTrue(featured['summary_only'])
        self.assertEqual([e['type'] for e in featured['items']],['state','completed'])
        self.assertEqual(featured['items'][0]['data']['state']['ply'],5)
        with self.store.transaction():
            self.store.db.execute('UPDATE competitions SET archived_ms=123 WHERE id=?',(self.ident,))
        publisher.publish()
        self.assertEqual(self.read('competitions.json')['items'],[])
        self.assertEqual(guest.get(f'/snapshots/fixtures/{self.ident}/{fixture}.json').status_code,404)
        with self.store.transaction():
            self.store.db.execute('UPDATE competitions SET archived_ms=NULL WHERE id=?',(self.ident,))
        publisher.publish()
        self.assertEqual(guest.get(f'/snapshots/fixtures/{self.ident}/{fixture}.json').status_code,200)
        self.assertEqual(len(self.read(f'fixtures/{self.ident}/{fixture}.json')['items']),7)

    def test_fixture_window_keeps_all_unfinished_games_and_only_recent_completed_games(self):
        source=self.store.create('submission','fixture',dict(version='unit-fixture'))
        active=self.store.create('job','competition:'+self.ident,dict(kind='match',status='playing',report=None,submission_ids=[]))
        teams=[f'window-team-{n}' for n in range(50)]
        with self.store.transaction():
            for team in teams:
                self.store.db.execute('INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,?,?,?,?)',(team,team,team,'fixture@example.invalid','','[]',1))
                self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(self.ident,team,source['id'],source['id'],json.dumps(dict(version='unit-fixture')),1))
            pairs=[(a,b) for a in teams for b in teams if a!=b][:2300]
            self.store.db.executemany('INSERT INTO competition_fixtures(id,competition_id,ordinal,red_team_id,blue_team_id,status,current_job_id) VALUES(?,?,?,?,?,?,?)',
                ((f'window-{n}',self.ident,n,a,b,'queued' if n==1 else 'pending' if n<=1100 else 'completed',active['id'] if n==1 else None) for n,(a,b) in enumerate(pairs,1)))
        publisher=self.publisher();publisher.schedule(self.ident)
        from unittest.mock import patch
        for n in range(20):self.store.event(active['id'],'state',dict(state=dict(engine.initial_state(),ply=n)))
        row=publisher.fixture_row('window-1')
        publisher.fixture(row)
        self.store.event(active['id'],'state',dict(state=dict(engine.initial_state(),ply=20)))
        with patch.object(publisher.comp,'safe_events',wraps=publisher.comp.safe_events) as read:
            featured=publisher.fixture(row)
            self.assertNotIn(0,[call.args[1] for call in read.call_args_list])
        self.assertEqual(len(self.read(f'fixtures/{self.ident}/window-1.json')['items']),21)
        self.assertEqual(len(featured['items']),1)
        self.assertEqual(featured['items'][0]['data']['state']['ply'],20)
        self.store.update(active['id'],replay_pruned=True)
        with self.store.transaction():self.store.db.execute('DELETE FROM events WHERE job=?',(active['id'],))
        publisher.fixture(row)
        self.assertEqual(self.read(f'fixtures/{self.ident}/window-1.json')['items'],[])

        value=self.read(f'fixtures/{self.ident}.json')
        self.assertEqual((len(value['items']),value['history_total']),(2100,2300))
        self.assertEqual(value['items'][0]['id'],'window-1')
        self.assertEqual(sum(r['status']=='completed' for r in value['items']),1000)
        self.assertNotIn('window-1101',{r['id'] for r in value['items']})

    def test_top_100_separate_team_lookup_and_csv_formula_escaping(self):
        with self.store.transaction():
            config=self.comp.get(self.ident)['config']
            config.update(format='elo',ladder=dict(round_hours=2),official=dict(general_rounds=20,group_size=64,finalists=16,k=[[20,32],[None,16]]))
            self.store.db.execute('UPDATE competitions SET config=?,submission_deadline=? WHERE id=?',(json.dumps(config),1_900_000_000_000,self.ident))
            for n in range(101):
                ident=f'team-{n:03}'
                name='=FORMULA()' if n==100 else f'Team {n}'
                self.store.db.execute('INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,?,?,?,?)',(ident,name,ident,'private@example.invalid','','[]',1))
                self.store.db.execute("INSERT INTO elo_ratings VALUES(?,?,'ladder',?,0,0,NULL)",(self.ident,ident,2000-n))
        publisher=self.publisher();publisher.publish()
        value=self.read(f'standings/{self.ident}.json')
        self.assertEqual((len(value['items']),value['total']),(100,101))
        self.assertEqual(self.read(f'standings/{self.ident}/team-100.json')['rank'],101)
        text=gzip.decompress((publisher.output/f'standings/{self.ident}.csv.gz').read_bytes()).decode('utf-8-sig')
        self.assertIn("'=FORMULA()",text)
        old=(publisher.output/f'standings/{self.ident}/team-000.json.gz').stat().st_mtime_ns
        publisher.publish()
        self.assertEqual((publisher.output/f'standings/{self.ident}/team-000.json.gz').stat().st_mtime_ns,old)

    def test_separate_process_refreshes_within_10_seconds(self):
        env={**os.environ,'ARENA_CONFIG':'none','ARENA_SECRETS':'none','ARENA_API_DATA':str(self.settings.data)}
        process=subprocess.Popen([sys.executable,'-m','arena_api','publish'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def wait_title(title):
            until=time.monotonic()+9
            while time.monotonic()<until:
                self.assertIsNone(process.poll(), 'Publisher process exited')
                try:
                    if self.read('competitions.json')['items'][0]['title']==title:return
                except (FileNotFoundError,IndexError):pass
                time.sleep(.05)
            self.fail('Snapshot did not refresh within 10 seconds')
        try:
            wait_title('Public fixtures')
            started=time.monotonic()
            with self.store.transaction():
                self.store.db.execute("UPDATE competitions SET title='Changed public title' WHERE id=?",(self.ident,))
            wait_title('Changed public title')
            self.assertLess(time.monotonic()-started,10)
        finally:
            process.terminate();process.communicate(timeout=10)
