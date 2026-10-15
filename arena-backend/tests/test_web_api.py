import io
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import urllib.request

from fastapi.testclient import TestClient
from arena.protocol import Fault
from arena.store import Store
from arena_api.accounts import Accounts, now_ms
from arena_api.app import create_app
from arena_api.dependencies import COOKIE
from arena_api.settings import Lease, Settings


def registration(name="Team One", email="captain@example.test"):
    return {"team": name, "email": email, "phone": "", "members": [
        {"name": "Student " + str(i), "student_id": "2602000" + str(i)} for i in range(3)], "confirmed": True}


class APIHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(data=Path(self.tmp.name), origin="http://127.0.0.1:4181")
        self.app = create_app(self.settings, start_worker=False)
        self.client = TestClient(self.app, base_url=self.settings.origin)
        self.client.__enter__()
        self.extra_clients = []
        self.app.state.accounts.bootstrap_admin("organizer", "test-only-admin-password")
        result = self.client.post("/api/v1/auth/login", json={"username": "organizer", "password": "test-only-admin-password"})
        self.assertEqual(result.status_code, 200, result.text)
        self.client.headers["X-CSRF-Token"] = result.json()["csrf_token"]

    def tearDown(self):
        for client in self.extra_clients:
            client.close()
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def other_client(self):
        client = TestClient(self.app, base_url=self.settings.origin)
        self.extra_clients.append(client)
        return client

    def create_team(self, name="Team One", username="team-one", change=True):
        result = self.client.post("/api/v1/registrations", json=registration(name))
        self.assertEqual(result.status_code, 201, result.text)
        approval = self.client.post(f"/api/v1/admin/registrations/{result.json()['id']}/approve", json={"username": username})
        self.assertEqual(approval.status_code, 201, approval.text)
        credentials = approval.json()
        client = self.other_client()
        login = client.post("/api/v1/auth/login", json={"username": username, "password": credentials["temporary_password"]})
        self.assertEqual(login.status_code, 200, login.text)
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        if change:
            result = client.post("/api/v1/auth/password", json={"current_password": credentials["temporary_password"], "new_password": "test-only-new-team-password"})
            self.assertEqual(result.status_code, 200, result.text)
            client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        return client, credentials


class WebAPI(APIHarness):
    def test_approval_first_password_and_team_profile(self):
        team, issued = self.create_team(change=False)
        self.assertEqual(team.get("/api/v1/teams/me").status_code, 403)
        self.assertEqual(team.get("/api/v1/auth/me").json()["user"]["must_change_password"], True)
        old_cookie = team.cookies.get(COOKIE)
        changed = team.post("/api/v1/auth/password", json={"current_password": issued["temporary_password"], "new_password": "test-only-new-team-password"})
        self.assertEqual(changed.status_code, 200)
        team.headers["X-CSRF-Token"] = changed.json()["csrf_token"]
        self.assertNotEqual(team.cookies.get(COOKIE), old_cookie)
        with self.assertRaises(Fault):
            self.app.state.accounts.principal(old_cookie)
        self.assertEqual(team.get("/api/v1/teams/me").json()["name"], "Team One")
        self.assertEqual(team.get("/api/v1/dashboard").json()["counts"]["submissions"], 0)
        listed = self.client.get("/api/v1/admin/accounts").text
        self.assertNotIn(issued["temporary_password"], listed)
        self.assertNotIn("password_hash", listed)

    def test_csrf_rbac_and_no_team_id_spoofing(self):
        team, _ = self.create_team()
        self.assertEqual(team.get("/api/v1/admin/accounts").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/teams/me").status_code, 403)
        self.assertEqual(team.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        self.assertEqual(team.get("/api/v1/auth/me").status_code, 200)
        result = team.post("/api/v1/matches", json={"team_id": "other", "red_submission_id": "a" * 32, "blue_submission_id": "b" * 32, "delegate_timeout_claims": True})
        self.assertEqual(result.status_code, 422)

    def test_cookie_flags_logout_and_expiry(self):
        team, _ = self.create_team()
        me = team.get("/api/v1/auth/me").json()
        self.assertNotIn("token", me)
        self.assertNotIn("token_hash", me)
        response = team.post("/api/v1/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(team.get("/api/v1/auth/me").status_code, 401)
        login = team.post("/api/v1/auth/login", json={"username": "team-one", "password": "test-only-new-team-password"})
        cookie = login.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/api", cookie)
        with self.app.state.store.lock, self.app.state.store.db:
            self.app.state.store.db.execute("UPDATE sessions SET expires_ms=? WHERE account_id=?", (now_ms() - 1, login.json()["user"]["id"]))
        self.assertEqual(team.get("/api/v1/auth/me").status_code, 401)

    def test_lock_reset_revoke_and_preserve_locked_state(self):
        team, issued = self.create_team()
        account_id = issued["account"]["id"]
        self.assertEqual(self.client.post(f"/api/v1/admin/accounts/{account_id}/lock", json={"locked": True}).status_code, 200)
        self.assertEqual(team.get("/api/v1/auth/me").status_code, 401)
        reset = self.client.post(f"/api/v1/admin/accounts/{account_id}/password", json={})
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(reset.json()["account"]["locked"])
        self.assertEqual(team.post("/api/v1/auth/login", json={"username": "team-one", "password": reset.json()["temporary_password"]}).status_code, 401)
        self.client.post(f"/api/v1/admin/accounts/{account_id}/lock", json={"locked": False})
        self.assertEqual(team.post("/api/v1/auth/login", json={"username": "team-one", "password": "test-only-new-team-password"}).status_code, 401)
        login = team.post("/api/v1/auth/login", json={"username": "team-one", "password": reset.json()["temporary_password"]})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["user"]["must_change_password"])

    def test_atomic_approval_rollback_and_conflicts(self):
        self.create_team()
        response = self.client.post("/api/v1/registrations", json=registration("Team Two"))
        ident = response.json()["id"]
        path = f"/api/v1/admin/registrations/{ident}/approve"
        self.assertEqual(self.client.post(path, json={"username": "team-one"}).status_code, 409)
        pending = self.client.get("/api/v1/admin/registrations").json()["items"]
        self.assertTrue(any(r["id"] == ident for r in pending))
        self.assertEqual(self.client.post(path, json={"username": "team-two"}).status_code, 201)
        self.assertEqual(self.client.post(path, json={"username": "team-two"}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/registrations", json=registration("Team Two")).status_code, 409)
        self.assertEqual(len(self.client.get("/api/v1/admin/accounts").json()["items"]), 2)

    def test_recovery_generic_response_and_admin_resolution(self):
        _, issued = self.create_team()
        a = self.client.post("/api/v1/auth/recovery", json={"username": "team-one", "email": "captain@example.test"})
        b = self.client.post("/api/v1/auth/recovery", json={"username": "no-account", "email": "missing@example.test"})
        self.assertEqual(a.json(), b.json())
        self.assertEqual(a.status_code, 202)
        self.assertEqual(len(self.client.get("/api/v1/admin/recoveries").json()["items"]), 1)
        self.client.post(f"/api/v1/admin/accounts/{issued['account']['id']}/password", json={})
        self.assertEqual(self.client.get("/api/v1/admin/recoveries").json()["items"], [])

    def test_upload_idempotency_and_cross_team_ownership(self):
        a, _ = self.create_team()
        b, _ = self.create_team("Team Two", "team-two")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("bot.cpp", "int main(){}")
        headers = {"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0", "Idempotency-Key": "web-unit-upload"}
        result = a.post("/api/v1/submissions", content=output.getvalue(), headers=headers)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertNotIn("source_path", result.text)
        self.assertEqual(a.post("/api/v1/submissions", content=output.getvalue(), headers=headers).json()["id"], result.json()["id"])
        self.assertEqual(b.get("/api/v1/submissions").json()["items"], [])
        result = b.post("/api/v1/benchmarks", json={"submission_id": result.json()["id"]}, headers={"Idempotency-Key": "web-unit-job"})
        self.assertEqual(result.status_code, 404)

    def test_worker_offline_fails_closed_without_a_queued_job(self):
        team, issued = self.create_team()
        source = self.app.state.store.create("submission", issued["account"]["team_id"], {"version": "v1.0.0"})
        with patch("arena.sandbox.image_id", side_effect=Fault("worker_unavailable", "offline")):
            result = team.post("/api/v1/benchmarks", json={"submission_id": source["id"]}, headers={"Idempotency-Key": "offline-key"})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.app.state.store.list("job"), [])

    def test_event_replay_sse_cursor_and_cross_team_privacy(self):
        a, issued = self.create_team()
        b, _ = self.create_team("Team Two", "team-two")
        job = self.app.state.store.create("job", issued["account"]["team_id"], {"kind": "match", "status": "completed", "report": None})
        for i in range(3):
            self.app.state.store.event(job["id"], "state", {"ply": i})
        path = f"/api/v1/jobs/{job['id']}"
        self.assertEqual(b.get(path).status_code, 404)
        self.assertEqual(b.get(path + "/events").status_code, 404)
        self.assertEqual([e["seq"] for e in a.get(path + "/events?after=1").json()["items"]], [2, 3])
        response = a.get(path + "/events", headers={"Accept": "text/event-stream", "Last-Event-ID": "2"})
        self.assertIn("id: 3\n", response.text)
        self.assertNotIn("id: 2\n", response.text)
        self.assertEqual(a.get(path + "/events", headers={"Last-Event-ID": "-1"}).status_code, 400)
        self.assertEqual(a.get(path + "/report").status_code, 409)

    def test_validation_does_not_echo_secrets_or_accept_extra_roles(self):
        secret = "test-only-invalid-password-" * 8
        response = self.client.post("/api/v1/auth/login", json={"username": "organizer", "password": secret, "role": "admin"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        self.assertNotIn("password_hash", response.text)
        data = registration()
        data["members"][1]["student_id"] = data["members"][0]["student_id"]
        self.assertEqual(self.client.post("/api/v1/registrations", json=data).status_code, 422)
        data = registration()
        data["confirmed"] = 1
        self.assertEqual(self.client.post("/api/v1/registrations", json=data).status_code, 422)

    def test_host_origin_and_body_limits(self):
        self.assertEqual(self.client.get("/api/v1/time", headers={"Host": "evil.invalid"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        response = self.client.post("/api/v1/auth/login", content=b"x" * 16385)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.post("/api/v1/auth/login", content=b'{"username":"a","username":"b"}', headers={"Content-Type": "application/json"}).status_code, 400)

    def test_login_throttle_persists_and_audit_has_no_secrets(self):
        for _ in range(10):
            result = self.client.post("/api/v1/auth/login", json={"username": "missing", "password": "test-only-wrong-password"})
            self.assertEqual(result.status_code, 401)
        result = self.client.post("/api/v1/auth/login", json={"username": "missing", "password": "test-only-wrong-password"})
        self.assertEqual(result.status_code, 429)
        self.assertIn("Retry-After", result.headers)
        audit = self.client.get("/api/v1/admin/audit").text
        self.assertNotIn("test-only", audit)
        self.assertNotIn("password_hash", audit)

    def test_openapi_documents_cookie_csrf_and_typed_inputs(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["info"]["version"], "0.6.0")
        self.assertEqual(schema["paths"]["/api/v1/matches"]["post"]["security"], [{"cookieAuth": [], "csrfAuth": []}])
        self.assertIn("Credentials", schema["components"]["schemas"])
        self.assertIn("application/zip", schema["paths"]["/api/v1/submissions"]["post"]["requestBody"]["content"])
        self.assertEqual(self.client.get("/docs").status_code, 200)


class MigrationAndPersistence(unittest.TestCase):
    def test_upgrade_v1_preserves_existing_credentials_and_session(self):
        from argon2 import PasswordHasher
        from arena_api.accounts import token_hash
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, recover_jobs=False)
            sql = (Path(__file__).resolve().parents[1] / "arena_api" / "migrations" / "001_accounts.sql").read_text()
            store.db.executescript(sql + "\nCREATE TABLE schema_migrations(version INTEGER PRIMARY KEY); INSERT INTO schema_migrations VALUES(1);")
            encoded = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1).hash("migration-only-existing-password")
            with store.db:
                store.db.execute("INSERT INTO accounts VALUES('existing-admin','organizer',?,'admin',NULL,0,0,0,?)", (encoded, now_ms()))
                store.db.execute("INSERT INTO sessions VALUES(?,'existing-admin','existing-csrf',?)", (token_hash("migration-only-existing-token"), now_ms() + 60000))
            accounts = Accounts(store)
            self.assertEqual(accounts.principal("migration-only-existing-token")["user"]["id"], "existing-admin")
            self.assertTrue(accounts.verify(accounts.one("SELECT password_hash FROM accounts")["password_hash"], "migration-only-existing-password"))
            self.assertEqual([r[0] for r in store.db.execute("SELECT version FROM schema_migrations ORDER BY version")], [1, 2, 3, 4])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM competitions").fetchone()[0], 0)
            store.db.close()

    def test_cli_bootstrap_serve_and_graceful_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            origin = f"http://127.0.0.1:{port}"
            env = {**os.environ, "ARENA_API_DATA": directory, "ARENA_API_ORIGIN": origin, "ARENA_API_COOKIE_SECURE": "0"}
            base = [sys.executable, "-m", "arena_api"]
            root = Path(__file__).resolve().parents[1]
            secret = "cli-test-only-admin-password"
            result = subprocess.run(base + ["bootstrap-admin", "--username", "organizer", "--password-stdin"], input=secret + "\n", text=True, capture_output=True, env=env, cwd=root, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(secret, result.stdout + result.stderr)
            process = subprocess.Popen(base + ["serve", "--port", str(port)], env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                until, ready = time.monotonic() + 15, False
                while time.monotonic() < until and process.poll() is None:
                    try:
                        with urllib.request.urlopen(origin + "/api/v1/health/live", timeout=1) as response:
                            ready = json.load(response)["ok"]
                            break
                    except OSError:
                        time.sleep(0.05)
                self.assertTrue(ready, "CLI service did not start")
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
            # Uvicorn re-raises SIGTERM after graceful lifespan cleanup on POSIX.
            self.assertIn(process.returncode, (0, -signal.SIGTERM), stderr)
            self.assertIn("Application shutdown complete", stderr)
            self.assertNotIn(secret, stdout + stderr)
            lease = Lease(directory)
            lease.close()

    def test_migration_is_repeatable_and_session_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, recover_jobs=False)
            accounts = Accounts(store)
            accounts.bootstrap_admin("organizer", "test-only-admin-password")
            session = accounts.login("organizer", "test-only-admin-password", "unit")
            token = session["token"]
            self.assertNotIn(token, json.dumps(accounts.rows("SELECT * FROM sessions")))
            store.db.close()
            store = Store(directory, recover_jobs=False)
            accounts = Accounts(store)
            self.assertEqual(accounts.principal(token)["user"]["role"], "admin")
            self.assertEqual([r[0] for r in store.db.execute("SELECT version FROM schema_migrations ORDER BY version")], [1, 2, 3, 4])
            store.db.close()

    def test_single_controller_lease_and_public_secure_cookie_config(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Lease(directory)
            with self.assertRaises(RuntimeError):
                Lease(directory)
            lease.close()
        # This test concerns origin validation, independent of the process cwd
        # (DrvFS can invalidate getcwd after parallel Docker integration runs).
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                Settings(data=Path(directory), origin="http://arena.example")
            with self.assertRaises(ValueError):
                Settings(data=Path(directory), origin="https://arena.example", cookie_secure=False)
            Settings(data=Path(directory), origin="https://arena.example", cookie_secure=True)


if __name__ == "__main__":
    unittest.main()
