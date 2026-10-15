"""Add-on import/publication/team contract pipeline; no Docker or real DB used."""
from copy import deepcopy
import io
import json
import uuid
import zipfile

from arena import experimental
from arena_api import addons
from test_web_api import APIHarness
import test_experimental


class AddonPipeline(APIHarness):
    post=test_experimental.ExperimentalAPI.post
    publish=test_experimental.ExperimentalAPI.publish
    def test_atomic_import_export_starter_and_no_extra_jobs(self):
        d=deepcopy(experimental.PRESETS['energy-dash'])
        pack={'format':addons.FORMAT,'format_version':1,'definition':d,'release_notes':'Kéo có dash.\nGiữ hai vùng năng lượng.'}
        key=uuid.uuid4().hex
        body={'package':pack,'publish':True}
        r=self.post('/admin/rulesets/import',body,201,key)
        self.assertEqual(r,self.post('/admin/rulesets/import',body,201,key))
        self.post('/admin/rulesets/import',{**body,'publish':False},409,key)
        self.assertEqual(r['status'],'published')
        self.assertEqual(r['hash'],experimental.rule_hash(d))
        self.assertEqual(len(r['validation']['smoke']),2)
        self.assertTrue(all(x['plies']>0 for x in r['validation']['smoke']))
        self.assertEqual(len(self.app.state.store.list('ruleset')),1)
        self.assertEqual(self.app.state.store.list('job'),[])
        team,_=self.create_team()
        schema=team.get('/api/v1/rulesets/schema')
        self.assertEqual(schema.status_code,200)
        self.assertEqual(schema.json()['properties']['modifiers']['maxItems'],4)
        detail=team.get('/api/v1/rulesets/'+r['id']).json()
        self.assertEqual(detail['contract']['compatibility']['protocol_version'],3)
        self.assertEqual(detail['contract']['hash'],r['hash'])
        self.assertIn('modifiers.dash',[x['path'] for x in detail['contract']['changes']])
        self.assertEqual(detail['contract']['examples']['ready']['ruleset_hash'],r['hash'])
        obs=detail['contract']['examples']['observation_payload']
        self.assertEqual(obs['legal_actions'],experimental.Rules(d).legal_actions(obs['state']))
        export=team.get('/api/v1/rulesets/'+r['id']+'/package.json').json()
        self.assertEqual(addons.unpack(export),(d,pack['release_notes']))
        bundle=team.get('/api/v1/rulesets/'+r['id']+'/kit.zip')
        self.assertEqual(bundle.status_code,200)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as z:
            self.assertEqual(json.loads(z.read('contract.json'))['hash'],r['hash'])
            self.assertIn('starter.zip',z.namelist())
            self.assertIn('RULESET-ADDONS.md',z.namelist())
        starter=team.get('/api/v1/rulesets/'+r['id']+'/starter.zip')
        source=team.post('/api/v1/submissions',content=starter.content,headers={'Idempotency-Key':uuid.uuid4().hex,'Content-Type':'application/zip','X-Submission-Version':'v1.0.0','X-Arena-Protocol':'3'})
        self.assertEqual(source.status_code,201,source.text)
        self.assertEqual(source.json()['protocol_version'],3)

    def test_invalid_import_rolls_back_and_rbac(self):
        team,_=self.create_team()
        self.post('/admin/rulesets/import',{'package':experimental.PRESETS['domination'],'publish':True},403,client=team)
        guest=self.other_client()
        self.assertEqual(guest.get('/api/v1/rulesets').status_code,401)
        invalid=deepcopy(experimental.PRESETS['domination']);invalid['modifiers'][0]['pattern']='teleport'
        for pack in (invalid,{'format':addons.FORMAT,'format_version':2,'definition':invalid}, {'format':addons.FORMAT,'format_version':1,'definition':experimental.PRESETS['domination'],'release_notes':'\x00'}):
            self.post('/admin/rulesets/import',{'package':pack,'publish':True},400)
        self.assertEqual(self.app.state.store.list('ruleset'),[])
        self.assertEqual(self.app.state.store.db.execute("SELECT COUNT(*) FROM competition_commands WHERE operation LIKE 'ruleset:%'").fetchone()[0],0)

    def test_release_clone_diff_immutable_and_archive_inspection(self):
        team,_=self.create_team()
        d=deepcopy(experimental.PRESETS['energy-dash'])
        r=self.post('/admin/rulesets',{'definition':d,'release_notes':'Bản đầu'},201)
        self.assertEqual(team.get('/api/v1/rulesets/'+r['id']).status_code,404)
        self.post('/rulesets/'+r['id']+'/preview',{},404,client=team)
        self.post('/admin/rulesets/'+r['id']+'/release',{'expected_revision':99},409)
        r=self.post('/admin/rulesets/'+r['id']+'/release',{'expected_revision':r['revision']})
        before=deepcopy(r)
        clone=self.post('/admin/rulesets/'+r['id']+'/clone',{'expected_revision':r['revision']})
        d['modifiers'][0]['cost']=1;d['zones'][0]['gain']=2
        clone=self.post('/admin/rulesets/'+clone['id']+'/edit',{'definition':d,'release_notes':'Giảm chi phí dash; tăng thưởng c5.','expected_revision':clone['revision']})
        clone=self.post('/admin/rulesets/'+clone['id']+'/edit',{'definition':d,'expected_revision':clone['revision']})
        self.assertEqual(clone['release_notes'],'Giảm chi phí dash; tăng thưởng c5.')
        clone=self.post('/admin/rulesets/'+clone['id']+'/release',{'expected_revision':clone['revision']})
        spec=team.get('/api/v1/rulesets/'+clone['id']).json()['contract']
        self.assertEqual(spec['compared_to']['id'],r['id'])
        self.assertEqual({x['path'] for x in spec['changes']},{'zones.c5','modifiers.dash'})
        self.assertNotEqual(clone['hash'],r['hash'])
        self.post('/admin/rulesets/'+r['id']+'/edit',{'definition':d,'release_notes':'oops','expected_revision':r['revision']},409)
        self.assertEqual(self.app.state.store.get(r['id']),before)
        self.post('/admin/rulesets/'+r['id']+'/archive',{'expected_revision':r['revision']})
        self.assertEqual(team.get('/api/v1/rulesets/'+r['id']).status_code,200)
        self.assertNotIn(r['id'],[x['id'] for x in team.get('/api/v1/rulesets').json()['items']])

    def test_team_playground_current_action_and_step_limit(self):
        r=self.publish();team,_=self.create_team()
        before=self.app.state.store.get(r['id'])
        initial=self.post('/rulesets/'+r['id']+'/preview',{},client=team)
        self.assertFalse(self.app.state.rulesets.settings()['enabled'])
        a=initial['observation']['legal_actions'][0]['id']
        result=self.post('/rulesets/'+r['id']+'/preview',{'actions':[a]},client=team)
        self.assertEqual(result['observation']['state']['ply'],1)
        self.post('/rulesets/'+r['id']+'/preview',{'actions':['expired']},400,client=team)
        self.post('/rulesets/'+r['id']+'/preview',{'actions':['a0']*33},422,client=team)
        self.assertEqual(self.app.state.store.get(r['id']),before)
        self.assertEqual(self.app.state.store.list('job'),[])
