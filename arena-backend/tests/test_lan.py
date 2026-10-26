"""LAN is opt-in; normal HTTPS hub and qualification rules stay unchanged."""
import dataclasses
import io
import uuid
import zipfile
from unittest.mock import patch
from arena.conf import ConfigError
from arena_api.settings import Settings
import test_competitions as competition_tests
from test_web_api import APIHarness


class LanUploads(APIHarness):
    def test_private_ipv4_http_and_public_https(self):
        for host in ('192.168.1.10', '10.2.3.4', '172.22.192.183'):
            self.assertFalse(Settings(origin='http://'+host+':4191').cookie_secure)
        for origin, secure in [('http://arena.example',False),('http://8.8.8.8',False),('http://192.168.1.10',True),('http://[fd00::1]',False)]:
            with self.assertRaises(ConfigError):Settings(origin=origin,cookie_secure=secure)
        self.assertTrue(Settings(origin='https://arena.example',cookie_secure=True).cookie_secure)
        self.assertFalse(Settings().team_zip_upload)

    def test_upload_flag_validation_quota_and_idempotent_automatic_check(self):
        team, credentials = self.create_team()
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr('bot.py','print("test")')
        headers={'Content-Type':'application/zip','X-Submission-Version':'v1.0.0','X-Arena-Runtime':'python3','Idempotency-Key':'lan-upload-test'}
        def upload(body=raw.getvalue(),**extra):return team.post('/api/v1/submissions',content=body,headers=headers|extra)
        self.assertEqual(upload().status_code,403)
        self.app.state.settings=dataclasses.replace(self.settings,team_zip_upload=True)
        self.assertTrue(team.get('/api/v1/platform').json()['team_zip_upload'])
        first=upload();self.assertEqual(first.status_code,201,first.text)
        self.assertEqual(upload().json()['id'],first.json()['id'])
        jobs=self.app.state.store.list('job',credentials['account']['team_id'])
        self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['kind'],'benchmark')
        self.assertTrue(jobs[0]['profile']['id'].startswith('ottv2-check-500ms-v1'))
        data=team.get('/api/v1/ui/read?view=submissions').json()
        self.assertEqual(len(data['jobs']),1);self.assertEqual(len(data['submissions']),1)
        self.assertEqual(upload(b'not zip',**{'Idempotency-Key':'bad-lan-zip','X-Submission-Version':'v1.0.1'}).status_code,400)
        self.assertEqual(upload(**{'Content-Type':'application/json'}).status_code,400)
        self.app.state.service.max_submissions_per_hour=1
        self.assertEqual(upload(**{'Idempotency-Key':'lan-quota-test','X-Submission-Version':'v1.0.2'}).status_code,429)
        self.assertEqual(team.post('/api/v1/submissions',content=raw.getvalue(),headers=headers|{'X-CSRF-Token':'bad'}).status_code,403)


class LanEntries(APIHarness):
    def setUp(self):
        super().setUp()
        self.comp=self.app.state.competitions
        self.store=self.app.state.store
        image=patch('arena.service.Service.runtime_for',return_value='sha256:'+'1'*64)
        image.start();self.addCleanup(image.stop)
    post=competition_tests.CompetitionAPI.post
    create_comp=competition_tests.CompetitionAPI.create_comp
    qualified=competition_tests.CompetitionAPI.qualified
    launch=competition_tests.CompetitionAPI.launch
    take_job=competition_tests.CompetitionAPI.take_job
    finish=competition_tests.CompetitionAPI.finish
    def test_latest_compatible_pass_draft_open_lock_rbac_and_audit(self):
        cup=self.post(self.client,'/admin/competitions',{'title':'LAN group','max_teams':4},expected=201)
        team,credential=self.create_team();tid=credential['account']['team_id'];path=f"/admin/competitions/{cup['id']}/entries/{tid}"
        self.post(team,path,expected=403)
        self.post(self.client,path,expected=409)
        passed=self.qualified(tid,cup['config'])
        newer=self.qualified(tid,cup['config']);self.store.update(newer['benchmark_id'],status='failed')
        result=self.post(self.client,path,key='same-admin-entry')
        self.assertEqual(result['submission_id'],passed['submission_id'])
        self.qualified(tid,cup['config'])
        self.assertEqual(self.post(self.client,path,key='same-admin-entry'),result)
        audit=self.app.state.accounts.rows("SELECT * FROM audit WHERE action='competition.add_team'")
        self.assertTrue(audit)
        self.post(self.client,f"/admin/competitions/{cup['id']}/open")
        other,cred=self.create_team('Other LAN team','other-lan')
        self.qualified(cred['account']['team_id'],cup['config'])
        self.post(self.client,f"/admin/competitions/{cup['id']}/entries/{cred['account']['team_id']}")
        self.post(self.client,f"/admin/competitions/{cup['id']}/lock")
        self.post(self.client,path,expected=409)

    def test_lan_round_robin_uses_one_half_points_without_changing_normal(self):
        self.app.state.settings=dataclasses.replace(self.settings,team_zip_upload=True)
        cup=self.create_comp();self.assertEqual(cup['config']['scoring'],dict(win=1,draw=.5,loss=0))
        a,ca=self.create_team();b,cb=self.create_team('Other scoring team','scoring-team')
        for c in (ca,cb):
            tid=c['account']['team_id'];self.qualified(tid,cup['config'])
            self.post(self.client,f"/admin/competitions/{cup['id']}/entries/{tid}")
        locked=self.post(self.client,f"/admin/competitions/{cup['id']}/lock")
        result={'winner':None,'reason':'threefold_repetition'}
        self.finish(self.launch(locked),winner=None,result=result,final_state={'result':result})
        standings=self.comp.standings(cup['id'])
        self.assertEqual(standings['scoring']['draw'],.5)
        self.assertEqual([x['points'] for x in standings['items']],[.5,.5])
