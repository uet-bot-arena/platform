import json
import uuid

from arena_api.publisher import Publisher
from test_web_api import APIHarness


class Announcements(APIHarness):
    def post(self,body,key=None,client=None):
        return (client or self.client).post('/api/v1/admin/announcements',json=body,headers={'Idempotency-Key':key or uuid.uuid4().hex})

    def test_admin_posts_once_and_read_only_publisher_exposes_only_plain_public_fields(self):
        data=dict(title='<svg onload=alert(1)>',body='Thông báo\n<img src=x onerror=alert(1)>')
        key=uuid.uuid4().hex
        first=self.post(data,key);self.assertEqual(first.status_code,201,first.text)
        self.assertEqual(self.post(data,key).json(),first.json())
        self.assertEqual(self.post(dict(data,title='Changed'),key).status_code,409)
        self.assertEqual(len(self.app.state.store.list('announcement')),1)
        publisher=Publisher(self.settings)
        try:publisher.publish()
        finally:publisher.close()
        guest=self.other_client()
        public=guest.get('/snapshots/announcements.json')
        self.assertEqual(public.status_code,200)
        self.assertEqual(public.json()['items'][0]['body'],data['body'])
        self.assertEqual(set(public.json()['items'][0]),{'id','title','body','created_unix_ms','revision','archived_ms'})
        self.assertEqual(self.app.state.accounts.one("SELECT COUNT(*) AS n FROM audit WHERE action='announcement.created'")['n'],1)

    def test_visibility_is_revision_guarded_and_withdrawal_reaches_snapshot(self):
        item=self.post(dict(title='Thông báo',body='Fixture')).json();ident=item['id']
        path=f'/api/v1/admin/announcements/{ident}/visibility'
        hidden=self.client.post(path,json=dict(expected_revision=1,archived=True))
        self.assertEqual(hidden.status_code,200,hidden.text)
        self.assertEqual(hidden.json()['revision'],2)
        publisher=Publisher(self.settings)
        try:
            publisher.publish()
            self.assertEqual(self.other_client().get('/snapshots/announcements.json').json()['items'],[])
            self.assertEqual(self.client.post(path,json=dict(expected_revision=1,archived=False)).status_code,409)
            self.assertEqual(self.client.post(path,json=dict(expected_revision=2,archived=False)).status_code,200)
            publisher.publish()
            self.assertEqual(len(self.other_client().get('/snapshots/announcements.json').json()['items']),1)
        finally:publisher.close()

    def test_student_guest_csrf_and_invalid_text_cannot_publish(self):
        team,_=self.create_team();guest=self.other_client();body=dict(title='Fixture',body='Public text')
        for client in (team,guest):
            self.assertIn(self.post(body,client=client).status_code,(401,403))
            self.assertIn(client.get('/api/v1/admin/announcements').status_code,(401,403))
        stolen=self.other_client();stolen.cookies.update(self.client.cookies)
        self.assertEqual(self.post(body,client=stolen).status_code,403)
        for invalid in (dict(body,title='bad\nline'),dict(body,body='bad\x00text'),dict(body,body='x'*3001),dict(body,team_id='spoof')):
            self.assertEqual(self.post(invalid).status_code,422)
        self.assertEqual(self.app.state.store.list('announcement'),[])
