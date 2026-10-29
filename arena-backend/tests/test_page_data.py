from test_web_api import APIHarness


class PageData(APIHarness):
    def test_page_bundle_preserves_team_isolation_and_existing_filters(self):
        first,a=self.create_team();second,b=self.create_team('Other','other')
        store=self.app.state.store
        own=store.create('job',a['account']['team_id'],dict(kind='benchmark',status='queued',submission_ids=[],profile={}))
        other=store.create('job',b['account']['team_id'],dict(kind='benchmark',status='queued',submission_ids=[],profile={}))
        result=first.get('/api/v1/ui/page?view=home')
        self.assertEqual(result.status_code,200,result.text)
        data=result.json()
        self.assertEqual(data['personal']['team']['id'],a['account']['team_id'])
        self.assertEqual([j['id'] for j in data['jobs']],[own['id']])
        self.assertEqual(data['dashboard']['counts']['benchmarks'],1)
        self.assertEqual(first.get('/api/v1/ui/page?view=jobs&job_id='+other['id']).status_code,404)
        self.assertEqual(self.other_client().get('/api/v1/ui/page?view=home').status_code,401)
        admin=self.client.get('/api/v1/ui/page?view=home')
        self.assertEqual(admin.status_code,200,admin.text)
        self.assertIsNone(admin.json()['personal']['team'])
        self.assertEqual(admin.json()['jobs'],[])
        self.assertEqual(first.get('/api/v1/ui/page?view=unknown').status_code,422)
        for view in ('team','submissions','benchmark','practice'):
            response=first.get('/api/v1/ui/page?view='+view)
            self.assertEqual(response.status_code,200,(view,response.text))
