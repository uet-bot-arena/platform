from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from arena.conf import Secrets
from arena.protocol import Fault
from arena.store import Store
from arena_api.accounts import Accounts, token_hash
from arena_api.mailer import Mailer
from arena_api.outbox import MailQueue
from arena_api.people import People
from arena_api.settings import AccountRules, EmailProvider
from arena_api.teams import HOUR, Teams
from test_web_api import APIHarness

PASSWORD = "personal-test-only-password"


class PeopleAPI(APIHarness):
    def setUp(self):
        super().setUp()
        self.current = 1792740000000
        clock = patch("arena_api.accounts.now_ms", lambda: self.current)
        clock.start()
        self.addCleanup(clock.stop)
        rules = AccountRules(student_email=r"^\d{8}@vnu\.test$", staff_domains=("vnu.test",), freshman_cohort=2026)
        settings = replace(self.settings, account_rules=rules,
                           secrets=Secrets({"verification_hmac_key": "test-only-people-" + "x" * 40}),
                           email_providers=(EmailProvider("mailpit", "127.0.0.1", 1025, "none", "no-reply@vnu.test", daily_limit=10000, hourly_limit=10000),))
        state = self.app.state
        state.settings = settings
        self.queue = state.mail = MailQueue(state.store, settings, clock=lambda: self.current)
        state.people = People(state.accounts, self.queue, rules)
        self.teams = state.teams = Teams(state.accounts, rules, clock=lambda: self.current)
        state.competitions.teams = self.teams
        self.serial = 0

    def proof(self, email, purpose="verify"):
        guest = self.other_client()
        response = guest.post("/api/v1/auth/email/request", json={"email": email, "purpose": purpose})
        self.assertEqual(response.status_code, 202, response.text)
        ident = response.json()["id"]
        with patch("arena_api.outbox.secrets.randbelow", return_value=123456):
            Mailer(self.queue, send=lambda *_: None).once()
        response = guest.post("/api/v1/auth/email/verify", json={"request_id": ident, "code": "123456"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"request_id": ident, "verification_token": response.json()["verification_token"]}

    def login_person(self, email, password=PASSWORD):
        client = self.other_client()
        result = client.post("/api/v1/auth/login", json={"username": email, "password": password})
        self.assertEqual(result.status_code, 200, result.text)
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        return client, result.json()["user"]

    def person(self, email=None, name=None):
        self.serial += 1
        email = email or f"2600{self.serial:04d}@vnu.test"
        body = self.proof(email) | {"password": PASSWORD, "display_name": name or f"Người {self.serial}"}
        response = self.other_client().post("/api/v1/auth/enroll", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return self.login_person(email)

    def team(self, client, name="Đội bốn người", guest=False):
        result = client.post("/api/v1/teams", json={"name": name, "guest": guest})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def profile(self, client, name="Người tìm đội", **extra):
        result = client.post("/api/v1/users/me", json={"display_name": name, "looking": True, **extra})
        self.assertEqual(result.status_code, 200, result.text)

    def test_normalized_student_all_faculties_staff_and_email_login(self):
        for email in (" 26000001@VNU.TEST ", "26999999@vnu.test"):
            _, user = self.person(email)
            self.assertEqual((user["role"], user["cohort"]), ("student", 2026))
            self.assertEqual(user["email"], email.strip().lower())
        _, staff = self.person("lecturer@vnu.test")
        self.assertEqual(staff["role"], "staff")
        self.assertIsNone(staff["cohort"])
        guest = self.other_client()
        for email in ("26000001+alias@vnu.test", "26000001@example.test", "a\r\nb@vnu.test"):
            self.assertEqual(guest.post("/api/v1/auth/email/request", json={"email": email}).status_code, 400)

    def test_proof_purpose_expiry_replay_and_failure_roll_back_consumption(self):
        proof = self.proof("26000001@vnu.test")
        body = proof | {"password": PASSWORD, "display_name": "Người thử"}
        guest = self.other_client()
        self.assertEqual(guest.post("/api/v1/auth/reset", json=body).status_code, 400)
        self.assertIsNone(self.queue.get(proof["request_id"])["consumed_ms"])
        self.assertEqual(guest.post("/api/v1/auth/enroll", json=body | {"verification_token": "x" * 43}).status_code, 400)
        self.assertEqual(guest.post("/api/v1/auth/enroll", json=body).status_code, 201)
        self.assertEqual(guest.post("/api/v1/auth/enroll", json=body).status_code, 400)
        self.current += 60_000
        duplicate = self.proof("26000001@vnu.test")
        self.assertEqual(guest.post("/api/v1/auth/enroll", json=duplicate | {"password": PASSWORD, "display_name": "Trùng email"}).status_code, 409)
        self.assertIsNone(self.queue.get(duplicate["request_id"])["consumed_ms"])
        expires = self.proof("26000002@vnu.test")
        self.current += 600_000
        self.assertEqual(guest.post("/api/v1/auth/enroll", json=expires | {"password": PASSWORD, "display_name": "Hết hạn"}).status_code, 400)

    def test_new_delivered_code_revokes_an_already_issued_proof(self):
        old = self.proof("26000001@vnu.test")
        self.current += 60_000
        self.proof("26000001@vnu.test")
        response = self.other_client().post("/api/v1/auth/enroll", json=old | {"password": PASSWORD, "display_name": "Mã cũ"})
        self.assertEqual(response.status_code, 400)

    def test_reset_revokes_all_sessions_and_requires_fresh_login(self):
        first, user = self.person()
        second, _ = self.login_person(user["email"])
        proof = self.proof(user["email"], "reset")
        new_password = "reset-test-only-new-password"
        result = self.other_client().post("/api/v1/auth/reset", json=proof | {"password": new_password})
        self.assertEqual(result.status_code, 200, result.text)
        for client in (first, second):
            self.assertEqual(client.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(self.other_client().post("/api/v1/auth/login", json={"username": user["email"], "password": PASSWORD}).status_code, 401)
        self.login_person(user["email"], new_password)
        self.assertEqual(self.other_client().post("/api/v1/auth/reset", json=proof | {"password": new_password}).status_code, 400)

    def test_reset_for_address_without_account_spends_no_mail_and_looks_the_same(self):
        _, user = self.person()
        guest, sent = self.other_client(), []
        unknown = guest.post("/api/v1/auth/email/request", json={"email": "26999999@vnu.test", "purpose": "reset"})
        known = guest.post("/api/v1/auth/email/request", json={"email": user["email"], "purpose": "reset"})
        self.assertEqual((unknown.status_code, known.status_code), (202, 202))
        with patch("arena_api.outbox.secrets.randbelow", return_value=123456):
            while Mailer(self.queue, send=lambda provider, credentials, mail: sent.append(mail["To"])).once():
                pass
        self.assertEqual(sent, [user["email"]], "only the existing account receives a reset mail")
        statuses = [guest.get("/api/v1/auth/email/requests/" + r.json()["id"]).json() for r in (unknown, known)]
        visible = lambda s: {k: s[k] for k in ("purpose", "status", "attempts_remaining")} | {"has_expiry": s["expires_ms"] is not None}
        self.assertEqual(visible(statuses[0]), visible(statuses[1]), "unknown and known addresses are indistinguishable")
        attempts = self.queue.db.execute("SELECT COUNT(*) FROM email_attempts WHERE request_id=?", (unknown.json()["id"],)).fetchone()[0]
        self.assertEqual(attempts, 0, "no provider attempt, so no quota is used")
        bad = guest.post("/api/v1/auth/email/verify", json={"request_id": unknown.json()["id"], "code": "123456"})
        self.assertEqual(bad.status_code, 400, "no code can verify a reset that was never mailed")

    def test_email_ip_limit_and_profile_name_limits(self):
        with self.app.state.store.transaction():
            self.app.state.store.db.execute("INSERT INTO rate_limits VALUES(?,?,?)", (token_hash("email-code:ip:testclient"), 1000, self.current + 100 * HOUR))
        result = self.other_client().post("/api/v1/auth/email/request", json={"email": "26000001@vnu.test"})
        self.assertEqual(result.status_code, 429, result.text)
        with self.app.state.store.transaction():
            self.app.state.store.db.execute("DELETE FROM rate_limits")
        client, _ = self.person()
        for name in ("x", "x" * 41, "bad\nname"):
            self.assertEqual(client.post("/api/v1/users/me", json={"display_name": name}).status_code, 422 if name == "x" else 400)

    def test_four_people_close_invite_leave_reopens_and_member_cannot_manage(self):
        people = [self.person() for _ in range(5)]
        owner, _ = people[0]
        team = self.team(owner)
        code = team["invite_code"]
        self.assertRegex(code, r"^[A-Z2-9]{8}$")
        for client, _ in people[1:4]:
            result = client.post("/api/v1/teams/join", json={"code": code.lower()})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIsNone(result.json()["invite_code"])
        current = owner.get("/api/v1/users/me").json()["team"]
        self.assertEqual(len(current["roster"]), 4)
        self.assertTrue(current["freshman"])
        self.assertFalse(current["invite_enabled"])
        self.assertEqual(people[4][0].post("/api/v1/teams/join", json={"code": code}).status_code, 400)
        self.assertEqual(people[1][0].post("/api/v1/teams/name", json={"name": "Bị cấm"}).status_code, 403)
        self.assertEqual(owner.post("/api/v1/teams/leave").status_code, 409)
        self.assertEqual(people[3][0].post("/api/v1/teams/leave").status_code, 200)
        self.assertTrue(owner.get("/api/v1/users/me").json()["team"]["invite_enabled"])
        self.assertEqual(people[4][0].post("/api/v1/teams/join", json={"code": code}).status_code, 200)
        self.assertEqual(people[1][0].post("/api/v1/teams", json={"name": "Đội thứ hai"}).status_code, 409)

    def test_rotate_transfer_remove_and_only_captain_can_choose_entry(self):
        a, owner = self.person()
        b, member = self.person()
        c, _ = self.person("25000003@vnu.test")
        team = self.team(a)
        self.assertEqual(b.post("/api/v1/teams/join", json={"code": team["invite_code"]}).status_code, 200)
        self.assertEqual(b.post("/api/v1/competitions/" + "a" * 32 + "/entry", headers={"Idempotency-Key": "personal-test-entry"}, json={"submission_id": "a" * 32, "benchmark_id": "b" * 32, "delegate_timeout_claims": True}).status_code, 403)
        self.assertEqual(b.post("/api/v1/competitions/" + "a" * 32 + "/withdraw", headers={"Idempotency-Key": "personal-test-withdraw"}).status_code, 403)
        rotated = a.post("/api/v1/teams/invite/rotate").json()
        self.assertNotEqual(rotated["invite_code"], team["invite_code"])
        self.assertEqual(c.post("/api/v1/teams/join", json={"code": team["invite_code"]}).status_code, 400)
        self.assertEqual(a.post("/api/v1/teams/captain", json={"user_id": member["id"]}).status_code, 200)
        self.assertEqual(a.post("/api/v1/teams/name", json={"name": "Không còn quyền"}).status_code, 403)
        self.assertEqual(b.post("/api/v1/teams/name", json={"name": "Tên mới"}).json()["name"], "Tên mới")
        self.assertEqual(b.post("/api/v1/teams/member/remove", json={"user_id": owner["id"]}).status_code, 200)
        self.assertEqual(a.get("/api/v1/auth/me").status_code, 401)

    def test_wrong_invite_guesses_commit_and_temporarily_block_the_account(self):
        a, _ = self.person()
        b, _ = self.person()
        team = self.team(a)
        extra, _ = self.login_person("26000002@vnu.test")
        for _ in range(11):
            self.assertEqual(b.post("/api/v1/teams/join", json={"code": "WRONG000"}).status_code, 400)
        self.assertEqual(b.post("/api/v1/teams/join", json={"code": team["invite_code"]}).status_code, 429)
        self.assertEqual(b.get("/api/v1/auth/me").status_code, 429)
        blocked = b.post("/api/v1/auth/login", json={"username": "26000002@vnu.test", "password": PASSWORD})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(b.get("/api/v1/competitions").status_code, 200)
        self.assertEqual(extra.post("/api/v1/auth/logout").status_code, 200)
        self.current += HOUR + 1
        self.assertEqual(b.post("/api/v1/teams/join", json={"code": team["invite_code"]}).status_code, 401)
        b, _ = self.login_person("26000002@vnu.test")
        self.assertEqual(b.post("/api/v1/teams/join", json={"code": team["invite_code"]}).status_code, 200)

    def test_two_joiners_cannot_both_take_the_fourth_seat(self):
        people = [self.person() for _ in range(5)]
        team = self.team(people[0][0])
        for _, user in people[1:3]:
            self.teams.join(user["id"], team["invite_code"])
        def join(user):
            try:
                self.teams.join(user["id"], team["invite_code"])
                return "joined"
            except Fault:
                return "full"
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(join, (user for _, user in people[3:])))
        self.assertCountEqual(outcomes, ["joined", "full"])
        self.assertEqual(len(self.teams.roster(team["id"])), 4)

    def test_deadline_locks_full_teams_admin_membership_is_audited(self):
        people = [self.person() for _ in range(5)]
        team = self.team(people[0][0])
        with self.assertRaises(Fault) as incomplete:
            self.teams.eligible(team["id"])
        self.assertEqual(incomplete.exception.code, "competition_conflict")
        for _, user in people[1:4]:
            self.teams.join(user["id"], team["invite_code"])
        self.teams.eligible(team["id"])
        choices = self.client.get("/api/v1/admin/teams")
        self.assertEqual(choices.status_code, 200)
        self.assertEqual(choices.json()["items"][0]["id"], team["id"])
        self.assertEqual(len(choices.json()["items"][0]["roster"]), 4)
        self.assertEqual(people[1][0].get("/api/v1/admin/teams").status_code, 403)
        self.assertEqual(self.other_client().get("/api/v1/admin/teams").status_code, 401)
        result = self.client.post("/api/v1/admin/team-policy", json={"deadline_ms": self.current, "expected_revision": 1})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(self.teams.current(people[0][1]["id"])["locked_ms"])
        self.assertEqual(people[1][0].post("/api/v1/teams/leave").status_code, 409)
        for action, user in (("remove", people[3][1]), ("add", people[4][1])):
            response = self.client.post(f"/api/v1/admin/teams/{team['id']}/membership", json={"action": action, "user_id": user["id"]})
            self.assertEqual(response.status_code, 200, response.text)
        actions = [r[0] for r in self.app.state.store.db.execute("SELECT action FROM audit WHERE target=?", (team["id"],))]
        self.assertIn("team.deadline_locked", actions)
        self.assertIn("team.admin_add", actions)
        self.assertEqual(self.client.post("/api/v1/admin/team-policy", json={"deadline_ms": None, "expected_revision": 1}).status_code, 409)

    def test_only_admin_promotes_staff_and_judge_guest_is_unranked(self):
        student, user = self.person()
        staff, teacher = self.person("teacher@vnu.test")
        self.assertEqual(self.client.post(f"/api/v1/admin/users/{user['id']}/judge", json={"enabled": True}).status_code, 400)
        self.assertEqual(student.post(f"/api/v1/admin/users/{teacher['id']}/judge", json={"enabled": True}).status_code, 403)
        self.assertEqual(staff.post("/api/v1/teams", json={"name": "Đội thầy", "guest": True}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/admin/users/{teacher['id']}/judge", json={"enabled": True}).status_code, 200)
        team = self.team(staff, "Đội thầy", guest=True)
        self.assertTrue(team["is_guest"])
        self.assertFalse(team["invite_enabled"])
        self.teams.eligible(team["id"])
        # Results remain readable, but the guest never receives an official rank.
        comp = self.app.state.competitions
        store=self.app.state.store
        source=store.create('submission',team['id'],dict(version='guest',runtime='cpp17'))
        check=store.create('job',team['id'],dict(kind='benchmark',status='completed'))
        with store.transaction():
            store.db.execute("INSERT INTO competitions(id,title,status,config,created_ms) VALUES('test','Guest test','completed',?,1)",
                             (json.dumps(dict(format='double_round_robin')),))
            store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,1)',
                             ('test',team['id'],source['id'],check['id'],json.dumps(dict(version='guest'))))
        scores = comp.standings("test")
        self.assertIsNone(scores["items"][0]["rank"])
        self.assertTrue(scores["items"][0]["is_guest"])

    def test_matching_is_private_opt_in_and_never_exports_email(self):
        a, owner = self.person()
        b, user = self.person()
        self.profile(b, "Người tự tìm", class_name="K71", contact="Discord: my-name")
        self.profile(a,"Đội trưởng liên hệ",language="python3",contact="Discord: captain")
        team = self.team(a)
        self.assertEqual(a.post("/api/v1/teams/recruiting", json={"enabled": True}).status_code, 200)
        self.assertEqual(self.other_client().get("/api/v1/matching").status_code, 401)
        response = b.get("/api/v1/matching")
        self.assertEqual(len(response.json()["people"]), 1)
        self.assertEqual(response.json()["teams"][0]["id"], team["id"])
        self.assertEqual(response.json()["teams"][0]["captain_name"], "Đội trưởng liên hệ")
        self.assertEqual(response.json()["teams"][0]["contact"], "Discord: captain")
        self.assertEqual(response.json()["teams"][0]["language"], "python3")
        self.assertNotIn(user["email"], response.text)
        self.assertNotIn(owner["email"], response.text)
        self.assertNotIn("username", response.text)
        self.assertEqual(b.post("/api/v1/users/me", json={"display_name": "Không tìm nữa", "looking": False}).status_code, 200)
        self.assertEqual(b.get("/api/v1/matching").json()["people"], [])

    def test_merge_preview_prioritizes_class_then_language_and_needs_confirmation(self):
        people = [self.person() for _ in range(7)]
        for i, (client, _) in enumerate(people):
            self.profile(client, "Người " + str(i), class_name="A" if i < 4 else "B", language="cpp17")
        team = self.team(people[0][0])
        self.teams.join(people[1][1]["id"], team["invite_code"])
        self.teams.set_deadline(self.current, 1, "admin")
        preview = self.client.get("/api/v1/admin/team-merges").json()
        self.assertEqual(len(preview["items"]), 1)
        self.assertEqual({p["id"] for p in preview["items"][0]["people"]}, {u["id"] for _, u in people[2:4]})
        self.assertEqual(len(self.teams.roster(team["id"])), 2)
        proposal = preview["items"][0]["id"]
        result = self.client.post("/api/v1/admin/team-merges/confirm", json={"proposal_id": proposal})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.teams.roster(team["id"])), 4)
        self.assertTrue(result.json()["locked_ms"])
        self.assertEqual(len(preview["remainder"]), 3)
        self.assertEqual(self.client.post("/api/v1/admin/team-merges/confirm", json={"proposal_id": proposal}).status_code, 409)

    def test_new_team_merge_stale_proposals_and_48_hour_window(self):
        people = [self.person() for _ in range(4)]
        for client, _ in people:
            self.profile(client)
        self.teams.set_deadline(self.current, 1, "admin")
        old = self.teams.proposals()["items"][0]["id"]
        self.profile(people[0][0], "Tên đã đổi")
        self.assertEqual(self.client.post("/api/v1/admin/team-merges/confirm", json={"proposal_id": old, "name": "Đội ghép"}).status_code, 409)
        proposal = self.teams.proposals()["items"][0]["id"]
        self.current += 49 * HOUR
        self.assertEqual(self.client.post("/api/v1/admin/team-merges/confirm", json={"proposal_id": proposal, "name": "Đội ghép"}).status_code, 409)
        self.current -= 49 * HOUR
        result = self.client.post("/api/v1/admin/team-merges/confirm", json={"proposal_id": proposal, "name": "Đội ghép"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.teams.roster(result.json()["id"])), 4)
        self.assertEqual(self.teams.proposals()["items"], [])

    def test_membership_is_unique_per_season_and_old_teams_remain(self):
        client, user = self.person()
        old = self.team(client)
        next_rules = replace(self.teams.rules, season_id="next")
        next_season = Teams(self.app.state.accounts, next_rules)
        self.assertIsNone(next_season.current(user["id"]))
        new = next_season.create(user["id"], "Đội mùa sau")
        self.assertNotEqual(old["id"], new["id"])
        previous = Teams(self.app.state.accounts, self.teams.rules)
        self.assertEqual(previous.current(user["id"])["id"], old["id"])

    def test_policy_reader_waits_for_a_concurrent_transaction_to_commit(self):
        started = threading.Event()
        def read():
            started.set()
            return self.teams.policy()
        with ThreadPoolExecutor(1) as pool:
            with self.app.state.store.transaction():
                self.app.state.store.db.execute("DELETE FROM seasons WHERE id='default'")
                future = pool.submit(read)
                self.assertTrue(started.wait(2))
                with self.assertRaises(TimeoutError):
                    future.result(timeout=.1)
                self.app.state.store.db.execute("INSERT INTO seasons(id,revision) VALUES('default',2)")
            self.assertEqual(future.result(timeout=2)["revision"], 2)


class PeopleMigration(unittest.TestCase):
    def test_v6_upgrade_preserves_password_team_sources_results_but_revokes_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp, recover_jobs=False)
            try:
                folder = Path(__file__).resolve().parents[1] / "arena_api/migrations"
                store.db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
                for path in sorted(folder.glob("*.sql")):
                    version = int(path.name.split("_")[0])
                    if version >= 7:
                        continue
                    store.db.executescript(path.read_text() + f"\nINSERT INTO schema_migrations VALUES({version});")
                from argon2 import PasswordHasher
                encoded = PasswordHasher().hash(PASSWORD)
                store.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES('old-team','Old','old','old@example.test','','[]',1)")
                store.db.execute("INSERT INTO accounts(id,username,password_hash,role,team_id,must_change,created_ms) VALUES('old-user','old-name',?,'team','old-team',0,1)", (encoded,))
                store.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (token_hash("legacy-session"), "old-user", "csrf", 9_000_000_000_000))
                store.db.commit()
                source = store.create("submission", "old-team", {"source": "sha256:" + "a" * 64})
                result = store.create("job", "old-team", {"status": "completed", "kind": "match", "report": {"result": {"winner": "red"}}})
                accounts = Accounts(store)
                with self.assertRaises(Fault):accounts.principal("legacy-session")
                user = accounts.public(accounts.one("SELECT * FROM accounts WHERE id='old-user'"))
                self.assertEqual((user["id"], user["team_id"], user["username"], user["role"]), ("old-user", "old-team", "old-name", "guest"))
                self.assertTrue(accounts.verify(accounts.one("SELECT password_hash FROM accounts WHERE id='old-user'")["password_hash"], PASSWORD))
                self.assertEqual(store.get(source["id"], "old-team", "submission"), source)
                self.assertEqual(store.get(result["id"], "old-team", "job"), result)
                self.assertEqual(accounts.one("SELECT * FROM team_members WHERE user_id='old-user'")["captain"], 1)
                self.assertEqual(store.db.execute("PRAGMA foreign_key_check").fetchall(), [])
                Accounts(store)
            finally:
                store.db.close()
