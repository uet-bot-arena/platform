from unittest.mock import patch
from fastapi.testclient import TestClient
from arena_api.accounts import token_hash
from arena_api.dependencies import COOKIE
from arena_api.read_worker import create_app as read_app
from arena_api.settings import Settings
from arena.conf import ConfigError
from test_web_api import APIHarness

BASE=2_000_000_000_000


class SessionExpiry(APIHarness):
    def session(self,role):
        a=self.app.state.accounts
        with a.store.lock,a.db:
            a.db.execute("INSERT INTO accounts(id,username,password_hash,role,must_change,created_ms) SELECT ?,?,password_hash,?,0,? FROM accounts WHERE username='organizer'",
                         (role,role+'-fixture',role,BASE))
            account=a.one('SELECT * FROM accounts WHERE id=?',(role,))
            with patch('arena_api.accounts.now_ms',return_value=BASE):value=a.new_session(account)
        client=self.other_client();client.cookies.set(COOKIE,value['token'],path='/api')
        client.headers['X-CSRF-Token']=value['csrf_token']
        return client,value

    def test_idle_limits_for_all_roles_and_poll_does_not_extend(self):
        for role,idle in [('student',3600),('staff',3600),('guest',3600),('admin',1800)]:
            with self.subTest(role=role):
                client,value=self.session(role)
                with patch('arena_api.accounts.now_ms',return_value=BASE+idle*1000-1):
                    self.assertEqual(client.get('/api/v1/auth/me',headers={'X-Arena-Poll':'1'}).status_code,200)
                with patch('arena_api.accounts.now_ms',return_value=BASE+idle*1000):
                    self.assertEqual(client.get('/api/v1/auth/me',headers={'X-Arena-Poll':'1'}).status_code,401)
                self.assertIsNone(self.app.state.accounts.one('SELECT * FROM sessions WHERE token_hash=?',(token_hash(value['token']),)))

    def test_absolute_limits_even_with_continuing_activity(self):
        for role,maximum,idle in [('student',86400,3600),('admin',43200,1800)]:
            client,value=self.session(role)
            self.assertEqual(value['expires_ms'],BASE+maximum*1000)
            for elapsed in range(idle//2,maximum,idle//2):
                with patch('arena_api.accounts.now_ms',return_value=BASE+elapsed*1000):
                    self.assertEqual(client.get('/api/v1/auth/me').status_code,200)
            with patch('arena_api.accounts.now_ms',return_value=BASE+maximum*1000):
                self.assertEqual(client.get('/api/v1/auth/me').status_code,401)

    def test_manual_refresh_is_written_at_most_once_per_five_minutes(self):
        client,value=self.session('student');a=self.app.state.accounts;statements=[]
        a.db.set_trace_callback(statements.append)
        try:
            for elapsed,poll in [(299999,False),(300000,True),(300000,False),(300001,False),(599999,False)]:
                with patch('arena_api.accounts.now_ms',return_value=BASE+elapsed):
                    self.assertEqual(client.get('/api/v1/auth/me',headers={'X-Arena-Poll':'1'} if poll else {}).status_code,200)
        finally:a.db.set_trace_callback(None)
        writes=[s for s in statements if s.startswith('UPDATE sessions SET last_seen_ms=')]
        self.assertEqual(len(writes),1)
        self.assertEqual(a.one('SELECT last_seen_ms FROM sessions WHERE token_hash=?',(token_hash(value['token']),))['last_seen_ms'],BASE+300000)

    def test_read_worker_checks_but_never_updates_or_deletes_session(self):
        client,value=self.session('student');app=read_app(self.settings)
        with TestClient(app,base_url=self.settings.origin) as reader:
            reader.cookies.update(client.cookies)
            statements=[];app.state.store.db.set_trace_callback(statements.append)
            with patch('arena_api.accounts.now_ms',return_value=BASE+300000):
                self.assertEqual(reader.get('/api/v1/auth/me').status_code,200)
            with patch('arena_api.accounts.now_ms',return_value=BASE+3600000):
                self.assertEqual(reader.get('/api/v1/auth/me').status_code,401)
            self.assertFalse(any(s.startswith(('UPDATE ','DELETE ','INSERT ')) for s in statements))
        self.assertEqual(self.app.state.accounts.one('SELECT last_seen_ms FROM sessions WHERE token_hash=?',(token_hash(value['token']),))['last_seen_ms'],BASE)

    def test_sse_handshake_does_not_refresh(self):
        client,value=self.session('student');a=self.app.state.accounts
        # Authentication is checked before looking up the absent job; do not open a long-lived stream.
        with patch('arena_api.accounts.now_ms',return_value=BASE+300000):
            response=client.get('/api/v1/jobs/absent/events',headers={'Accept':'text/event-stream'})
        self.assertEqual(response.status_code,404)  # Personal practice authenticates, then rejects the nonexistent job.
        self.assertEqual(a.one('SELECT last_seen_ms FROM sessions WHERE token_hash=?',(token_hash(value['token']),))['last_seen_ms'],BASE)

    def test_cookie_limits_and_multiple_simultaneous_logins(self):
        for role,maximum in [('student',86400),('admin',43200)]:
            self.session(role);clients=[self.other_client(),self.other_client()]
            for client in clients:
                with patch('arena_api.accounts.now_ms',return_value=BASE):
                    result=client.post('/api/v1/auth/login',json=dict(username=role+'-fixture',password='test-only-admin-password'))
                self.assertEqual(result.status_code,200)
                self.assertIn('Max-Age='+str(maximum),result.headers['set-cookie'])
            self.assertNotEqual(clients[0].cookies.get(COOKIE),clients[1].cookies.get(COOKIE))
            with patch('arena_api.accounts.now_ms',return_value=BASE+1000):
                self.assertEqual([c.get('/api/v1/auth/me').status_code for c in clients],[200,200])

    def test_session_settings_reject_invalid_idle_ranges_and_support_custom_roles(self):
        for values in [dict(session_idle_seconds=299),dict(session_idle_seconds=86401),dict(session_seconds=600,session_idle_seconds=601),
                       dict(admin_session_idle_seconds=299),dict(admin_session_seconds=600,admin_session_idle_seconds=601),dict(admin_session_seconds=0)]:
            with self.subTest(values=values),self.assertRaises(ConfigError):Settings(**values)
        settings=Settings(session_seconds=7200,session_idle_seconds=600,admin_session_seconds=3600,admin_session_idle_seconds=300)
        self.assertEqual((settings.session_seconds,settings.session_idle_seconds,settings.admin_session_seconds,settings.admin_session_idle_seconds),(7200,600,3600,300))
