from pathlib import Path
import re
import sqlite3
import tempfile
from fastapi.testclient import TestClient
from arena_api.read_worker import create_app,nginx_config,PATHS
from arena_api.settings import Settings
from test_web_api import APIHarness


class ReadWorker(APIHarness):
    def test_allowlist_is_read_only_isolated_and_sees_committed_writes(self):
        team,issued=self.create_team();other,_=self.create_team('Other','other')
        ro=create_app(self.settings)
        with TestClient(ro,base_url=self.settings.origin) as reader:
            self.assertFalse(hasattr(ro.state.accounts,'hasher'))
            self.assertFalse(hasattr(ro.state.accounts,'hash_slots'))
            self.assertFalse(hasattr(ro.state,'broadcast'))
            self.assertFalse(hasattr(ro.state.github,'thread'))
            self.assertFalse(hasattr(ro.state.competitions,'thread'))
            with self.assertRaises(sqlite3.OperationalError):ro.state.store.db.execute("UPDATE accounts SET locked=1")
            reader.cookies.update(team.cookies)
            for path in PATHS:
                response=reader.get(path)
                self.assertEqual(response.status_code,200,(path,response.text))
            for path in ('/api/v1/ui/personal','/api/v1/ui/page','/api/v1/queue','/api/v1/auth/email/requests/x'):
                self.assertEqual(reader.get(path).status_code,404,path)
            self.assertEqual(reader.post('/api/v1/auth/login',json={}).status_code,404)
            self.assertEqual(reader.post('/api/v1/submissions',content=b'no').status_code,405)
            own=self.app.state.store.create('submission',issued['account']['team_id'],dict(version='read-after-write',runtime='cpp17'))
            self.assertEqual(reader.get('/api/v1/submissions').json()['items'][0]['id'],own['id'])
            self.assertEqual(team.post('/api/v1/auth/logout',json={}).status_code,200)
            self.assertEqual(reader.get('/api/v1/auth/me').status_code,401)
            reader.cookies.clear();unauthorized=reader.get('/api/v1/ui/read')
            self.assertEqual(unauthorized.status_code,401)
            self.assertEqual(unauthorized.json()['request_id'],unauthorized.headers['X-Request-ID'])
            reader.cookies.update(self.client.cookies);admin=reader.get('/api/v1/ui/read')
            self.assertEqual(admin.status_code,200,admin.text)
            self.assertEqual(admin.json()['submissions'],[])
            self.assertEqual(admin.json()['jobs'],[])
            reader.cookies.clear();reader.cookies.update(other.cookies)
            self.assertEqual(reader.get('/api/v1/ui/read').json()['submissions'],[])
            invalid=reader.get('/api/v1/ui/read?view=invalid')
            self.assertEqual(invalid.status_code,422)
            self.assertEqual(invalid.json()['error']['code'],'invalid_request')

    def test_nginx_routes_exactly_the_read_allowlist_and_zero_falls_back_to_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for count in (0,2):
                nginx_config(Settings(read_workers=count),root)
                upstream=(root/'nginx-read-upstream.conf').read_text()
                self.assertEqual(upstream.count('backup'),bool(count))
                self.assertEqual('127.0.0.1:4183' in upstream,bool(count))
                config=(root/'nginx-read-routes.conf').read_text()
                pattern=config.split('location ~ ',1)[1].split(' {',1)[0]
                self.assertTrue(all(re.fullmatch(pattern,path) for path in PATHS))
                self.assertFalse(re.fullmatch(pattern,'/api/v1/ui/read/extra'))
