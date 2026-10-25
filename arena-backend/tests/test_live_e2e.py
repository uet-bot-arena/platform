"""Offline safety checks for the explicitly invoked production acceptance script."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import httpx
import live_e2e


class LiveAcceptanceSafety(TestCase):
    def test_github_accepts_successful_202_delivery(self):
        for status in (200, 202, 204, 299):
            live_e2e.check_webhook_status(status)
        for status in (199, 300, 401, 500):
            with self.assertRaises(AssertionError):
                live_e2e.check_webhook_status(status)

    def test_private_dir_comes_from_environment_not_a_fixed_machine_path(self):
        with patch.dict(live_e2e.os.environ, {'ARENA_PRIVATE_DIR': '/srv/owner-secrets'}):
            self.assertEqual(live_e2e.private_dir(), Path('/srv/owner-secrets'))
        with patch.dict(live_e2e.os.environ, {}, clear=True), \
                patch.object(live_e2e.Path, 'home', return_value=Path('/home/btc')):
            self.assertEqual(live_e2e.private_dir(), Path('/home/btc/.uet-arena'))

    def test_post_has_idempotency_and_does_not_log_response_credentials(self):
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(403, json={'password': 'must-never-appear',
                                            'csrf_token': 'also-private'})

        with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(transport)) as client:
            for _ in range(2):
                with self.assertRaisesRegex(AssertionError, '^POST /auth/login: HTTP 403, expected 200$'):
                    live_e2e.api(client, 'POST', '/auth/login', json={'password': 'private-input'})
        self.assertNotEqual(requests[0].headers['Idempotency-Key'], requests[1].headers['Idempotency-Key'])

    def test_login_client_is_ready_and_closes_on_failure(self):
        real_client = httpx.Client
        clients = []

        def factory(**kwargs):
            def transport(request):
                self.assertEqual(request.headers['User-Agent'], 'uet-arena-e2e/1')
                self.assertEqual(request.headers['Origin'], 'https://example.test')
                return httpx.Response(200, json={'csrf_token': 'test-csrf'},
                                      headers={'set-cookie': 'session=private; Path=/'})
            client = real_client(**kwargs, transport=httpx.MockTransport(transport))
            clients.append(client)
            return client

        with patch.object(live_e2e.httpx, 'Client', side_effect=factory):
            client = live_e2e.login('https://example.test', 'test-user', 'test-password')
            try:
                self.assertEqual(client.headers['X-CSRF-Token'], 'test-csrf')
                self.assertEqual(client.cookies['session'], 'private')
                self.assertFalse(client.is_closed)
            finally:
                client.close()
        self.assertTrue(all(client.is_closed for client in clients))

    def test_demo_guard_cannot_mutate_live_hub(self):
        result = {}
        with patch.object(live_e2e, 'api', side_effect=AssertionError('unexpected network mutation')) as api:
            with self.assertRaisesRegex(AssertionError, 'B blocked'):
                live_e2e.part_b('https://example.test', result)
            api.assert_not_called()
        self.assertEqual(len(result['blockers']), 3)
