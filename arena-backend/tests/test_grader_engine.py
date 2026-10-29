from unittest.mock import patch
from arena import engine
from test_web_api import APIHarness
import test_graders as helpers


class GraderEngine(APIHarness):
    post=helpers.GraderAPI.post
    practice=helpers.GraderAPI.practice
    lease=helpers.GraderAPI.lease

    def setUp(self):
        super().setUp()
        self.service=self.app.state.service
        self.store=self.app.state.store

    def machine(self,name,digest=None):
        item=self.post(self.client,'/admin/graders',{'name':name},expected=201)
        client=self.other_client();client.headers['Authorization']='Bearer '+item['token']
        body=dict(platform='windows',runtime_id=helpers.WINDOWS,slots=2,backend='windows',version='0.8.0-m1' if digest is None else '0.9.0')
        if digest is not None:body['engine_sha256']=digest
        response=client.post('/api/v1/grader/hello',json=body)
        self.assertEqual(response.status_code,200,response.text)
        return client,item

    def test_missing_or_different_engine_keeps_job_queued_and_is_excluded_from_health_eta(self):
        old,_=self.machine('old package')
        wrong,_=self.machine('different engine','0'*64)
        current,_=self.machine('current',engine.ENGINE_SHA256)
        team,issued=self.create_team();job=self.practice(team,issued['account']['team_id'])
        with patch.object(self.service,'before_next') as scheduler:
            for _ in range(3):
                self.assertIsNone(self.lease(old))
                self.assertIsNone(self.lease(wrong))
            scheduler.assert_not_called()
        self.assertEqual(self.store.get(job['id'])['status'],'queued')
        health=self.client.get('/api/v1/health/ready').json()
        self.assertEqual((health['graders_online'],health['grader_slots']),(1,2))
        self.assertEqual([g['name'] for g in self.service.graders(online_only=True)],['current'])
        rows={g['name']:g for g in self.client.get('/api/v1/admin/graders').json()['items']}
        self.assertTrue(rows['old package']['needs_update']);self.assertIsNone(rows['old package']['engine_sha256'])
        self.assertTrue(rows['different engine']['needs_update']);self.assertFalse(rows['current']['needs_update'])
        self.assertEqual(self.lease(current)['job']['id'],job['id'])

    def test_rehello_without_engine_clears_previously_reported_engine(self):
        client,item=self.machine('downgraded',engine.ENGINE_SHA256)
        response=client.post('/api/v1/grader/hello',json=dict(platform='windows',runtime_id=helpers.WINDOWS,slots=2,backend='windows',version='0.8.0-m1'))
        self.assertEqual(response.status_code,200)
        self.assertIsNone(self.service.grader_row(item['id'])['engine_sha256'])
