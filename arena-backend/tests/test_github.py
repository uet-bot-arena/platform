"""GitHub fixtures are synthetic; native/real-GitHub acceptance has its own script."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import threading
import tempfile
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile
from fastapi.testclient import TestClient

from arena.conf import Secrets
from arena.protocol import Fault
from arena_api.github_archive import bot_archive
from arena_api.github_client import GitHubClient, GitHubError, repository_url
from arena_api.app import create_app
from arena_api.settings import Settings, GitHubSettings
from test_web_api import APIHarness


def tar(files, extra=()):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in files.items():
            item = tarfile.TarInfo("repo-abc/" + name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        for item in extra:
            archive.addfile(item)
    return output.getvalue()


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


class FakeGitHub:
    def __init__(self):
        self.private, self.installed, self.failure = True, True, False
        self.owner, self.name, self.repo_id = "captain", "repo", 17
        self.commits, self.trees, self.archives, self.history, self.statuses = {}, {}, {}, [], []
        self.head, self.etags, self.calls = None, [], []

    def push(self, sha, pushed_ms, files=None, message="Push from test", commit_date="2099-01-01T00:00:00Z"):
        files = files or {"bot/bot.py": b"print('fixture')\n"}
        tree_sha = hashlib.sha1((sha + "root").encode()).hexdigest()
        bot_sha = hashlib.sha1((sha + "bot").encode()).hexdigest()
        self.commits[sha] = {"sha": sha, "commit": {"tree": {"sha": tree_sha}, "message": message, "committer": {"date": commit_date}}}
        self.trees[tree_sha] = {"tree": [{"path": "bot", "sha": bot_sha, "type": "tree", "mode": "040000"}]}
        self.trees[bot_sha] = {"tree": [{"path": k.removeprefix("bot/"), "sha": "1" * 40, "type": "blob", "mode": "100644"} for k in files if k.startswith("bot/")]}
        self.archives[sha] = tar(files)
        self.head = sha
        self.history.insert(0, {"after": sha, "pushed_at": iso(pushed_ms), "ref": "refs/heads/main"})

    def installation(self, owner, repo):
        if not self.installed:
            raise GitHubError(404)
        return 9

    def get(self, owner, repo, installation, suffix="", headers=None):
        self.calls.append(suffix)
        if self.failure:
            raise GitHubError(503)
        if not self.installed:
            raise GitHubError(404)
        if not suffix:
            return 200, {}, {"id": self.repo_id, "private": self.private, "full_name": f"{owner}/{repo}"}
        if suffix == "/commits/main":
            self.etags.append(headers)
            if not self.head:
                raise GitHubError(404)
            if headers and headers.get("If-None-Match") == self.head:
                return 304, {}, None
            return 200, {"etag": self.head}, self.commits[self.head]
        if suffix.startswith("/commits/"):
            return 200, {}, self.commits[suffix.split("/")[-1]]
        if suffix.startswith("/git/trees/"):
            return 200, {}, self.trees[suffix.split("/")[-1]]
        raise AssertionError(suffix)

    def tarball(self, owner, repo, installation, sha):
        return self.archives[sha]

    def activities(self, owner, repo, installation, deadline_ms=None):
        if self.failure:
            raise GitHubError(503)
        return [r for r in self.history if deadline_ms is None or r["pushed_at"] <= iso(deadline_ms)]

    def status(self, owner, repo, installation, sha, passed, description, details_url):
        self.statuses.append((sha, passed, description, details_url))


class GitHubArchive(TestCase):
    def test_tunnel_host_only_accepts_signed_post_to_exact_webhook_path(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory), github=GitHubSettings(webhook_hosts=("example.trycloudflare.com",)),
                                secrets=Secrets({"github_webhook_secret": "unit-test-only"}))
            with TestClient(create_app(settings, start_worker=False), base_url=settings.origin) as client:
                body = b'{"repository":{"id":17}}'
                headers = {"Host": "example.trycloudflare.com", "Content-Type": "application/json", "X-GitHub-Delivery": "signed-1", "X-GitHub-Event": "push",
                           "X-Hub-Signature-256": "sha256=" + hmac.new(b"unit-test-only", body, hashlib.sha256).hexdigest()}
                self.assertEqual(client.post("/api/v1/github/webhook", content=body, headers=headers).status_code, 202)
                for path in ("/", "/api/v1/auth/login", "/api/v1/github/webhook/"):
                    self.assertEqual(client.post(path, content=body, headers=headers).status_code, 403)
                self.assertEqual(client.get("/api/v1/github/webhook", headers=headers).status_code, 403)
                headers["X-Hub-Signature-256"] = "sha256=bad"
                self.assertEqual(client.post("/api/v1/github/webhook", content=body, headers=headers).status_code, 403)

    def test_flat_only_single_language_unchanged_readme_and_deterministic_zip(self):
        a = bot_archive(tar({"bot/bot.py": b"a", "README.md": b"one"}))
        b = bot_archive(tar({"bot/bot.py": b"a", "README.md": b"two"}))
        self.assertEqual(a, b)
        self.assertEqual(a[0], "python3")
        with zipfile.ZipFile(io.BytesIO(a[1])) as archive:
            self.assertEqual(archive.namelist(), ["bot.py"])
        self.assertNotEqual(a[2], bot_archive(tar({"bot/bot.py": b"b"}))[2])

    def test_rejects_unsafe_paths_links_submodules_lfs_and_bad_entries(self):
        symlink = tarfile.TarInfo("repo-abc/bot/linked.py")
        symlink.type, symlink.linkname = tarfile.SYMTYPE, "/etc/passwd"
        hardlink = tarfile.TarInfo("repo-abc/bot/hard.py")
        hardlink.type, hardlink.linkname = tarfile.LNKTYPE, "repo-abc/bot/bot.py"
        cases = [tar({"bot/bot.py": b"a"}, [symlink]), tar({"bot/bot.py": b"a"}, [hardlink]),
                 tar({"bot/bot.py": b"a", "bot/nested/x.py": b"x"}), tar({"bot/../bot.py": b"x"}),
                 tar({"bot/bot.py": b"a", "bot/bot.cpp": b"x"}), tar({"bot/helper.py": b"x"}),
                 tar({"bot/bot.py": b"version https://git-lfs.github.com/spec/v1\noid sha256:x"}),
                 b"bad tar", gzip.compress(b"0" * (24 * 1024 * 1024 + 1)), b"x" * (10 * 1024 * 1024 + 1)]
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(Fault):
                bot_archive(raw)
        for tree in ({"tree": [{"path": "bot.py", "type": "commit", "mode": "160000"}]},
                     {"tree": [], "truncated": True}, {"tree": []}):
            with self.subTest(tree=tree), self.assertRaises(Fault):
                bot_archive(tar({"bot/bot.py": b"a"}), tree)

    def test_repository_parser_blocks_ssrf_and_malformed_urls(self):
        self.assertEqual(repository_url("https://github.com/team-owner/ott2026-ab12cd"), ("team-owner", "ott2026-ab12cd"))
        for url in ("http://127.0.0.1/repo", "https://github.com.evil/a/b", "https://u:p@github.com/a/b", "https://[", "https://github.com/a/b?x=1", "https://github.com/a/b/../c", "https://github.com/a/b%2Fc", "https://github.com/a/b.git", "https://github.com/a/b#main"):
            with self.subTest(url=url), self.assertRaises(Fault):
                repository_url(url)


class GitHubTransport(TestCase):
    def test_http_worker_reuses_tls_setup_and_keeps_authorization_per_request(self):
        from urllib.request import build_opener as real_opener
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch('arena_api.github_client.build_opener',wraps=real_opener) as setup:
                def requests():
                    for i in range(12):
                        token='Bearer fixture-'+str(i%2)
                        status,_,data=GitHubClient.http('GET',f'http://127.0.0.1:{server.server_port}/',{'Authorization':token},None,1024)
                        self.assertEqual((status,data),(200,b'{}'))
                with ThreadPoolExecutor(1) as pool:pool.submit(requests).result(15)
                self.assertEqual(setup.call_count,1,'Reloaded HTTPS/CA setup for each request')
            self.assertEqual(received,['Bearer fixture-'+str(i%2) for i in range(12)])
        finally:server.shutdown();server.server_close();thread.join(5)

    def test_token_cache_serializes_each_repo_without_blocking_other_installations(self):
        barrier, calls = threading.Barrier(2), []
        def transport(method, url, headers, body, limit):
            repo = json.loads(body)["repositories"][0]
            calls.append(repo)
            barrier.wait(timeout=2)
            return 201, {}, json.dumps({"token": "fake-" + repo, "expires_at": iso(2_000_000)}).encode()
        client = GitHubClient(Secrets({}), transport=transport, clock=lambda: 1000)
        client.jwt = lambda: "fake-jwt"
        with ThreadPoolExecutor(max_workers=2) as pool:
            tokens = list(pool.map(lambda x: client.token(*x), [(1, "a"), (2, "b")]))
        self.assertEqual(tokens, ["fake-a", "fake-b"])
        self.assertEqual(client.token(1, "a"), "fake-a")
        self.assertEqual(len(calls), 2)

    def test_real_http_fake_server_tokens_redirect_limits_and_cursor_pagination(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                if self.path.startswith("/repos/a/b/tarball/"):
                    self.send_response(302)
                    self.send_header("Location", "https://codeload.github.com/a/b/tar.gz/" + "a" * 40)
                    self.end_headers()
                    return
                if self.path.startswith("/a/b/tar.gz/"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"tar")
                    return
                self.send_response(200)
                if "cursor" not in self.path:
                    self.send_header("Link", '<https://api.github.com/repos/a/b/activity?after=cursor>; rel="next"')
                    rows = [{"after": "f" * 40, "pushed_at": iso(3000), "ref": "refs/heads/main"}]
                else:
                    rows = [{"after": "a" * 40, "timestamp": iso(1000), "ref": "refs/heads/main"}]
                self.end_headers()
                self.wfile.write(json.dumps(rows).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def transport(method, url, headers, body, limit):
            p = urlsplit(url)
            return GitHubClient.http(method, f"http://127.0.0.1:{server.server_port}" + p.path + ("?" + p.query if p.query else ""), headers, body, limit)
        client = GitHubClient(Secrets({}), transport=transport)
        client.token = lambda *args: "fake-installation-token"
        self.assertEqual(client.tarball("a", "b", 1, "a" * 40), b"tar")
        self.assertIn("Authorization", requests[0][1])
        self.assertNotIn("Authorization", requests[1][1])
        self.assertEqual([r["after"] for r in client.activities("a", "b", 1, 2000)], ["a" * 40])
        client.transport = lambda *args: (302, {"Location": "https://127.0.0.1/private"}, b"")
        with self.assertRaises(GitHubError):
            client.tarball("a", "b", 1, "a" * 40)


class GitHubAPI(APIHarness):
    def test_open_competition_backfill_once_per_deadline_and_60_seconds(self):
        from types import SimpleNamespace
        self.attach();sha=self.push();self.complete(sha)
        ident=self.competition(self.clock+100_000)
        elapsed=[1000.0]
        with patch('arena_api.github.time',SimpleNamespace(monotonic=lambda:elapsed[0])), patch.object(self.g,'rows',wraps=self.g.rows) as rows:
            self.g.tick()
            self.assertIsNotNone(self.g.one('SELECT 1 FROM github_checks WHERE team_id=? AND sha=? AND purpose=?',(self.team_id,sha,ident)))
            for _ in range(9):self.g.tick()
            def backfills():
                return sum('SELECT g.team_id,g.sha FROM github_repositories' in call.args[0] for call in rows.call_args_list)
            self.assertEqual(backfills(),1)
            self.g.execute('UPDATE competitions SET submission_deadline=submission_deadline+1 WHERE id=?',(ident,))
            self.g.tick();self.assertEqual(backfills(),2)
            elapsed[0]+=59
            self.g.tick();self.assertEqual(backfills(),2)
            elapsed[0]+=1
            self.g.tick();self.assertEqual(backfills(),3)
            newest=self.push(2)
            self.assertIsNotNone(self.g.one('SELECT 1 FROM github_checks WHERE team_id=? AND sha=? AND purpose=?',(self.team_id,newest,ident)))
            self.assertEqual(backfills(),3,'Receive must create checks without waiting for backfill')

    def test_team_github_view_does_not_decode_retained_private_reports(self):
        import tracemalloc
        self.attach();sha=self.push();self.complete(sha)
        check=self.g.one('SELECT job_id FROM github_checks WHERE team_id=? AND sha=?',(self.team_id,sha))
        self.store.update(check['job_id'],report=dict(passed=True,samples=[{}]*72,artifacts=[dict(log='PRIVATE-BUILD-'+'x'*(4*1024**2))]))
        tracemalloc.start()
        try:
            value=self.g.view(self.team_id)
            _,peak=tracemalloc.get_traced_memory()
            self.assertEqual(value['commits'][0]['checks'][0]['samples'],72)
            self.assertNotIn('PRIVATE-BUILD',json.dumps(value))
            self.assertLess(peak,2*1024**2,'GitHub status decoded a private build report')
        finally:tracemalloc.stop()

    def setUp(self):
        super().setUp()
        self.g, self.store = self.app.state.github, self.app.state.store
        self.fake = FakeGitHub()
        self.g.client = self.fake
        self.clock = 1_800_000_000_000
        self.g.clock = lambda: self.clock
        self.team, issued = self.create_team()
        self.user, self.team_id = issued["account"]["id"], issued["account"]["team_id"]
        self.code = self.app.state.accounts.team(self.team_id)["repo_code"]
        self.url = "https://github.com/captain/ott2026-" + self.code

    def attach(self):
        result = self.team.post("/api/v1/teams/me/github", json={"url": self.url})
        self.assertEqual(result.status_code, 201, result.text)

    def push(self, n=1, when=None, files=None):
        sha = f"{n:040x}"
        self.fake.push(sha, self.clock if when is None else when, files)
        self.g.execute("UPDATE github_repositories SET next_poll_ms=0")
        self.clock += 1
        self.g.tick()
        return sha

    def complete(self, sha, purpose="practice", error=None):
        check = self.g.one("SELECT * FROM github_checks WHERE team_id=? AND sha=? AND purpose=?", (self.team_id, sha, purpose))
        self.assertIsNotNone(check)
        job = self.store.get(check["job_id"])
        self.app.state.service.pending.remove(job["id"])
        if error:
            self.store.update(job["id"], status="failed", error={"code": error, "detail": error}, report=None)
        else:
            source = self.store.get(job["submission_ids"][0])
            if purpose == "practice":
                report = {"passed": True, "samples": [{}] * 72, "artifacts":[{"artifact_sha256":"4"*64}]}
            else:
                config = self.app.state.competitions.get(purpose)["config"]
                artifact = {"artifact_sha256": "4" * 64, "source": source["source"], "runtime_id": config["runtime_id"],
                            "flags": ["pypy3", "-I", "-S"], "runtime": "python3", "sdk_sha256": config["sdk_sha256"]}
                report = {"passed": True, "samples": [{}] * 72, "turn_wall_ms": {"samples": 72}, "profile": job["profile"],
                          "runtime_id": config["runtime_id"], "engine_sha256": config["engine_sha256"], "suite_id": config["suite_id"],
                          "suite_sha256": config["suite_sha256"], "artifacts": [artifact]}
            platform=(job.get('requires') or {}).get('platform') or 'linux'
            folder=self.app.state.service.artifacts/platform
            folder.mkdir(parents=True,exist_ok=True)
            (folder/('4'*64)).write_bytes(b'synthetic-only-binary')
            self.store.update(job['id'],status='completed',report=report,runtime={'platform':platform})
        self.g.tick()

    def competition(self, deadline):
        comp = self.app.state.competitions
        with patch("arena.service.Service.runtime_for", return_value="sha256:" + "1" * 64):
            row = comp.create("unit-test", {"title": "GitHub deadline test", "max_teams": 16, "match_ms": 60000, "submission_deadline": deadline}, "github-create-competition")
        comp.transition("unit-test", row["id"], "open", "github-open-competition")
        return row["id"]

    def test_idle_intake_cost_is_bounded_when_finished_history_grows(self):
        self.attach();sha=self.push();self.complete(sha)
        old=self.g.one('SELECT * FROM github_commits WHERE team_id=? AND sha=?',(self.team_id,sha))
        check=self.g.one('SELECT * FROM github_checks WHERE team_id=? AND sha=?',(self.team_id,sha))
        with self.store.transaction():
            for number in range(2,202):
                previous=f'{number:040x}'
                self.store.db.execute("INSERT INTO github_commits(team_id,sha,received_ms,submission_id,bot_hash,state) VALUES(?,?,?,?,?,'pass')",(self.team_id,previous,self.clock-number,old['submission_id'],old['bot_hash']))
                self.store.db.execute("INSERT INTO github_checks(team_id,sha,purpose,job_id,state,status_sent) VALUES(?,?,'practice',?,'pass',1)",(self.team_id,previous,check['job_id']))
        statements=[];self.store.db.set_trace_callback(statements.append)
        try:self.g.tick()
        finally:self.store.db.set_trace_callback(None)
        self.assertLess(len(statements),50,'Idle intake queried each already reported historical commit')
        self.assertEqual(len(self.fake.statuses),1)

    def test_cross_team_duplicate_warns_admin_without_penalty_or_student_disclosure(self):
        self.attach();sha=self.push()
        other,issued=self.create_team('Other','other')
        other_id=issued['account']['team_id']
        self.fake.repo_id=18
        self.g.attach('test-admin',other_id,'https://github.com/captain/other',admin=True)
        self.g.execute('UPDATE github_repositories SET next_poll_ms=0')
        self.g.tick()
        response=self.client.get('/api/v1/admin/github/duplicates')
        self.assertEqual(response.status_code,200,response.text)
        items=response.json()['items'];self.assertEqual(len(items),1)
        self.assertEqual({t['id'] for t in items[0]['teams']},{self.team_id,other_id})
        self.assertEqual(items[0]['team_count'],2)
        self.assertEqual(other.get('/api/v1/admin/github/duplicates').status_code,403)
        audit=self.g.rows("SELECT * FROM audit WHERE action='github.duplicate_bot'")
        self.assertEqual(len(audit),1)
        self.g.tick()
        self.assertEqual(len(self.g.rows("SELECT * FROM audit WHERE action='github.duplicate_bot'")),1)
        self.assertNotEqual(self.g.one('SELECT state FROM github_commits WHERE team_id=? AND sha=?',(other_id,sha))['state'],'fail')

    def test_attach_name_code_private_installation_once_manual_and_code_immutable(self):
        for url in ("http://127.0.0.1/x", "https://github.com/captain/ott2026-other"):
            self.assertEqual(self.team.post("/api/v1/teams/me/github", json={"url": url}).status_code, 400)
        self.fake.installed = False
        self.assertIn("App", self.team.post("/api/v1/teams/me/github", json={"url": self.url}).text)
        self.fake.installed, self.fake.private = True, False
        self.assertIn("Private", self.team.post("/api/v1/teams/me/github", json={"url": self.url}).text)
        self.fake.private = True
        self.attach()
        self.assertEqual(self.team.post("/api/v1/teams/me/github", json={"url": self.url}).status_code, 409)
        with self.assertRaises(sqlite3.IntegrityError):
            self.g.execute("UPDATE teams SET repo_code='aaaaaa' WHERE id=?", (self.team_id,))
        other, issued = self.create_team("Other team", "other-team")
        duplicate = self.client.post(f"/api/v1/admin/teams/{issued['account']['team_id']}/github", json={"url": "https://github.com/uet-bot-arena/bot-thu"})
        self.assertEqual(duplicate.status_code, 409)
        self.fake.repo_id = 18
        manual = self.client.post(f"/api/v1/admin/teams/{issued['account']['team_id']}/github", json={"url": "https://github.com/uet-bot-arena/bot-thu"})
        self.assertEqual(manual.status_code, 201, manual.text)
        self.assertTrue(self.g.one("SELECT 1 FROM audit WHERE action='github.admin_attached'"))

    def test_poll_etag_pin_unchanged_bot_rate_limit_and_final_status(self):
        self.attach()
        sha = self.push()
        self.assertEqual(self.store.list("submission", self.team_id)[0]["channel_ref"], sha)
        self.assertFalse(self.fake.statuses)
        self.complete(sha)
        self.assertEqual([(s[0], s[1]) for s in self.fake.statuses], [(sha, True)])
        self.g.execute("UPDATE github_repositories SET next_poll_ms=0")
        self.g.tick()
        self.assertEqual(self.fake.etags[-1], {"If-None-Match": sha})
        next_sha = self.push(2, files={"bot/bot.py": b"print('fixture')\n", "README.md": b"new"})
        self.assertEqual(len(self.store.list("submission", self.team_id)), 1)
        self.assertEqual(self.g.view(self.team_id)["commits"][0]["sha"], next_sha)
        self.app.state.service.max_submissions_per_hour = 1
        third = self.push(3, files={"bot/bot.py": b"new bot"})
        self.assertEqual(self.g.one("SELECT state FROM github_commits WHERE sha=?", (third,))["state"], "detected")
        self.assertEqual(self.store.list("submission", self.team_id)[0]["channel_ref"], sha)

    def test_webhook_signature_duplicate_fake_commit_lost_webhook_and_tunnel_host(self):
        self.attach()
        self.g.settings = replace(self.settings, secrets=Secrets({"github_webhook_secret": "unit-test-only"}))
        body = json.dumps({"repository": {"id": 17}, "after": "f" * 40, "ref": "refs/heads/main"}).encode()
        good = "sha256=" + hmac.new(b"unit-test-only", body, hashlib.sha256).hexdigest()
        for signature in ("", "sha256=wrong"):
            with self.assertRaises(Fault):
                self.g.webhook(body, signature, "delivery-1", "push")
        self.assertFalse(self.g.webhook(body, good, "delivery-1", "push")["duplicate"])
        self.assertTrue(self.g.webhook(body, good, "delivery-1", "push")["duplicate"])
        self.assertFalse(self.g.rows("SELECT * FROM github_commits"))
        sha = self.push()
        self.assertEqual(self.g.view(self.team_id)["commits"][0]["sha"], sha)
        self.assertEqual(self.team.get("/api/v1/teams/me/github", headers={"Host": "evil.example"}).status_code, 403)

    def test_infrastructure_error_retries_never_marks_student_failure(self):
        self.attach()
        sha = self.push()
        self.complete(sha, error="provenance_mismatch")
        checks = self.g.view(self.team_id)["commits"][0]["checks"]
        self.assertEqual(checks[0]["state"], "checking")
        self.assertEqual(self.fake.statuses, [])
        self.complete(sha, error="illegal_action")
        self.assertEqual(self.fake.statuses[-1][1], False)

    def test_deadline_uses_push_time_hub_was_down_force_push_after_and_fallback_to_pass(self):
        self.attach()
        deadline = self.clock + 5000
        ident = self.competition(deadline)
        good, bad, late = (f"{n:040x}" for n in (1, 2, 3))
        self.fake.push(good, deadline - 2000, {"bot/bot.py": b"good"})
        self.fake.push(bad, deadline, {"bot/bot.py": b"bad"})
        self.fake.push(late, deadline + 1000, {"bot/bot.py": b"late"})
        self.clock = deadline + 2000  # No hub observations before the deadline.
        self.g.tick()
        choice = self.g.one("SELECT * FROM github_deadlines WHERE competition_id=?", (ident,))
        self.assertEqual([r["sha"] for r in json.loads(choice["candidates"])], [bad, good])
        self.g.tick()
        self.g.tick()
        self.complete(bad, ident, error="build_error")
        self.g.tick()
        self.g.tick()
        self.complete(good, ident)
        choice = self.g.one("SELECT * FROM github_deadlines WHERE competition_id=?", (ident,))
        self.assertEqual((choice["state"], choice["selected_sha"]), ("locked", good))
        self.assertEqual(choice["source"], "github_activity")
        self.fake.push("f" * 40, self.clock + 10)
        self.g.tick()
        self.assertEqual(self.g.one("SELECT selected_sha FROM github_deadlines WHERE competition_id=?", (ident,))["selected_sha"], good)
        self.assertEqual(self.g.one("SELECT COUNT(*) AS n FROM competition_entries WHERE competition_id=?", (ident,))["n"], 1)

    def test_deadline_api_down_requires_explicit_approval_and_revoked_has_no_entry(self):
        self.attach()
        sha = self.push()
        self.complete(sha)
        ident = self.competition(self.clock + 1000)
        self.clock += 2000
        self.fake.failure = True
        self.g.deadlines()
        choice = self.g.one("SELECT * FROM github_deadlines WHERE competition_id=?", (ident,))
        self.assertEqual((choice["state"], choice["source"]), ("needs_approval", "hub_observed"))
        self.assertIn("[HỎI]", choice["error"])
        self.assertFalse(self.g.rows("SELECT * FROM competition_entries"))
        self.g.approve_fallback(ident, self.team_id, "explicit-admin-approval")
        self.assertEqual(self.g.one("SELECT state FROM github_deadlines WHERE competition_id=?", (ident,))["state"], "checking")

    def test_public_or_revoked_repo_warns_and_invalid_archive_fails_without_job(self):
        self.attach()
        sha = self.push(files={"bot/bot.py": b"a", "bot/bot.c": b"b"})
        self.assertEqual(self.g.one("SELECT state FROM github_commits WHERE sha=?", (sha,))["state"], "fail")
        self.assertEqual(self.fake.statuses[-1][1], False)
        self.assertEqual(self.store.list("job", self.team_id), [])
        self.fake.private = False
        self.g.execute("UPDATE github_repositories SET next_poll_ms=0")
        self.g.tick()
        self.assertEqual(self.g.view(self.team_id)["repo"]["state"], "public")
        self.fake.private, self.fake.installed = True, False
        self.g.execute("UPDATE github_repositories SET next_poll_ms=0")
        self.g.tick()
        self.assertEqual(self.g.view(self.team_id)["repo"]["state"], "revoked")

    def test_student_zip_is_closed_and_admin_import_retains_validation(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr("bot.py", "pass")
        headers = {"Content-Type": "application/zip", "Idempotency-Key": "fixture-import", "X-Submission-Version": "v1.0.0", "X-Arena-Runtime": "python3"}
        result = self.team.post("/api/v1/submissions", content=raw.getvalue(), headers=headers)
        self.assertEqual(result.status_code, 403)
        self.assertIn("GitHub", result.text)
        self.assertEqual(self.team.post(f"/api/v1/admin/teams/{self.team_id}/submissions", content=raw.getvalue(), headers=headers).status_code, 403)
        self.assertEqual(self.upload(self.team, content=raw.getvalue(), headers=headers).status_code, 201)

    def test_repo_code_collision_is_retried_and_non_captain_cannot_attach(self):
        self.assertRegex(self.code, r"^[a-z0-9]{6}$")
        fresh = "fffffe" if self.code != "fffffe" else "fffffd"
        values = iter([bytes.fromhex(self.code), bytes.fromhex(fresh)])
        self.store.db.create_function("randomblob", 1, lambda n: next(values))
        _, issued = self.create_team("Collision team", "collision-team")
        self.assertEqual(self.app.state.accounts.team(issued["account"]["team_id"])["repo_code"], fresh)
        self.g.execute("UPDATE team_members SET captain=0 WHERE user_id=?", (self.user,))
        self.assertEqual(self.team.post("/api/v1/teams/me/github", json={"url": self.url}).status_code, 403)

    def test_deadline_no_pass_checks_at_most_five_earlier_pushes(self):
        self.attach()
        deadline = self.clock + 1000
        ident = self.competition(deadline)
        for n in range(1, 9):
            self.fake.push(f"{n:040x}", deadline - 9 + n, {"bot/helper.py": b"missing entry"})
        self.clock = deadline + 1
        for _ in range(12):
            self.g.tick()
        choice = self.g.one("SELECT * FROM github_deadlines WHERE competition_id=?", (ident,))
        self.assertEqual(choice["state"], "no_entry")
        self.assertEqual(len(json.loads(choice["candidates"])), 6)
        self.assertIsNone(self.g.one("SELECT 1 FROM github_commits WHERE sha=?", (f"{2:040x}",)))
        self.assertFalse(self.g.rows("SELECT * FROM competition_entries"))

    def test_app_revoked_at_deadline_never_uses_an_old_pass(self):
        self.attach()
        sha = self.push()
        self.complete(sha)
        ident = self.competition(self.clock + 1000)
        self.fake.installed = False
        self.clock += 2000
        self.g.deadlines()
        self.assertEqual(self.g.one("SELECT state FROM github_deadlines WHERE competition_id=?", (ident,))["state"], "no_entry")
        self.assertFalse(self.g.rows("SELECT * FROM competition_entries"))

    def test_manual_benchmark_cannot_replace_automatic_check(self):
        self.attach()
        first = self.push()
        automatic = self.g.one("SELECT job_id FROM github_checks WHERE sha=?", (first,))["job_id"]
        submission = self.store.list("submission", self.team_id)[0]
        manual = self.team.post("/api/v1/benchmarks", json={"submission_id": submission["id"], "match_ms": 60000}, headers={"Idempotency-Key": "manual-benchmark-test"})
        self.assertEqual(manual.status_code, 202, manual.text)
        self.assertNotEqual(manual.json()["id"], automatic)
        self.assertEqual(self.store.get(automatic)["status"], "queued")
        self.push(2, files={"bot/bot.py": b"next version"})
        self.assertEqual(self.store.get(automatic)["status"], "aborted")
        self.assertEqual(self.store.get(manual.json()["id"])["status"], "queued")

    def test_no_entry_at_deadline_removes_a_legacy_manual_entry(self):
        self.attach()
        ident = self.competition(None)
        sha = self.push()
        self.complete(sha, ident)
        row = self.g.one("SELECT * FROM github_commits WHERE sha=?", (sha,))
        check = self.g.one("SELECT * FROM github_checks WHERE sha=?", (sha,))
        self.app.state.competitions.enter(self.user, self.team_id, ident,
            {"submission_id": row["submission_id"], "benchmark_id": check["job_id"]}, "legacy-entry-test")
        self.g.execute("UPDATE competitions SET submission_deadline=? WHERE id=?", (self.clock - 1, ident))
        self.fake.installed = False
        self.g.deadlines()
        self.assertFalse(self.g.rows("SELECT * FROM competition_entries"))

    def test_preview_and_fallback_keep_force_push_observation_order(self):
        self.attach()
        deadline = self.clock + 10000
        ident = self.competition(deadline)
        first = self.push()
        self.complete(first, ident)
        second = self.push(2, files={"bot/bot.py": b"second"})
        self.complete(second, ident)
        self.fake.head = first
        self.fake.history.insert(0, {"after": first, "pushed_at": iso(self.clock), "ref": "refs/heads/main"})
        self.clock += 1
        self.g.execute("UPDATE github_repositories SET next_poll_ms=0")
        self.g.tick()
        self.assertEqual(self.g.view(self.team_id)["previews"][0]["sha"], first)
        self.clock = deadline + 1000
        self.fake.failure = True
        self.g.deadlines()
        choice = self.g.one("SELECT * FROM github_deadlines WHERE competition_id=?", (ident,))
        self.assertEqual(choice["state"], "needs_approval")
        self.assertEqual([r["sha"] for r in json.loads(choice["candidates"])], [first, second])

    def test_retention_old_pass_still_enters_ladder_after_25_failed_pushes(self):
        from arena_api.elo_rounds import RoundBook
        from arena_api.retention import Retention
        self.attach()
        ident=self.competition(self.clock+1_000_000)
        good=self.push()
        self.complete(good,ident)
        before=RoundBook(self.app.state.competitions).latest_passes(ident,self.clock,self.team_id)
        self.assertEqual(before[self.team_id]['github_sha'],good)
        for n in range(2,27):
            sha=self.push(n,files={'bot/bot.py':f'# invalid fixture {n}'.encode()})
            self.complete(sha,ident,error='illegal_action')
        Retention(self.app.state.service).run()
        after=RoundBook(self.app.state.competitions).latest_passes(ident,self.clock,self.team_id)
        self.assertEqual(before,after)
        check=self.g.one('SELECT job_id FROM github_checks WHERE sha=? AND purpose=?',(good,ident))
        self.assertIsNone(self.store.get(check['job_id'])['report'])
        self.assertEqual(self.store.get(check['job_id'])['qualification']['sample_count'],72)

    def test_pruned_source_is_refetched_by_exact_sha_without_adding_a_cached_version(self):
        from arena_api.retention import Retention
        self.attach()
        first=self.push();self.complete(first)
        old=self.push(2,files={'bot/bot.py':b'# old failed bot'})
        self.complete(old,error='illegal_action')
        source=self.store.get(self.g.one('SELECT submission_id FROM github_commits WHERE sha=?',(old,))['submission_id'])
        self.push(3,files={'bot/bot.py':b'# latest bot'})
        Retention(self.app.state.service).run()
        folder=Path(source['source_path'])
        self.assertFalse(folder.exists());self.assertFalse(folder.with_suffix('.zip').exists())
        raw=self.app.state.service.submission_archive(source)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),source['source']['zip_sha256'])
        self.assertFalse(folder.with_suffix('.zip').exists())
        self.fake.commits[old]['sha']='f'*40
        with self.assertRaises(Fault) as raised:self.app.state.service.submission_archive(source)
        self.assertEqual(raised.exception.code,'provenance_mismatch')

        self.fake.commits[old]['sha']=old
        self.push(4,files={'bot/bot.py':b'# old failed bot'})
        self.app.state.service.submission_archive(source)
        self.assertTrue(folder.with_suffix('.zip').exists())
        self.assertFalse(self.store.get(source['id'])['cache_pruned'])

    def test_deadline_rechecks_a_pass_whose_compiled_binary_was_pruned(self):
        self.attach();ident=self.competition(self.clock+10_000)
        sha=self.push();self.complete(sha,ident)
        old=self.g.one('SELECT job_id FROM github_checks WHERE sha=? AND purpose=?',(sha,ident))['job_id']
        path=self.app.state.service.artifact_path({},'windows','4'*64)
        self.assertTrue(path.resolve().is_relative_to(self.store.root.resolve()))
        path.unlink()
        self.clock+=20_000;self.g.tick()
        self.assertEqual(self.g.one('SELECT state FROM github_deadlines WHERE competition_id=?',(ident,))['state'],'checking')
        self.g.tick()
        new=self.g.one('SELECT job_id FROM github_checks WHERE sha=? AND purpose=?',(sha,ident))['job_id']
        self.assertNotEqual(old,new)
        self.complete(sha,ident)
        self.assertEqual(self.g.one('SELECT state FROM github_deadlines WHERE competition_id=?',(ident,))['state'],'locked')

    def test_windows_filename_collisions_and_devices_are_student_errors(self):
        self.attach()
        for n, name in enumerate(("BOT.py", "CON.py", "COM¹.py", "bad|name.py", "あ" * 100 + ".py"), 1):
            with self.subTest(name=name):
                sha = self.push(n, files={"bot/bot.py": b"pass", "bot/" + name: b"pass"})
                row = self.g.one("SELECT * FROM github_commits WHERE sha=?", (sha,))
                self.assertEqual(row["state"], "fail")
                self.assertIn("portable", row["error"])
        self.assertFalse(self.store.list("job", self.team_id))
