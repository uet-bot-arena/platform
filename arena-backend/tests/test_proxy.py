from types import SimpleNamespace
from unittest.mock import Mock
from arena_api.dependencies import peer
from test_web_api import APIHarness


class ProxyAndRateLimits(APIHarness):
    def test_only_explicit_proxy_cidrs_can_supply_forwarded_client(self):
        def request(host,forwarded,trusted):
            return SimpleNamespace(client=SimpleNamespace(host=host),headers={'x-forwarded-for':forwarded},
                app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(trusted_proxies=trusted))))
        self.assertEqual(peer(request('192.0.2.9','1.2.3.4',('127.0.0.1/32',))),'192.0.2.9')
        self.assertEqual(peer(request('127.0.0.1','198.51.100.2',('127.0.0.1/32',))),'198.51.100.2')
        self.assertEqual(peer(request('127.0.0.1','1.2.3.4, 198.51.100.2',('127.0.0.1/32',))),'198.51.100.2')
        self.assertEqual(peer(request('127.0.0.1','bad',('127.0.0.1/32',))),'127.0.0.1')

    def test_one_hundred_accounts_share_one_peer_but_account_bruteforce_is_limited(self):
        # Hash verification is already covered by the real account lifecycle
        # tests. This test isolates admission counters from Argon2 CPU cost.
        accounts=self.app.state.accounts
        accounts.verify=Mock(return_value=True)
        with accounts.store.lock,accounts.db:
            template=accounts.one("SELECT password_hash,created_ms FROM accounts WHERE username='organizer'")
            accounts.db.executemany("INSERT INTO accounts(id,username,password_hash,role,team_id,must_change,created_ms) VALUES(?,?,?,'admin',NULL,0,?)",
                [(str(i),f'proxy{i:04}',template['password_hash'],template['created_ms']) for i in range(100)])
        for i in range(100):
            response=self.client.post('/api/v1/auth/login',json={'username':f'proxy{i:04}','password':'test-only-password'})
            self.assertEqual(response.status_code,200,response.text)
        accounts.verify.return_value=False
        for _ in range(9):
            self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'proxy0000','password':'incorrect'}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'proxy0000','password':'incorrect'}).status_code,429)
