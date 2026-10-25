import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import smtplib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import MagicMock, patch

from arena.conf import Secrets
from arena.protocol import Fault
from arena.store import Store
from arena_api.mailer import Mailer, init_key, smtp_send
from arena_api.outbox import DAY, HOUR, MailQueue
from arena_api.settings import EmailProvider, Settings
from test_web_api import APIHarness


class MailDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name, recover_jobs=False)
        self.addCleanup(self.store.db.close)
        self.current = 20_000 * DAY + 10 * HOUR
        self.providers = tuple(EmailProvider(name, "127.0.0.1", 1025, "none", "no-reply@vnu.test", daily_limit=10, hourly_limit=2) for name in ("first", "second"))
        self.settings = Settings(data=Path(self.tmp.name), email_providers=self.providers,
                                 secrets=Secrets({"verification_hmac_key": "test-only-" + "x" * 40}))
        self.queue = MailQueue(self.store, self.settings, clock=lambda: self.current)
        self.sent = []

    def send(self, provider, credentials, message):
        self.sent.append((provider.name, message))

    def deliver(self, recipient="22000001@vnu.test"):
        job = self.queue.enqueue(recipient)
        with patch("arena_api.outbox.secrets.randbelow", return_value=123456):
            self.assertTrue(Mailer(self.queue, send=self.send).once())
        return job["id"]

    def test_request_has_no_code_and_expiry_starts_at_delivery_not_enqueue(self):
        job = self.queue.enqueue("22000001@vnu.test")
        self.assertIsNone(self.queue.get(job["id"])["code_hash"])
        self.assertEqual(self.sent, [])
        self.current += 15 * 60_000
        with patch("arena_api.outbox.secrets.randbelow", return_value=123456):
            Mailer(self.queue, send=self.send).once()
        status = self.queue.status(job["id"])
        self.assertEqual(status["sent_ms"], self.current)
        self.assertEqual(status["expires_ms"], self.current + 600_000)
        self.assertEqual(self.queue.verify(job["id"], "123456")["verified_ms"], self.current)
        self.current += 600_000
        with self.assertRaises(Fault):
            self.queue.verify(job["id"], "123456")

    def test_message_is_vietnamese_multipart_and_plaintext_code_never_enters_db_or_status(self):
        ident = self.deliver()
        mail = self.sent[0][1]
        self.assertEqual(str(mail["Subject"]), "Mã xác minh UET Bot Arena: 123456")
        self.assertTrue(mail["Message-ID"] and mail["Date"])
        self.assertIn("Nếu bạn không đăng ký, hãy bỏ qua thư này.", mail.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("123456", mail.get_body(preferencelist=("html",)).get_content())
        row = self.queue.get(ident)
        self.assertEqual(len(row["code_hash"]), 64)
        self.assertNotIn("123456", json.dumps(row))
        self.assertNotIn("code_hash", self.queue.status(ident))

    def test_quota_moves_to_next_provider_and_survives_a_new_connection(self):
        self.deliver("22000001@vnu.test")
        self.deliver("22000002@vnu.test")
        second = Store(self.tmp.name, recover_jobs=False)
        try:
            queue = MailQueue(second, self.settings, clock=lambda: self.current)
            queue.enqueue("22000003@vnu.test")
            Mailer(queue, send=self.send).once()
            self.assertEqual([p for p, _ in self.sent], ["first", "first", "second"])
            self.assertEqual(queue.summary()["providers"][0]["hourly_remaining"], 0)
        finally:
            second.db.close()

    def test_auth_and_temporary_failure_fall_back_without_leaking_smtp_details(self):
        job = self.queue.enqueue("22000001@vnu.test")
        def failed_first(provider, credentials, mail):
            if provider.name == "first":
                raise smtplib.SMTPAuthenticationError(535, b"must-not-be-persisted-password")
            self.send(provider, credentials, mail)
        Mailer(self.queue, send=failed_first).once()
        self.assertEqual(self.queue.get(job["id"])["provider"], "second")
        self.assertEqual(self.queue.summary()["providers"][0]["error"], "smtp_auth")
        self.assertNotIn("must-not-be-persisted", json.dumps(self.queue.summary()))
        self.current += HOUR
        job = self.queue.enqueue("22000002@vnu.test")
        def transient(provider, credentials, mail):
            if provider.name == "first":
                raise smtplib.SMTPDataError(451, b"temporary SMTP problem")
            self.send(provider, credentials, mail)
        Mailer(self.queue, send=transient).once()
        self.assertEqual(self.queue.get(job["id"])["status"], "sent")
        self.assertEqual(self.sent[-1][0], "second")

    def test_all_failures_back_off_and_expired_waiting_mail_is_dropped(self):
        job = self.queue.enqueue("22000001@vnu.test")
        def unavailable(*_):
            raise OSError("must-not-appear-in-public-error")
        worker = Mailer(self.queue, send=unavailable)
        self.assertTrue(worker.once())
        row = self.queue.get(job["id"])
        self.assertEqual(row["status"], "failed")
        self.assertGreater(row["next_attempt_ms"], self.current)
        self.assertFalse(worker.once())
        self.assertNotIn("must-not-appear", json.dumps(row))
        self.current += 1_800_000
        self.assertFalse(worker.once())
        self.assertEqual(self.queue.status(job["id"])["status"], "dropped")
        self.assertEqual(self.sent, [])

    def test_exhausted_providers_return_quota_reset_time_without_generating_a_code(self):
        for index in range(4):
            self.deliver(f"2200000{index}@vnu.test")
        job = self.queue.enqueue("22000009@vnu.test")
        self.assertEqual(job["estimated_sent_ms"], self.current // HOUR * HOUR + HOUR)
        Mailer(self.queue, send=self.send).once()
        self.assertIsNone(self.queue.get(job["id"])["code_hash"])
        self.assertEqual(self.queue.get(job["id"])["attempts"], 0)

    def test_wrong_code_limit_resend_throttle_and_newer_delivered_code(self):
        ident = self.deliver()
        with self.assertRaises(Fault) as caught:
            self.queue.enqueue("22000001@vnu.test")
        self.assertEqual(caught.exception.code, "rate_limited")
        for _ in range(5):
            with self.assertRaises(Fault):
                self.queue.verify(ident, "000000")
        with self.assertRaises(Fault):
            self.queue.verify(ident, "123456")
        self.current += 60_000
        newest = self.deliver()
        self.assertTrue(self.queue.verify(newest, "123456")["verified_ms"])
        self.assertEqual(self.queue.status(ident)["attempts_remaining"], 0)

    def test_codes_per_address_are_capped_per_hour_and_per_day_across_purposes(self):
        address = "22000002@vnu.test"
        first = self.current
        for purpose in ("verify", "reset", "reset", "verify", "reset"):
            self.queue.enqueue(address, purpose)
            self.current += 61_000
        with self.assertRaises(Fault) as caught:
            self.queue.enqueue(address, "reset")
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(caught.exception.retry_after, (first + HOUR - self.current + 999) // 1000)
        self.queue.enqueue("22000003@vnu.test", "reset")  # other addresses are unaffected
        self.current = first + HOUR + 300_000  # the first five are now outside the hourly window
        for _ in range(5):
            self.queue.enqueue(address, "reset")
            self.current += 61_000
        self.current = first + 3 * HOUR
        with self.assertRaises(Fault) as caught:
            self.queue.enqueue(address, "verify")
        self.assertEqual(caught.exception.retry_after, (first + DAY - self.current + 999) // 1000, "ten codes per day")
        self.current = first + DAY + 1
        self.queue.enqueue(address, "verify")

    def test_sql_claims_and_reservations_fence_two_workers(self):
        providers = (replace(self.providers[0], hourly_limit=1),)
        settings = replace(self.settings, email_providers=providers)
        self.queue.enqueue("22000001@vnu.test")
        self.queue.enqueue("22000002@vnu.test")
        second = Store(self.tmp.name, recover_jobs=False)
        try:
            a = MailQueue(self.store, settings, clock=lambda: self.current)
            b = MailQueue(second, settings, clock=lambda: self.current)
            first, next_job = a.claim(), b.claim()
            self.assertNotEqual(first["id"], next_job["id"])
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda pair: pair[0].reserve(pair[1], providers[0]), ((a, first), (b, next_job))))
            self.assertEqual(sum(bool(r[0]) for r in results), 1)
            self.current += 91_000
            taken = b.claim()
            self.assertEqual(taken["id"], first["id"])
            with self.assertRaises(Fault) as lost:
                a.new_code(first)
            self.assertEqual(lost.exception.code, "mail_claim_lost")
        finally:
            second.db.close()

    def test_only_a_newer_delivered_code_supersedes_the_previous_code(self):
        first = self.deliver()
        self.current += 60_000
        second = self.queue.enqueue("22000001@vnu.test")
        self.assertTrue(self.queue.verify(first, "123456")["verified_ms"])
        with patch("arena_api.outbox.secrets.randbelow", return_value=654321):
            Mailer(self.queue, send=self.send).once()
        with self.assertRaises(Fault):
            self.queue.verify(first, "123456")
        self.assertTrue(self.queue.verify(second["id"], "654321")["verified_ms"])


class MailAPI(APIHarness):
    def setUp(self):
        super().setUp()
        from arena_api.settings import AccountRules
        settings = replace(self.settings,
                           secrets=Secrets({"verification_hmac_key": "api-test-only-" + "x" * 40}),
                           email_providers=(EmailProvider("mailpit", "127.0.0.1", 1025, "none", "no-reply@vnu.test", daily_limit=1, hourly_limit=1),),
                           account_rules=AccountRules(student_email=r"^\d{8}@vnu\.test$", staff_domains=("vnu.test",)))
        self.app.state.settings = settings
        self.queue = self.app.state.mail = MailQueue(self.app.state.store, settings)

    def test_public_request_only_queues_and_verification_proof_is_not_public_metadata(self):
        guest = self.other_client()
        for path in ("/api/v1/auth/email/request", "/api/v1/auth/email/requests/{ident}", "/api/v1/auth/email/verify"):
            for operation in self.app.openapi()["paths"][path].values():
                self.assertNotIn("security", operation)
        with patch("arena_api.mailer.smtplib.SMTP") as smtp:
            queued = guest.post("/api/v1/auth/email/request", json={"email": " 22000001@VNU.TEST "})
            self.assertEqual(queued.status_code, 202, queued.text)
            smtp.assert_not_called()
        ident = queued.json()["id"]
        self.assertEqual(queued.json()["status"], "queued")
        self.assertIsNone(self.queue.get(ident)["code_hash"])
        with patch("arena_api.outbox.secrets.randbelow", return_value=42):
            Mailer(self.queue, send=lambda *_: None).once()
        verified = guest.post("/api/v1/auth/email/verify", json={"request_id": ident, "code": "000042"})
        self.assertEqual(verified.status_code, 200, verified.text)
        token = verified.json()["verification_token"]
        self.assertEqual(len(token), 43)
        public = guest.get("/api/v1/auth/email/requests/" + ident)
        self.assertNotIn(token, public.text)
        self.assertNotIn("proof_hash", public.text)
        self.assertNotIn("code_hash", public.text)
        self.assertNotIn("000042", public.text)
        self.assertNotIn(token, json.dumps(self.queue.get(ident)))

    def test_school_email_rules_resend_and_code_schema_do_not_echo_inputs(self):
        guest = self.other_client()
        for email in ("22000001+tag@vnu.test", "student@external.test", "a\n@vnu.test"):
            self.assertEqual(guest.post("/api/v1/auth/email/request", json={"email": email}).status_code, 400)
        self.assertEqual(guest.post("/api/v1/auth/email/request", json={"email": "staff@vnu.test"}).status_code, 202)
        self.assertEqual(guest.post("/api/v1/auth/email/request", json={"email": "staff@vnu.test"}).status_code, 429)
        bad = guest.post("/api/v1/auth/email/verify", json={"request_id": "a" * 32, "code": "secret-marker-too-long"})
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn("secret-marker", bad.text)

    def test_email_metrics_are_admin_only_and_include_low_quota_warning(self):
        guest = self.other_client()
        self.assertEqual(guest.get("/api/v1/admin/email").status_code, 401)
        team, _ = self.create_team()
        self.assertEqual(team.get("/api/v1/admin/email").status_code, 403)
        self.queue.enqueue("22000001@vnu.test")
        Mailer(self.queue, send=lambda *_: None).once()
        result = self.client.get("/api/v1/admin/email")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["counts"]["sent"], 1)
        self.assertEqual(result.json()["providers"][0]["sent"], 1)
        self.assertTrue(result.json()["providers"][0]["low_quota"])
        self.assertNotIn("verification_hmac_key", result.text)


class MailerInstallation(unittest.TestCase):
    def test_init_key_preserves_other_secrets_and_never_rotates_or_prints_the_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.toml"
            path.write_text('existing_password = "test-only-existing-password"\n')
            output = io.StringIO()
            with patch.dict(os.environ, {"ARENA_SECRETS": str(path)}), redirect_stdout(output):
                init_key()
                first = path.read_bytes()
                init_key()
            values = Secrets.load(path)
            self.assertEqual(values.get("existing_password"), "test-only-existing-password")
            self.assertEqual(path.read_bytes(), first)
            self.assertGreaterEqual(len(values.get("verification_hmac_key")), 32)
            self.assertNotIn(values.get("verification_hmac_key"), output.getvalue())

    def test_smtp_authentication_always_follows_tls(self):
        credentials = Secrets({"smtp_user": "test-user", "smtp_password": "test-password"})
        for mode in ("starttls", "ssl"):
            with self.subTest(mode=mode), patch("arena_api.mailer.smtplib.SMTP") as plain, patch("arena_api.mailer.smtplib.SMTP_SSL") as tls:
                provider = EmailProvider("relay", "smtp.example.test", 587, mode, "sender@vnu.test", user_secret="smtp_user", password_secret="smtp_password")
                client = MagicMock()
                client.__enter__.return_value = client
                client.send_message.return_value = {}
                (tls if mode == "ssl" else plain).return_value = client
                smtp_send(provider, credentials, object())
                client.login.assert_called_once_with("test-user", "test-password")
                if mode == "starttls":
                    calls = [c[0] for c in client.method_calls]
                    self.assertLess(calls.index("starttls"), calls.index("login"))
                else:
                    plain.assert_not_called()
                    client.starttls.assert_not_called()
