import json
import uuid
from arena.protocol import Fault
from unittest.mock import patch
from test_web_api import APIHarness, registration
import test_competitions as competition_tests


class AdminOperations(APIHarness):
    post = competition_tests.CompetitionAPI.post
    create_comp = competition_tests.CompetitionAPI.create_comp
    qualified = competition_tests.CompetitionAPI.qualified
    prepared = competition_tests.CompetitionAPI.prepared

    def setUp(self):
        super().setUp()
        self.store = self.app.state.store
        self.comp = self.app.state.competitions
        self.image_patch = patch('arena.sandbox.image_id', return_value='sha256:'+'1'*64)
        self.image_patch.start();self.addCleanup(self.image_patch.stop)

    def create_direct(self, name='Direct Team', username='direct-team'):
        r=self.client.post('/api/v1/admin/teams',json={'profile':registration(name),'username':username})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def test_create_atomic_edit_revision_and_no_secrets_in_audit(self):
        cred=self.create_direct();team=cred['account']['team_id']
        self.assertTrue(cred['account']['must_change_password'])
        profile=self.client.get('/api/v1/admin/teams/'+team).json()
        edit={**registration('Edited Team'),'expected_revision':profile['revision']}
        url='/api/v1/admin/teams/'+team+'/edit'
        changed=self.client.post(url,json=edit)
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['revision'],2)
        self.assertEqual(self.client.post(url,json=edit).status_code,409)
        conflict=self.client.post('/api/v1/admin/teams',json={'profile':registration('Other Name'),'username':'direct-team'})
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM teams').fetchone()[0],1)
        audit=self.client.get('/api/v1/admin/audit').text
        self.assertNotIn(cred['temporary_password'],audit)
        self.assertNotIn('Student 0',audit)

    def test_archive_revokes_sessions_restore_retains_lock_and_history(self):
        team,cred=self.create_team();tid=cred['account']['team_id'];aid=cred['account']['id']
        submission=self.store.create('submission',tid,{'version':'v1.0.0','source_path':'/private','source':{}})
        self.post(self.client,f'/admin/teams/{tid}/archive',{'expected_revision':1})
        self.assertEqual(team.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(self.client.post(f'/api/v1/admin/accounts/{aid}/lock',json={'locked':False}).status_code,409)
        restored=self.post(self.client,f'/admin/teams/{tid}/restore',{'expected_revision':2})
        self.assertIsNone(restored['archived_ms'])
        accounts=self.client.get('/api/v1/admin/accounts').json()['items']
        self.assertTrue(accounts[0]['locked'])
        self.assertEqual(self.store.get(submission['id'])['id'],submission['id'])
        self.post(self.client,f'/admin/accounts/{aid}/lock',{'locked':False})
        login=team.post('/api/v1/auth/login',json={'username':cred['account']['username'],'password':'test-only-new-team-password'})
        self.assertEqual(login.status_code,200)

    def test_archive_team_blocked_by_jobs_and_open_entry(self):
        _,cred=self.create_team();tid=cred['account']['team_id']
        job=self.store.create('job',tid,{'kind':'benchmark','status':'queued'})
        self.post(self.client,f'/admin/teams/{tid}/archive',{'expected_revision':1},expected=409)
        self.store.update(job['id'],status='failed')
        c=self.create_comp();entry=self.qualified(tid,c['config'])
        self.comp.enter('actor',tid,c['id'],entry,uuid.uuid4().hex)
        self.post(self.client,f'/admin/teams/{tid}/archive',{'expected_revision':1},expected=409)

    def test_competition_edit_archive_restore_and_idempotency(self):
        c=self.post(self.client,'/admin/competitions',{'title':'Draft Cup'},expected=201);cid=c['id']
        body={'title':'New Cup','match_ms':60000,'max_teams':8,'expected_revision':1}
        key=uuid.uuid4().hex
        updated=self.post(self.client,f'/admin/competitions/{cid}/edit',body,key=key)
        self.assertEqual(updated['config']['profile']['match_ms'],60000)
        self.assertEqual(self.post(self.client,f'/admin/competitions/{cid}/edit',body,key=key),updated)
        self.post(self.client,f'/admin/competitions/{cid}/edit',body,expected=409)
        self.post(self.client,f'/admin/competitions/{cid}/open')
        body['expected_revision']=3;body['match_ms']=300000
        self.post(self.client,f'/admin/competitions/{cid}/edit',body,expected=409)
        archived=self.post(self.client,f'/admin/competitions/{cid}/archive',{'expected_revision':3})
        self.assertIsNotNone(archived['archived_ms'])
        self.assertEqual(self.comp.listing(False)['total'],0)
        self.post(self.client,f'/admin/competitions/{cid}/lock',expected=409)
        self.post(self.client,f'/admin/competitions/{cid}/restore',{'expected_revision':4})
        self.assertEqual(self.comp.listing(False)['total'],1)

    def test_locked_competition_cannot_edit_or_delete_and_finished_preserves_results(self):
        c,_,_,_=self.prepared();cid=c['id']
        self.post(self.client,f'/admin/competitions/{cid}/archive',{'expected_revision':c['revision']},expected=409)
        self.post(self.client,f'/admin/competitions/{cid}/edit',{'title':'Unsafe Edit','max_teams':16,'turn_ms':15000,'expected_revision':c['revision']},expected=409)
        result=json.dumps({'winner':'red','reason':'base_capture'})
        with self.store.db:
            self.store.db.execute("UPDATE competition_fixtures SET status='completed',result=? WHERE competition_id=?",(result,cid))
            self.store.db.execute("UPDATE competitions SET status='completed' WHERE id=?",(cid,))
        before=self.comp.standings(cid,True)
        self.post(self.client,f'/admin/competitions/{cid}/archive',{'expected_revision':c['revision']})
        self.assertEqual(self.comp.standings(cid,True),before)
        with self.assertRaises(Fault) as hidden:
            self.comp.standings(cid,False)
        self.assertEqual(hidden.exception.code,'not_found')
        self.assertEqual(len(self.comp.schedule(cid,True)['items']),2)

    def test_metrics_filters_privacy_authorization_and_csrf(self):
        team,cred=self.create_team();tid=cred['account']['team_id']
        self.store.create('submission',tid,{'version':'v1.0.0','source_path':'/secret/source','source':{}})
        job=self.store.create('job',tid,{'kind':'benchmark','status':'failed','error':{'code':'compile_error'},'report':{'private_log':'diagnostic'}})
        self.store.create('job',tid,{'kind':'match','status':'queued','report':None})
        with patch.object(self.app.state.service,'health',return_value={'ready':False,'code':'worker_unavailable'}):
            m=self.client.get('/api/v1/admin/metrics').json()
        self.assertEqual(m['jobs']['by_status']['failed'],1)
        self.assertEqual(m['last_24h']['benchmark_pass_rate'],0)
        self.assertEqual(m['errors'][0]['code'],'compile_error')
        self.assertFalse(m['worker']['ready'])
        jobs=self.client.get('/api/v1/admin/jobs?status=failed&kind=benchmark').json()
        self.assertEqual(jobs['total'],1);self.assertNotIn('report',jobs['items'][0])
        self.assertIn('diagnostic',self.client.get('/api/v1/admin/jobs/'+job['id']).text)
        self.assertNotIn('/secret/source',self.client.get('/api/v1/admin/submissions').text)
        for path in ('metrics','jobs','submissions','teams/'+tid):
            self.assertEqual(team.get('/api/v1/admin/'+path).status_code,403)
            self.assertEqual(self.other_client().get('/api/v1/admin/'+path).status_code,401)
        self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.post('/api/v1/admin/teams',json={'profile':registration('Bad'),'username':'bad-team'}).status_code,403)

    def test_recovery_resolution_is_audited_idempotent_and_admin_only(self):
        team,cred=self.create_team()
        self.client.post('/api/v1/auth/recovery',json={'username':cred['account']['username'],'email':'captain@example.test'})
        pending=self.client.get('/api/v1/admin/recoveries').json()['items'][0]
        path='/api/v1/admin/recoveries/'+pending['id']+'/resolve'
        self.assertEqual(team.post(path).status_code,403)
        self.assertEqual(self.client.post(path).status_code,200)
        self.assertEqual(self.client.post(path).status_code,200)
        self.assertEqual(self.client.get('/api/v1/admin/recoveries').json()['items'],[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM audit WHERE action='recovery.resolved'").fetchone()[0],1)
        self.assertEqual(self.client.post('/api/v1/admin/recoveries/missing/resolve').status_code,404)
