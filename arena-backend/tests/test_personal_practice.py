from dataclasses import replace
from arena.conf import Secrets
from arena_api.settings import AccountRules, EmailProvider
from arena_api.outbox import MailQueue
from arena_api.people import People
from arena_api.teams import Teams
import io
import zipfile
from unittest.mock import patch
from arena.protocol import Fault
from test_web_api import APIHarness
import test_people as people


class PersonalPractice(APIHarness):
    def setUp(self):
        APIHarness.setUp(self)
        self.current = 1792740000000
        clock = patch("arena_api.accounts.now_ms", lambda: self.current)
        clock.start()
        self.addCleanup(clock.stop)
        rules = AccountRules(student_email=r"^\d{8}@vnu\.test$", staff_domains=("vnu.test",), freshman_cohort=2026)
        settings = replace(self.settings, account_rules=rules,
                           secrets=Secrets({"verification_hmac_key": "test-only-people-" + "x" * 40}),
                           email_providers=(EmailProvider("mailpit", "127.0.0.1", 1025, "none", "no-reply@vnu.test", daily_limit=10000, hourly_limit=10000),))
        state = self.app.state
        state.settings = settings
        self.queue = state.mail = MailQueue(state.store, settings, clock=lambda: self.current)
        state.people = People(state.accounts, self.queue, rules)
        self.teams = state.teams = Teams(state.accounts, rules, clock=lambda: self.current)
        state.competitions.teams = self.teams
        self.serial = 0

    proof=people.PeopleAPI.proof
    person=people.PeopleAPI.person
    login_person=people.PeopleAPI.login_person
    team=people.PeopleAPI.team

    def code(self, client, code='print(1)', key='personal-source'):
        return client.post('/api/v1/practice-submissions',json=dict(runtime='python3',code=code),headers={'Idempotency-Key':key})

    def zip(self, client, files, key='personal-zip'):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in files:z.writestr(name,data)
        return client.post('/api/v1/practice-submissions/zip',content=buf.getvalue(),headers={
            'Content-Type':'application/zip','X-Arena-Runtime':'python3','Idempotency-Key':key})

    def test_owner_privacy_join_and_competition_guards(self):
        a,user=self.person();b,_=self.person()
        r=self.code(a);self.assertEqual(r.status_code,201,r.text);sub=r.json()
        self.assertEqual((sub['team_id'],sub['channel']),('user:'+user['id'],'practice'))
        jid=a.get('/api/v1/jobs').json()['items'][0]['id']
        for suffix in ('','/report','/events'):
            self.assertEqual(b.get('/api/v1/jobs/'+jid+suffix).status_code,404)
        self.assertEqual(b.get('/api/v1/submissions').json()['items'],[])
        self.assertEqual(a.get('/api/v1/jobs/'+jid+'/events').status_code,200)
        self.assertEqual(a.get('/api/v1/sample-bots').status_code,200)
        for path in ('/teams/me/github','/team/competitions','/team/elo'):
            self.assertEqual(a.get('/api/v1'+path).status_code,403,path)
        r=a.post('/api/v1/competitions/'+'a'*32+'/entry',json={'submission_id':sub['id'],'benchmark_id':jid,'delegate_timeout_claims':True},headers={'Idempotency-Key':'no-personal-entry'})
        self.assertEqual(r.status_code,403,r.text)
        owner=self.team(b,'Other team')
        self.assertEqual(b.get('/api/v1/jobs/'+jid).status_code,404)
        self.assertEqual(a.get('/api/v1/ui/read?view=home').json()['submissions'][0]['id'],sub['id'])
        from fastapi.testclient import TestClient
        from arena_api.read_worker import create_app as read_app
        with TestClient(read_app(self.settings),base_url=self.settings.origin) as reader:
            reader.cookies.update(a.cookies)
            self.assertEqual(reader.get('/api/v1/ui/read?view=home').json()['submissions'][0]['id'],sub['id'])
            reader.cookies.clear();reader.cookies.update(b.cookies)
            self.assertEqual(reader.get('/api/v1/submissions').json()['items'],[])
        self.team(a)
        self.assertEqual(a.get('/api/v1/submissions').json()['items'],[])
        self.assertEqual(a.get('/api/v1/jobs/'+jid).status_code,404)
        new=self.code(a,'print(2)','team-source').json()
        self.assertNotEqual(new['team_id'],sub['team_id'])

    def test_zip_validation_shared_quota_and_qualification(self):
        a,user=self.person()
        r=self.zip(a,[('bot/',''),('bot/bot.py','import helper'),('bot/helper.py','x=1')])
        self.assertEqual(r.status_code,201,r.text);sub=r.json()
        self.assertEqual(sub['channel'],'practice')
        self.assertEqual(set(sub['source']['files']),{'bot.py','helper.py'})
        jobs=a.get('/api/v1/jobs').json()['items'];self.assertEqual(len(jobs),1)
        with self.assertRaises(Fault) as caught:
            self.app.state.competitions.qualification({},sub['team_id'],sub['id'],jobs[0]['id'])
        self.assertEqual(caught.exception.code,'qualification_required')
        for files in ([('bot/bot.py','pass'),('bot/bot.cpp','int main(){}')],
                      [('bot/bot.py','pass'),('bot/../escape.py','pass')],
                      [('bot/bot.py','pass'),('outside.py','pass')]):
            self.assertEqual(self.zip(a,files,key=str(len(str(files)))).status_code,400)
        r=a.post('/api/v1/practice-submissions/zip',content=b'x'*(1024*1024+1),headers={'Content-Type':'application/zip','Idempotency-Key':'large'})
        self.assertEqual(r.status_code,413,r.text)
        for i in range(9):self.assertEqual(self.code(a,'print('+str(i)+')','quota-code-'+str(i)).status_code,201)
        self.assertEqual(self.zip(a,[('bot.py','print(99)')],'eleventh').status_code,429)
