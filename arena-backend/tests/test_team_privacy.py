from test_web_api import APIHarness, registration


class TeamPrivacy(APIHarness):
    def test_admin_profile_contacts_never_return_through_team_apis(self):
        profile = registration('Private Contacts', 'private-contact@example.test') | {'phone': '0912345678'}
        issued = self.client.post('/api/v1/admin/teams', json={'profile': profile, 'username': 'private-team'}).json()
        team = self.other_client()
        login = team.post('/api/v1/auth/login', json={'username': 'private-team', 'password': issued['temporary_password']}).json()
        team.headers['X-CSRF-Token'] = login['csrf_token']
        changed = team.post('/api/v1/auth/password', json={'current_password': issued['temporary_password'], 'new_password': 'test-only-new-password'}).json()
        team.headers['X-CSRF-Token'] = changed['csrf_token']
        for path in ['/teams/me', '/dashboard', '/users/me']:
            response = team.get('/api/v1'+path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(profile['email'], response.text)
            self.assertNotIn(profile['phone'], response.text)
            value = response.json() if path == '/teams/me' else response.json()['team']
            for field in ['email', 'phone', 'members', 'legacy_roster']:
                self.assertNotIn(field, value)
            self.assertEqual(value['name'], 'Private Contacts')
            if path=='/users/me':self.assertEqual(value['member_count'],4)
        private = self.client.get('/api/v1/admin/teams/'+issued['account']['team_id']).json()
        self.assertEqual(private['email'], profile['email'])
        self.assertEqual(private['phone'], profile['phone'])
        self.assertEqual(len(private['members']), 4)
        self.assertEqual(team.get('/api/v1/admin/users').status_code, 403)
