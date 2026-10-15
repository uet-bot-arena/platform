import io
import json
import uuid
import zipfile
from unittest.mock import patch
from test_web_api import APIHarness
import test_competitions as competition_tests


class NewFlows(APIHarness):
    post = competition_tests.CompetitionAPI.post
    qualified = competition_tests.CompetitionAPI.qualified
    finish = competition_tests.CompetitionAPI.finish

    def setUp(self):
        super().setUp()
        self.store,self.comp=self.app.state.store,self.app.state.competitions
        self.image_patch=patch('arena.sandbox.image_id',return_value='sha256:'+'1'*64)
        self.image_patch.start();self.addCleanup(self.image_patch.stop)

    def test_swiss_round_barrier_byes_buchholz_and_restart(self):
        comp=self.post(self.client,'/admin/competitions',{'title':'Swiss test','format':'swiss','max_teams':200,'rounds':3},expected=201)
        cid=comp['id'];self.post(self.client,f'/admin/competitions/{cid}/open')
        for i in range(5):
            client,issued=self.create_team('Swiss '+str(i),'swiss-'+str(i))
            self.post(client,f'/competitions/{cid}/entry',self.qualified(issued['account']['team_id'],comp['config']))
        self.post(self.client,f'/admin/competitions/{cid}/lock')
        self.post(self.client,f'/admin/competitions/{cid}/start')
        history=set()
        for number in (1,2,3):
            fixtures=self.comp.schedule(cid,True,100)['items']
            current=[f for f in fixtures if self.comp.fixture(cid,f['id'])['round_number']==number]
            self.assertEqual(len(current),4)
            pairs={frozenset((f['red_team_id'],f['blue_team_id'])) for f in current}
            self.assertFalse(history & pairs);history|=pairs
            for i,f in enumerate(current):
                self.comp.tick();ident=self.app.state.service.pending.get_nowait();self.app.state.service.pending.task_done()
                job=self.store.get(ident)
                if i==len(current)-1:
                    self.store.update(ident,status='failed',error={'code':'worker_error'})
                    self.comp.reconcile()
                    self.assertEqual(self.comp.get(cid)['config']['current_round'],number)
                    self.comp.retry(self.client.get('/api/v1/auth/me').json()['user']['id'],cid,f['id'],uuid.uuid4().hex)
                    self.comp.tick();ident=self.app.state.service.pending.get_nowait();self.app.state.service.pending.task_done();job=self.store.get(ident)
                self.finish(job)
        final=self.comp.get(cid)
        self.assertEqual(final['status'],'completed')
        byes=self.comp.rows('SELECT * FROM competition_byes WHERE competition_id=?',(cid,))
        self.assertEqual(len({b['team_id'] for b in byes}),3)
        standings=self.comp.standings(cid,True)
        self.assertEqual(standings['ranking'],'points_buchholz')
        self.assertTrue(all('buchholz' in r for r in standings['items']))
        self.assertEqual([(r['points'],r['buchholz']) for r in standings['items']],sorted([(r['points'],r['buchholz']) for r in standings['items']],reverse=True))

    def test_organizer_sample_privacy_and_disable(self):
        team,issued=self.create_team()
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr('bot.py','print("secret-source-marker")')
        upload=self.client.post('/api/v1/admin/sample-bots',content=raw.getvalue(),headers={'Content-Type':'application/zip','X-Bot-Name':'Practice sample','X-Bot-Level':'easy','X-Arena-Runtime':'python3','Idempotency-Key':'sample-catalog-idempotency'})
        self.assertEqual(upload.status_code,201,upload.text)
        sample=upload.json();catalog=team.get('/api/v1/sample-bots').json()
        self.assertNotIn('secret-source-marker',json.dumps(catalog));self.assertNotIn('submission_id',catalog['items'][0])
        own=self.app.state.service.submit(issued['account']['team_id'],'v1.0.0','python3',raw.getvalue(),'own-source-test')
        job=self.post(team,'/matches',{'red_submission_id':own['id'],'sample_bot_id':sample['id'],'delegate_timeout_claims':True},expected=202)
        sample_source=self.store.get(sample['id'])['submission_id']
        self.store.event(job['id'],'build',{'submission_id':sample_source,'log':'secret-source-marker'})
        self.store.update(job['id'],report={'artifacts':[{}, {'source':'secret-source-marker'}],'sandbox_metrics':[{}, {'stderr':'secret-source-marker'}]})
        for suffix in ('','/report','/events'):
            response=team.get('/api/v1/jobs/'+job['id']+suffix)
            self.assertEqual(response.status_code,200,response.text);self.assertNotIn('secret-source-marker',response.text)
        self.post(self.client,'/admin/sample-bots/'+sample['id']+'/enabled',{'enabled':False})
        self.assertEqual(team.get('/api/v1/sample-bots').json()['items'],[])
        self.post(team,'/matches',{'red_submission_id':own['id'],'sample_bot_id':sample['id'],'delegate_timeout_claims':True},expected=404)

    def test_basic_competition_benchmark_pins_its_rules_and_public_view_hides_runtime_hashes(self):
        comp=self.post(self.client,'/admin/competitions',{'title':'10x10 test','max_teams':2,'basic_rules':{'rows':10,'cols':10,'eliminate_types':2}},expected=201)
        cid=comp['id'];self.post(self.client,f'/admin/competitions/{cid}/open')
        team,issued=self.create_team()
        submission=self.store.create('submission',issued['account']['team_id'],{'version':'v1.0.0','runtime':'python3','protocol_version':2})
        job=self.post(team,'/benchmarks',{'submission_id':submission['id'],'competition_id':cid},expected=202)
        self.assertEqual(job['basic_rules'],comp['config']['basic_rules'])
        self.assertEqual(job['profile'],comp['config']['profile'])
        guest=self.other_client()
        for endpoint in ('/competitions','/competitions/'+cid):
            response=guest.get('/api/v1'+endpoint)
            self.assertEqual(response.status_code,200)
            for field in ('engine_sha256','sdk_sha256','image_id','suite_sha256','source_path'):
                self.assertNotIn(field,response.text)
