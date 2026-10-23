"""M1 operational abuse cases through the authenticated HTTP boundary."""
from test_web_api import APIHarness


class M1Security(APIHarness):
    def test_one_guessed_account_cannot_lock_out_another_account_on_the_same_nat(self):
        team,_=self.create_team()
        guest=self.other_client()
        for _ in range(10):
            self.assertEqual(guest.post('/api/v1/auth/login',json=dict(username='guessed-person',password='wrong')).status_code,401)
        self.assertEqual(guest.post('/api/v1/auth/login',json=dict(username='guessed-person',password='wrong'),headers={'X-Forwarded-For':'198.51.100.99','CF-Connecting-IP':'198.51.100.99'}).status_code,429)
        response=guest.post('/api/v1/auth/login',json=dict(username='team-one',password='test-only-new-team-password'))
        self.assertEqual(response.status_code,200,response.text)

    def test_students_cannot_issue_or_register_grader_tokens_or_read_admin_identity(self):
        team,_=self.create_team()
        for path in ('/api/v1/admin/graders','/api/v1/admin/github/duplicates'):
            self.assertEqual(team.get(path).status_code,403)
        self.assertEqual(team.post('/api/v1/admin/graders',json=dict(name='Untrusted PC')).status_code,403)
        self.assertEqual(team.post('/api/v1/grader/hello',json=dict(platform='windows')).status_code,401)
