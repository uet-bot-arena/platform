import json
from test_elo_rounds import EloFixture
import test_elo_scheduler as scheduling


class CompetitionViews(EloFixture):
    configure = scheduling.EloScheduling.configure

    def test_personal_poll_omits_competitions_without_computing_them(self):
        from unittest.mock import patch
        self.configure();self.repositories()
        team=self.other_client();team.post('/api/v1/auth/login',json={'username':'elo-0','password':'test-only-new-team-password'})
        for view in ['home','team']:
            with self.subTest(view=view):
                with patch('arena_api.competition_views.team_competitions',side_effect=AssertionError('Poll must not compute competitions')):
                    response=team.get('/api/v1/ui/personal',params=dict(view=view,poll=1))
                    self.assertEqual(response.status_code,200)
                    self.assertNotIn('teamCompetitions',response.json())
                self.assertIn('teamCompetitions',team.get('/api/v1/ui/personal',params=dict(view=view)).json())

    def repositories(self):
        with self.store.transaction():
            for i,(team,entry) in enumerate(self.entries.items()):
                self.store.db.execute("INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms) VALUES(?,?,'owner',?,1,1)",(team,i+1,'repo'+str(i)))
                self.store.db.execute("INSERT INTO github_commits(team_id,sha,received_ms,submission_id,bot_hash) VALUES(?,?,1,?,?)",(team,entry['github_sha'],entry['submission_id'],entry['bot_hash']))
                self.store.db.execute('INSERT INTO github_observations(team_id,sha,observed_ms) VALUES(?,?,1)',(team,entry['github_sha']))
                self.store.db.execute("INSERT INTO github_checks(team_id,sha,purpose,job_id,state,finished_ms) VALUES(?,?,?,?,'pass',1)",(team,entry['github_sha'],self.ident,entry['benchmark_id']))

    def test_elo_summary_candidates_real_qualification_and_filters(self):
        self.configure();self.repositories()
        team,issued=self.create_team('Missing Team','missing-team');tid=issued['account']['team_id']
        with self.store.transaction():self.store.db.execute('UPDATE teams SET legacy_roster=0 WHERE id=?',(tid,))
        url=f'/api/v1/admin/competitions/{self.ident}/summary'
        first=self.client.get(url).json()
        self.assertEqual(first['counts'],dict(active=0,eligible=4,ineligible=1,excluded=0))
        self.assertEqual(first['reasons'],dict(team_size=1,repository=1,passed_bot=1))
        public=self.other_client().get(f'/api/v1/competitions/{self.ident}').json()
        self.assertEqual(public['participation_counts'],dict(active=0,eligible=4,ineligible=1))
        self.assertNotIn('email',public);self.assertNotIn('teams',public)
        from arena_api.publisher import Publisher
        import gzip
        publisher=Publisher(self.settings)
        try:publisher.publish()
        finally:publisher.close()
        snapshot=json.loads(gzip.decompress((self.settings.data/'snapshots'/f'competitions/{self.ident}.json.gz').read_bytes()))
        self.assertEqual(snapshot['participation_counts'],public['participation_counts'])
        self.assertEqual(team.get(url).status_code,403)
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries);self.complete_games(rid);self.book.finish(rid,2000)
        result=self.client.get(url).json()
        self.assertEqual(result['counts']['active'],4)
        self.assertEqual(result['fixtures']['completed'],4)
        first=next(iter(self.entries))
        queued=self.store.create('job',first,dict(kind='benchmark',status='queued',submission_ids=[]))
        with self.store.transaction():self.store.db.execute('UPDATE github_checks SET job_id=?,state=\'waiting\' WHERE team_id=? AND purpose=?',(queued['id'],first,self.ident))
        self.assertEqual(self.client.get(url).json()['queue'],dict(queued=1))
        for path in ['/admin/accounts','/admin/users','/admin/teams']:
            response=self.client.get('/api/v1'+path,params={'competition':self.ident})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['items'])
            self.assertTrue(all('participation' in r for r in response.json()['items']))
            self.assertEqual(team.get('/api/v1'+path,params={'competition':self.ident}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/admin/users?competition=missing').status_code,404)
        own=team.get('/api/v1/team/competitions').json()['items'][0]
        self.assertEqual(own['state'],'ineligible');self.assertIn('repository',own['reasons'])

    def test_own_elo_rank_matches_standings_and_exclusion(self):
        self.configure();self.repositories()
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries);self.complete_games(rid);self.book.finish(rid,2000)
        team=self.other_client();login=team.post('/api/v1/auth/login',json={'username':'elo-0','password':'test-only-new-team-password'}).json()
        tid=login['user']['team_id'];score=next(r for r in self.comp.elo.standings(self.ident)['items'] if r['team_id']==tid)
        own=team.get('/api/v1/team/competitions').json()['items'][0]
        self.assertEqual((own['state'],own['rank'],own['rating'],own['played']),('active',score['rank'],score['rating'],score['played']))
        self.assertNotIn('email',own);self.assertNotIn('source',own)
        with self.store.transaction():self.store.db.execute("INSERT INTO elo_exclusions(competition_id,team_id,excluded,reason,actor,created_ms) VALUES(?,?,1,'fixture','fixture',1)",(self.ident,tid))
        self.assertEqual(team.get('/api/v1/team/competitions').json()['items'][0]['state'],'excluded')
        bundle=team.get('/api/v1/ui/personal?view=home').json()
        self.assertEqual(bundle['teamCompetitions']['items'][0]['state'],'excluded')
        with self.store.transaction():self.store.db.execute('UPDATE competitions SET archived_ms=1 WHERE id=?',(self.ident,))
        self.assertEqual(team.get('/api/v1/team/competitions').json()['items'],[])

    def test_round_robin_entries_and_team_without_membership(self):
        with self.store.transaction():
            self.store.db.execute("UPDATE competitions SET status='open' WHERE id=?",(self.ident,))
            for tid,entry in list(self.entries.items())[:2]:self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(self.ident,tid,entry['submission_id'],entry['benchmark_id'],json.dumps(entry),1))
        summary=self.client.get(f'/api/v1/admin/competitions/{self.ident}/summary').json()
        self.assertEqual(summary['counts']['active'],2);self.assertIsNone(summary['progress'])
        team=self.other_client();team.post('/api/v1/auth/login',json={'username':'elo-0','password':'test-only-new-team-password'})
        own=team.get('/api/v1/team/competitions').json()['items'][0]
        self.assertEqual((own['state'],own['points'],own['played']),('active',0,0))
        tid=next(iter(self.entries))
        with self.store.transaction():
            self.store.db.execute('DELETE FROM team_members WHERE team_id=?',(tid,))
            self.store.db.execute('UPDATE accounts SET team_id=NULL WHERE team_id=?',(tid,))
        self.assertEqual(team.get('/api/v1/team/competitions').status_code,403,'team_principal requires a team; UI uses public open competitions before joining')

    def test_team_outside_round_robin_keeps_its_official_elo(self):
        self.configure();self.repositories()
        self.book.seed_official(self.ident,self.entries,1000)
        finalists=dict(list(self.entries.items())[2:])
        self.book.start(self.ident,'round_robin',1,2000,finalists)
        team=self.other_client();team.post('/api/v1/auth/login',json={'username':'elo-0','password':'test-only-new-team-password'})
        with self.store.transaction():self.store.db.execute("UPDATE competitions SET status='running' WHERE id=?",(self.ident,))
        own=team.get('/api/v1/team/competitions').json()['items'][0]
        self.assertEqual(own['state'],'active');self.assertEqual(own['rating'],1500);self.assertEqual(own['played'],0);self.assertIsNone(own['rank'])

    def test_completed_round_robin_omits_nonparticipants_from_team_and_admin_views(self):
        active=list(self.entries)[1:3]
        with self.store.transaction():
            self.store.db.execute("UPDATE competitions SET status='completed' WHERE id=?",(self.ident,))
            for tid in active:
                entry=self.entries[tid]
                self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(self.ident,tid,entry['submission_id'],entry['benchmark_id'],json.dumps(entry),1))
        team=self.other_client();team.post('/api/v1/auth/login',json={'username':'elo-0','password':'test-only-new-team-password'})
        self.assertEqual(team.get('/api/v1/team/competitions').json()['items'],[])
        for path in ['/admin/accounts','/admin/users','/admin/teams']:
            rows=self.client.get('/api/v1'+path,params={'competition':self.ident}).json()['items']
            self.assertEqual(len(rows),2,path)
            self.assertTrue(all(r['participation']['state']=='active' for r in rows))
        summary=self.client.get(f'/api/v1/admin/competitions/{self.ident}/summary').json()
        self.assertEqual(summary['counts'],dict(active=2,eligible=0,ineligible=0,excluded=0))
        self.assertEqual(summary['reasons'],{})

    def test_open_keeps_four_states_but_closed_keeps_only_active_and_excluded(self):
        self.configure();self.repositories()
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries);self.complete_games(rid);self.book.finish(rid,2000)
        self.create_team('Missing Team','missing-team')
        excluded,eligible=list(self.entries)[:2]
        with self.store.transaction():
            self.store.db.execute('DELETE FROM elo_ratings WHERE competition_id=? AND team_id=?',(self.ident,eligible))
            self.store.db.execute("INSERT INTO elo_exclusions(competition_id,team_id,excluded,reason,actor,created_ms) VALUES(?,?,1,'fixture','fixture',1)",(self.ident,excluded))
        url=f'/api/v1/admin/competitions/{self.ident}/summary'
        self.assertEqual(self.client.get(url).json()['counts'],dict(active=2,eligible=1,ineligible=1,excluded=1))
        for status in ['locked','running','paused','completed']:
            with self.subTest(status=status):
                with self.store.transaction():self.store.db.execute('UPDATE competitions SET status=? WHERE id=?',(status,self.ident))
                result=self.client.get(url).json()
                self.assertEqual(result['counts'],dict(active=2,eligible=0,ineligible=0,excluded=1))
                self.assertEqual(result['reasons'],{})
