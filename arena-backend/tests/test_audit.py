from dataclasses import replace
from fastapi.testclient import TestClient
from test_web_api import APIHarness


class AuditTests(APIHarness):
    def rows(self, client=None, **params):
        response=(client or self.client).get('/api/v1/admin/audit',params=params)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['items']

    def test_request_ip_uses_trusted_proxy_and_background_context_resets(self):
        self.app.state.settings=replace(self.settings,trusted_proxies=('127.0.0.1/32',))
        client=TestClient(self.app,base_url=self.settings.origin,client=('127.0.0.1',5000));self.extra_clients.append(client)
        response=client.post('/api/v1/auth/login',json=dict(username='organizer',password='test-only-admin-password'),headers={'X-Forwarded-For':'198.51.100.23'})
        self.assertEqual(response.status_code,200,response.text)
        a=self.app.state.accounts
        self.assertEqual(self.rows(group='auth',q='organizer')[0]['ip'],'198.51.100.23')
        with a.store.lock,a.db:a.audit('system','competition.result','background')
        self.assertIsNone(self.rows(role='system',group='competition')[0]['ip'])
        self.assertEqual(audit_context(),None)

    def test_role_group_search_target_labels_and_pagination(self):
        team,issued=self.create_team();a=self.app.state.accounts;admin=self.client.get('/api/v1/auth/me').json()['user']['id'];tid=issued['account']['team_id'];user=issued['account']['id']
        with a.store.lock,a.db:
            a.audit(admin,'grader.created',tid)
            a.audit(user,'github.attached',user)
            a.audit('system','elo.result','missing')
            a.audit('system','sampleXbot.enabled','not-the-bot-prefix')
        self.assertEqual(self.rows(role='team',group='bot',q='TEAM-ONE')[0]['actor_username'],'team-one')
        self.assertEqual(self.rows(role='admin',group='grader')[0]['target_label'],'Team One')
        self.assertEqual(self.rows(role='team',group='bot')[0]['target_label'],'team-one')
        self.assertIsNone(self.rows(role='system',group='competition')[0]['actor_role'])
        self.assertIsNone(self.rows(role='system',group='competition')[0]['target_label'])
        self.assertEqual(self.rows(role='system',group='bot'),[])
        self.assertEqual(self.rows(q='not-a-username'),[])
        self.assertEqual(self.rows(q='%'),[])
        all_rows=self.rows();self.assertEqual(self.rows(limit=1,offset=1)[0]['id'],all_rows[1]['id'])
        self.assertEqual(team.get('/api/v1/admin/audit').status_code,403)
        self.assertEqual(self.other_client().get('/api/v1/admin/audit').status_code,401)

    def test_only_manager_sees_full_ipv4_ipv6_and_legacy_rows_are_null(self):
        a=self.app.state.accounts;a.bootstrap_admin('other-btc','test-only-other-password')
        self.app.state.settings=replace(self.settings,account_rules=replace(self.settings.account_rules,grader_admins=('organizer',)))
        other=self.other_client();self.assertEqual(other.post('/api/v1/auth/login',json=dict(username='other-btc',password='test-only-other-password')).status_code,200)
        with a.store.lock,a.db:
            a.db.execute("INSERT INTO audit(actor,action,target,created_ms) VALUES('system','auth.legacy','legacy',1)")
            for ip in ['113.185.123.45','2001:db8:abcd:1234::99']:
                a.db.execute("INSERT INTO audit(actor,action,target,created_ms,ip) VALUES('system','auth.sample','sample',1,?)",(ip,))
        self.assertEqual(self.rows(q='113.185')[0]['ip'],'113.185.123.45')
        self.assertEqual(self.rows(other,q='113.185')[0]['ip'],'113.185.x.x')
        self.assertEqual(self.rows(other,q='2001:db8')[0]['ip'],'2001:db8:x:x:x:x:x:x')
        self.assertEqual(self.rows(q='2001:db8')[0]['ip'],'2001:db8:abcd:1234::99')
        self.assertIsNone(next(r for r in self.rows() if r['action']=='auth.legacy')['ip'])

    def test_labels_resolve_competition_finals_and_grader_names(self):
        a=self.app.state.accounts
        cup=self.app.state.competitions.create('test',dict(title='Audit Cup',max_teams=2,runtime_id='sha256:'+'1'*64),'audit-cup-create')
        response=self.client.post('/api/v1/admin/graders',json=dict(name='Audit grader'));self.assertEqual(response.status_code,201)
        grader=response.json()['id']
        with a.store.lock,a.db:
            a.db.execute("INSERT INTO finals(id,name,status,created_ms,config,state) VALUES('audit-final','Audit Final','draft',1,'{}','{}')")
            for target in [cup['id'],'audit-final',grader]:a.audit('system','auth.label',target)
        labels={r['target']:r['target_label'] for r in self.rows(group='auth')}
        self.assertEqual([labels[t] for t in [cup['id'],'audit-final',grader]],['Audit Cup','Audit Final','Audit grader'])


def audit_context():
    from arena_api.accounts import audit_ip
    return audit_ip.get()
