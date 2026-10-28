import dataclasses
import hashlib
from unittest.mock import patch
from arena.protocol import Fault
from arena_api.dependencies import COOKIE
from arena_api.settings import Settings
from test_web_api import APIHarness
import test_lan as lan


class QuickPractice(APIHarness):
    def test_policy_settings_defaults_and_file(self):
        defaults=Settings()
        self.assertTrue(defaults.practice_upload)
        self.assertTrue(defaults.account_rules.admin_login_lockout)
        self.assertEqual(defaults.account_rules.login_ip_limit,600)
        self.assertEqual(defaults.account_rules.grader_admins,())
        path=self.settings.data/'policy.toml'
        path.write_text('[hub]\npractice_upload = false\n[accounts]\nadmin_login_lockout = false\nlogin_ip_limit = 1000\ngrader_admins = ["Organizer"]\n')
        settings=Settings(config_file=path)
        self.assertFalse(settings.practice_upload)
        self.assertFalse(settings.account_rules.admin_login_lockout)
        self.assertEqual(settings.account_rules.login_ip_limit,1000)
        self.assertEqual(settings.account_rules.grader_admins,('organizer',))

    def test_upload_deterministic_retry_limits_and_disabled(self):
        team, cred = self.create_team()
        path='/api/v1/practice-submissions'
        def upload(code, key):
            return team.post(path,json={'runtime':'python3','code':code},headers={'Idempotency-Key':'practice-'+key})
        first=upload('print("hello")','first')
        self.assertEqual(first.status_code,201,first.text)
        item=first.json();self.assertEqual(item['channel'],'practice')
        self.assertEqual(item['version'],'thu-'+item['source']['zip_sha256'][:7])
        self.assertEqual(upload('print("hello")','first').json()['id'],item['id'])
        again=upload('print("hello")','other');self.assertEqual(again.status_code,409,again.text)
        self.assertIn('Bạn đã nạp đúng mã này',again.text)
        jobs=self.app.state.store.list('job',cred['account']['team_id'])
        self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['kind'],'benchmark')
        self.app.state.service.max_submissions_per_hour=1
        self.assertEqual(upload('print("shared quota")','shared-limit').status_code,429)
        self.app.state.service.max_submissions_per_hour=30
        self.assertEqual(upload('','empty').status_code,400)
        self.assertEqual(upload('á'*100001,'large').status_code,400)
        for i in range(9):self.assertEqual(upload('print('+str(i)+')','code-'+str(i)).status_code,201)
        self.assertEqual(upload('print(100)','eleven').status_code,429)
        self.app.state.settings=dataclasses.replace(self.settings,practice_upload=False)
        self.assertEqual(upload('print(101)','disabled').status_code,403)
        self.assertEqual(team.post('/api/v1/submissions').status_code,403)

    def test_grader_manager_allowlist_and_roles(self):
        self.app.state.accounts.bootstrap_admin('reviewer','test-reviewer-password')
        other=self.other_client();result=other.post('/api/v1/auth/login',json={'username':'reviewer','password':'test-reviewer-password'}).json()
        other.headers['X-CSRF-Token']=result['csrf_token']
        self.assertTrue(other.get('/api/v1/auth/me').json()['can_manage_graders'])
        self.app.state.settings=dataclasses.replace(self.settings,account_rules=dataclasses.replace(self.settings.account_rules,grader_admins=('organizer',)))
        self.assertFalse(other.get('/api/v1/auth/me').json()['can_manage_graders'])
        grader=self.client.post('/api/v1/admin/graders',json={'name':'test PC'});self.assertEqual(grader.status_code,201)
        ident=grader.json()['id']
        for method,path,body in [('get','/admin/grader-package',None),('post','/admin/graders',{'name':'not allowed'}),('post',f'/admin/graders/{ident}/revoke',{}),('post',f'/admin/graders/{ident}/delete',{})]:
            response=other.get('/api/v1'+path) if method=='get' else other.post('/api/v1'+path,json=body)
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(other.get('/api/v1/admin/graders').status_code,200)
        self.assertTrue(self.client.get('/api/v1/auth/me').json()['can_manage_graders'])
        self.assertEqual(self.client.post(f'/api/v1/admin/graders/{ident}/revoke').status_code,200)
        self.assertEqual(self.client.post(f'/api/v1/admin/graders/{ident}/delete').status_code,200)

    def test_login_policy_admin_student_and_ip(self):
        a=self.app.state.accounts;a.admin_login_lockout=False
        for i in range(12):
            self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'organizer','password':'wrong password'}).status_code,401)
        login=self.client.post('/api/v1/auth/login',json={'username':'organizer','password':'test-only-admin-password'})
        self.assertEqual(login.status_code,200);self.client.headers['X-CSRF-Token']=login.json()['csrf_token']
        team,_=self.create_team()
        for i in range(10):self.assertEqual(team.post('/api/v1/auth/login',json={'username':'team-one','password':'wrong password'}).status_code,401)
        self.assertEqual(team.post('/api/v1/auth/login',json={'username':'team-one','password':'test-only-new-team-password'}).status_code,429)
        a.admin_login_lockout=True
        for i in range(10):self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'organizer','password':'wrong password'}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'organizer','password':'test-only-admin-password'}).status_code,429)
        a.admin_login_lockout=False;a.login_ip_limit=1000
        with patch.object(a,'verify',return_value=False):
            for i in range(1000):
                with self.assertRaises(Fault) as error:a.login('organizer','wrong password','quota-test')
                self.assertEqual(error.exception.code,'unauthorized')
            with self.assertRaises(Fault) as error:a.login('organizer','wrong password','quota-test')
            self.assertEqual(error.exception.code,'rate_limited')


class PracticeQualification(APIHarness):
    post=lan.LanEntries.post
    qualified=lan.LanEntries.qualified
    def test_practice_rejected_and_admin_falls_back(self):
        self.comp=self.app.state.competitions;self.store=self.app.state.store
        with patch('arena.service.Service.runtime_for',return_value='sha256:'+'1'*64):
            cup=self.post(self.client,'/admin/competitions',{'title':'Practice guard','max_teams':4},expected=201)
        team,cred=self.create_team();tid=cred['account']['team_id']
        old=self.qualified(tid,cup['config']);new=self.qualified(tid,cup['config'])
        self.app.state.store.update(new['submission_id'],channel='practice')
        self.post(self.client,f"/admin/competitions/{cup['id']}/open")
        response=team.post(f"/api/v1/competitions/{cup['id']}/entry",json=new,headers={'Idempotency-Key':'practice-enter'})
        self.assertEqual(response.status_code,409,response.text)
        result=self.post(self.client,f"/admin/competitions/{cup['id']}/entries/{tid}")
        self.assertEqual(result['submission_id'],old['submission_id'])
