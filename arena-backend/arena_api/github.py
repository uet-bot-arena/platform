"""Durable GitHub intake. Poll/API owns truth; webhooks only wake the poller."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import logging
import re
import sqlite3
import threading

from arena.protocol import Fault, strict_json
from arena.adjudication import BOT_FAULTS
from arena.service import TERMINAL
from arena_api.accounts import now_ms
from arena_api.competitions import INFRASTRUCTURE
from arena_api.github_archive import bot_archive
from arena_api.github_client import GitHubClient, GitHubError, SHA, repository_url, unix_ms

INSTALL_URL = "https://github.com/apps/uet-bot-arena/installations/new"
BOT_ERRORS = {"invalid_archive", "build_failed", "build_error", "build_timeout", "unsupported_runtime", "unsupported_protocol", "illegal_action"} | BOT_FAULTS


class GitHub:
    def __init__(self, store, accounts, teams, service, competitions, settings, client=None, clock=now_ms, concurrent=False):
        self.store, self.db, self.a, self.teams = store, store.db, accounts, teams
        self.service, self.competitions, self.settings, self.clock = service, competitions, settings, clock
        self.client = client or GitHubClient(settings.secrets, settings.github.max_tarball_bytes)
        self.stop, self.wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.loop, name="github-intake", daemon=True)
        self.tick_lock = threading.Lock()
        # HTTP waits overlap; bounded intake keeps worst-case archive RAM below 4 x 64 MiB.
        self.pollers = ThreadPoolExecutor(max_workers=32, thread_name_prefix="github-poll") if concurrent else None
        self.receivers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="github-source") if concurrent else None

    def close_pools(self):
        for pool in (self.pollers, self.receivers):
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)

    def rows(self, sql, args=()):
        with self.store.lock:
            return self.a.rows(sql, args)

    def one(self, sql, args=()):
        values = self.rows(sql, args)
        return values[0] if values else None

    def execute(self, sql, args=()):
        with self.store.transaction():
            self.db.execute(sql, args)

    def attach(self, user, team, url, admin=False):
        owner, name = repository_url(url)
        with self.store.lock:
            if not admin and self.teams.captain_team(user)["id"] != team:
                raise Fault("forbidden", "Chỉ đội trưởng gắn repo của đội")
            value = self.a.team(team)
            if value["archived_ms"]:
                raise Fault("forbidden", "Đội đã lưu trữ")
            if not admin and name != self.settings.github.repo_prefix + value["repo_code"]:
                raise Fault("invalid_request", "Tên repo phải là " + self.settings.github.repo_prefix + value["repo_code"])
            if self.one("SELECT 1 FROM github_repositories WHERE team_id=?", (team,)):
                raise Fault("account_conflict", "Đội đã gắn repo; cần BTC xử lý trường hợp đặc biệt")
        try:
            installation = self.client.installation(owner, name)
            data = self.client.get(owner, name, installation)[2]
        except GitHubError as exc:
            if exc.status in (401, 403, 404):
                raise Fault("invalid_request", "App chưa được cấp quyền đọc repo này. Cài hoặc chọn lại repo tại " + INSTALL_URL) from None
            raise Fault("github_unavailable", "GitHub tạm thời chưa trả lời; thử lại sau") from None
        if data.get("private") is not True:
            raise Fault("invalid_request", "Repo phải đặt Private trước khi gắn. Link cài App: " + INSTALL_URL)
        if data.get("full_name", "").lower() != f"{owner}/{name}".lower() or type(data.get("id")) is not int:
            raise Fault("invalid_request", "Repo đã đổi tên; dán lại link chính xác")
        try:
            with self.store.transaction():
                # Membership/captain can change during the API call.
                if not admin and self.teams.captain_team(user)["id"] != team:
                    raise Fault("forbidden", "Đội trưởng đã thay đổi; tải lại trang")
                self.db.execute("INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms) VALUES(?,?,?,?,?,?)",
                                (team, data["id"], owner, name, installation, self.clock()))
                self.a.audit(user, "github.admin_attached" if admin else "github.attached", team)
        except sqlite3.IntegrityError:
            raise Fault("account_conflict", "Đội hoặc repo này đã có kết nối; mỗi repo chỉ gắn một đội") from None
        self.wake.set()
        return self.view(team)

    def webhook(self, body, signature, delivery, event):
        if not self.settings.secrets.has("github_webhook_secret"):
            raise Fault("github_unavailable", "Webhook chưa được cấu hình")
        expected = "sha256=" + hmac.new(self.settings.secrets.get("github_webhook_secret").encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), signature.encode()):
            raise Fault("forbidden", "Chữ ký webhook không hợp lệ")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery):
            raise Fault("invalid_request", "Thiếu delivery ID hợp lệ")
        payload = strict_json(body)
        if not isinstance(payload, dict):
            raise Fault("invalid_request", "Webhook cần object JSON")
        with self.store.transaction():
            old = self.one("SELECT 1 FROM github_deliveries WHERE id=?", (delivery,))
            if old:
                return {"accepted": True, "duplicate": True}
            self.db.execute("INSERT INTO github_deliveries VALUES(?,?)", (delivery, self.clock()))
            repo = payload.get("repository")
            repo_id = repo.get("id") if isinstance(repo, dict) else None
            if event == "push" and type(repo_id) is int:
                # Ignore ref, after, commits and timestamps from the signed payload too.
                self.db.execute("UPDATE github_repositories SET next_poll_ms=0 WHERE repo_id=?", (repo_id,))
            self.db.execute("DELETE FROM github_deliveries WHERE received_ms<?", (self.clock() - 7 * 86_400_000,))
        self.wake.set()
        return {"accepted": True, "duplicate": False}

    def detect(self, repo, sha, pushed_ms=None, message="", *, observed=True):
        if not SHA.fullmatch(sha):
            raise GitHubError(502)
        with self.store.transaction():
            self.execute("INSERT INTO github_commits(team_id,sha,message,pushed_ms,received_ms) VALUES(?,?,?,?,?) "
                         "ON CONFLICT(team_id,sha) DO UPDATE SET pushed_ms=COALESCE(github_commits.pushed_ms,excluded.pushed_ms)",
                         (repo["team_id"], sha, message.splitlines()[0][:200] if message else "", pushed_ms, self.clock()))
            if observed:
                self.db.execute("INSERT INTO github_observations(team_id,sha,observed_ms,pushed_ms) VALUES(?,?,?,?)",
                                (repo["team_id"], sha, self.clock(), pushed_ms))

    def observations(self, team, deadline):
        return self.rows("SELECT sha,pushed_ms FROM (SELECT sha,pushed_ms,id,ROW_NUMBER() OVER(PARTITION BY sha ORDER BY id DESC) AS latest FROM github_observations WHERE team_id=? AND observed_ms<=?) WHERE latest=1 ORDER BY id DESC LIMIT 6", (team, deadline))

    def poll(self, repo):
        team, owner, name = repo["team_id"], repo["owner"], repo["name"]
        next_poll = self.clock() + self.settings.github.poll_seconds * 1000
        try:
            installation = repo["installation_id"]
            try:
                metadata = self.client.get(owner, name, installation)[2]
            except GitHubError as exc:
                if exc.status not in (401, 403, 404):
                    raise
                # Reinstall can change installation ID. Normal polls use its cached token;
                # they don't spend the App-level JWT quota on an installation lookup.
                installation = self.client.installation(owner, name)
                metadata = self.client.get(owner, name, installation)[2]
            if metadata.get("private") is not True or metadata.get("id") != repo["repo_id"]:
                self.execute("UPDATE github_repositories SET state='public',error=?,next_poll_ms=? WHERE team_id=?",
                             ("Repo cần Private và đúng repo đã gắn; chưa thể nhận bài", next_poll, team))
                return
            code, headers, commit = self.client.get(owner, name, installation, "/commits/main",
                                                     {"If-None-Match": repo["etag"]} if repo["etag"] else {})
            if code != 304:
                sha = commit["sha"]
                pushed = None
                try:
                    activities = self.client.activities(owner, name, installation)
                    pushed = next((unix_ms(r["pushed_at"]) for r in activities if r["after"] == sha), None)
                except GitHubError:
                    pass  # Deadline resolution independently requires authoritative activity.
                self.detect(repo, sha, pushed, commit["commit"]["message"])
            self.execute("UPDATE github_repositories SET installation_id=?,etag=COALESCE(?,etag),last_poll_ms=?,next_poll_ms=?,state='connected',error=NULL WHERE team_id=?",
                         (installation, headers.get("etag"), self.clock(), next_poll, team))
        except GitHubError as exc:
            revoked = exc.status in (401, 403, 404)
            self.execute("UPDATE github_repositories SET state=?,error=?,next_poll_ms=? WHERE team_id=?",
                         ("revoked" if revoked else "retry", "Không đọc được repo; kiểm tra Private và quyền App" if revoked else "GitHub đang bận; hub sẽ đọc lại",
                          next_poll, team))

    def purposes(self, team, sha):
        opened = [r["id"] for r in self.rows("SELECT id FROM competitions WHERE status='open' AND archived_ms IS NULL AND (submission_deadline IS NULL OR submission_deadline>?)", (self.clock(),))]
        pinned = [r["competition_id"] for r in self.rows("SELECT competition_id FROM github_deadlines d WHERE team_id=? AND state='checking' AND EXISTS(SELECT 1 FROM json_each(d.candidates) x WHERE json_extract(x.value,'$.sha')=?)", (team, sha))]
        return list(dict.fromkeys(opened + pinned)) or ["practice"]

    def receive(self, row):
        repo = self.one("SELECT * FROM github_repositories WHERE team_id=?", (row["team_id"],))
        if repo["state"] != "connected":
            return
        try:
            commit = self.client.get(repo["owner"], repo["name"], repo["installation_id"], "/commits/" + row["sha"])[2]
            root = self.client.get(repo["owner"], repo["name"], repo["installation_id"], "/git/trees/" + commit["commit"]["tree"]["sha"])[2]
            bot = next((x for x in root["tree"] if x["path"] == "bot"), None)
            if root.get("truncated") or not bot or bot.get("type") != "tree" or bot.get("mode") != "040000":
                raise Fault("invalid_archive", "Repo cần thư mục bot/ chứa mã nguồn, không phải submodule")
            tree = self.client.get(repo["owner"], repo["name"], repo["installation_id"], "/git/trees/" + bot["sha"])[2]
            runtime, raw, bot_hash = bot_archive(self.client.tarball(repo["owner"], repo["name"], repo["installation_id"], row["sha"]), tree)
            previous = self.one("SELECT * FROM github_commits WHERE team_id=? AND bot_hash=? AND submission_id IS NOT NULL AND sha<>? ORDER BY received_ms DESC LIMIT 1",
                                (row["team_id"], bot_hash, row["sha"]))
            if previous:
                submission = self.store.get(previous["submission_id"])
            else:
                submission = self.service.submit(row["team_id"], row["sha"][:7], runtime, raw, "github-" + row["sha"],
                    channel="github", channel_ref=row["sha"], pushed_ms=row["pushed_ms"], message=commit["commit"]["message"].splitlines()[0][:200])
            self.execute("UPDATE github_commits SET message=?,bot_hash=?,submission_id=?,state='checking',error=NULL WHERE team_id=? AND sha=?",
                         (commit["commit"]["message"].splitlines()[0][:200], bot_hash, submission["id"], row["team_id"], row["sha"]))
            for purpose in self.purposes(row["team_id"], row["sha"]):
                self.ensure_check(row["team_id"], row["sha"], purpose)
        except GitHubError as exc:
            if exc.status == 413:
                self.execute("UPDATE github_commits SET state='fail',error='Repo vượt giới hạn tải nguồn' WHERE team_id=? AND sha=?", (row["team_id"], row["sha"]))
            else:
                self.execute("UPDATE github_commits SET retry_ms=? WHERE team_id=? AND sha=?", (self.clock() + 60_000, row["team_id"], row["sha"]))
        except Fault as exc:
            if exc.code in ("rate_limited", "quota_exceeded", "worker_unavailable"):
                self.execute("UPDATE github_commits SET retry_ms=? WHERE team_id=? AND sha=?", (self.clock() + 60_000, row["team_id"], row["sha"]))
            elif exc.code in BOT_ERRORS:
                self.execute("UPDATE github_commits SET state='fail',error=? WHERE team_id=? AND sha=?", (exc.detail[:500], row["team_id"], row["sha"]))
            else:
                raise

    def ensure_check(self, team, sha, purpose):
        self.execute("INSERT OR IGNORE INTO github_checks(team_id,sha,purpose) VALUES(?,?,?)", (team, sha, purpose))

    def run_check(self, check):
        team, sha, purpose = check["team_id"], check["sha"], check["purpose"]
        commit = self.one("SELECT * FROM github_commits WHERE team_id=? AND sha=?", (team, sha))
        if not commit["submission_id"]:
            return
        job = self.store.get(check["job_id"], team, "job") if check["job_id"] else None
        if job and job["status"] not in TERMINAL:
            return
        if job and job["status"] == "completed":
            passed = (job.get("report") or {}).get("passed") is True
            self.execute("UPDATE github_checks SET state=? WHERE team_id=? AND sha=? AND purpose=?", ("pass" if passed else "fail", team, sha, purpose))
            self.execute("UPDATE github_commits SET state=? WHERE team_id=? AND sha=?", ("pass" if passed else "fail", team, sha))
            return
        error = (job or {}).get("error") or {}
        if job and error.get("code") in BOT_ERRORS:
            self.execute("UPDATE github_checks SET state='fail' WHERE team_id=? AND sha=? AND purpose=?", (team, sha, purpose))
            self.execute("UPDATE github_commits SET state='fail',error=? WHERE team_id=? AND sha=?", (error.get("detail", "Bot không qua kiểm tra")[:500], team, sha))
            return
        if error.get("code") == "superseded":
            # A deadline may later request this exact SHA explicitly.
            self.execute("UPDATE github_checks SET state='replaced' WHERE team_id=? AND sha=? AND purpose=?", (team, sha, purpose))
            return
        try:
            # Reuse a completed check for unchanged bot/ only with identical purpose/provenance.
            old = self.one("SELECT c.job_id FROM github_checks c JOIN github_commits g USING(team_id,sha) "
                           "WHERE g.team_id=? AND g.submission_id=? AND c.purpose=? AND c.state='pass' LIMIT 1",
                           (team, commit["submission_id"], purpose))
            if old:
                self.execute("UPDATE github_checks SET job_id=?,state='pass' WHERE team_id=? AND sha=? AND purpose=?", (old["job_id"], team, sha, purpose))
                self.execute("UPDATE github_commits SET state='pass' WHERE team_id=? AND sha=?", (team, sha))
                return
            body = {"submission_id": commit["submission_id"], **({"match_ms": 60_000} if purpose == "practice" else {"competition_id": purpose})}
            # New attempt gets a fresh idempotency key; an interrupted enqueue is harmless:
            # Service.enqueue reuses the same waiting identity before replacing anything.
            job = self.service.enqueue(team, "benchmark", body, "github-check-" + sha + "-" + purpose + "-" + str(self.clock()), managed=True)
            self.execute("UPDATE github_checks SET job_id=?,state='checking' WHERE team_id=? AND sha=? AND purpose=?", (job["id"], team, sha, purpose))
        except Fault as exc:
            if exc.code not in ("quota_exceeded", "rate_limited", "worker_unavailable"):
                raise
            self.execute("UPDATE github_checks SET retry_ms=? WHERE team_id=? AND sha=? AND purpose=?", (self.clock() + 5000, team, sha, purpose))

    def send_status(self, row):
        repo = self.one("SELECT * FROM github_repositories WHERE team_id=?", (row["team_id"],))
        checks = self.rows("SELECT * FROM github_checks WHERE team_id=? AND sha=?", (row["team_id"], row["sha"]))
        if row["state"] not in ("pass", "fail") or any(c["state"] in ("waiting", "checking") for c in checks):
            return
        # An invalid archive has no benchmark. A durable status marker handles both paths.
        if not checks:
            self.ensure_check(row["team_id"], row["sha"], "archive")
            self.execute("UPDATE github_checks SET state='fail' WHERE team_id=? AND sha=? AND purpose='archive'", (row["team_id"], row["sha"]))
            checks = self.rows("SELECT * FROM github_checks WHERE team_id=? AND sha=?", (row["team_id"], row["sha"]))
        if all(c["status_sent"] for c in checks):
            return
        passed = row["state"] == "pass" and all(c["state"] == "pass" for c in checks)
        try:
            job = next((c["job_id"] for c in checks if c["job_id"]), None)
            self.client.status(repo["owner"], repo["name"], repo["installation_id"], row["sha"], passed,
                               "Đạt 72/72" if passed else "Lỗi: " + (row["error"] or "không qua 72 mẫu")[:120],
                               self.settings.origin + ("/#/jobs/" + job if job else "/#/submissions"))
            self.execute("UPDATE github_checks SET status_sent=1 WHERE team_id=? AND sha=?", (row["team_id"], row["sha"]))
        except GitHubError:
            pass

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            due = self.rows("SELECT r.* FROM github_repositories r JOIN teams t ON t.id=r.team_id WHERE r.next_poll_ms<=? AND t.archived_ms IS NULL ORDER BY r.next_poll_ms LIMIT 32", (self.clock(),))
            if self.pollers:
                list(self.pollers.map(self.poll, due))
            else:
                for repo in due:
                    self.poll(repo)
            # One source per team in a batch preserves push order when downloads overlap.
            pending = self.rows("SELECT * FROM (SELECT g.*,ROW_NUMBER() OVER(PARTITION BY team_id ORDER BY received_ms,rowid) AS position FROM github_commits g WHERE state='detected' AND retry_ms<=?) WHERE position=1 ORDER BY received_ms LIMIT 4", (self.clock(),))
            if self.receivers:
                list(self.receivers.map(self.receive, pending))
            else:
                for row in pending:
                    self.receive(row)
            for check in self.rows("SELECT * FROM github_checks WHERE state IN ('waiting','checking') AND retry_ms<=?", (self.clock(),)):
                self.run_check(check)
            for row in self.rows("SELECT * FROM github_commits WHERE submission_id IS NOT NULL AND state<>'detected'"):
                states = [c["state"] for c in self.rows("SELECT state FROM github_checks WHERE team_id=? AND sha=?", (row["team_id"], row["sha"]))]
                state = "checking" if not states or any(s in ("waiting", "checking") for s in states) else "fail" if "fail" in states else "pass" if "pass" in states else "replaced"
                if state != row["state"]:
                    self.execute("UPDATE github_commits SET state=? WHERE team_id=? AND sha=?", (state, row["team_id"], row["sha"]))
            self.deadlines()
            for row in self.rows("SELECT * FROM github_commits WHERE state IN ('pass','fail')"):
                self.send_status(row)
        finally:
            self.tick_lock.release()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                # Do not log GitHub response bodies, signed URLs or credential-bearing requests.
                trace = exc.__traceback__
                while trace.tb_next:
                    trace = trace.tb_next
                logging.getLogger("arena.github").error("GitHub intake %s tại %s:%s; thử lại", type(exc).__name__, trace.tb_frame.f_code.co_name, trace.tb_lineno)
            self.wake.wait(1)
            self.wake.clear()

    def deadlines(self):
        for competition in self.rows("SELECT * FROM competitions WHERE status='open' AND archived_ms IS NULL AND submission_deadline IS NOT NULL"):
            ident, deadline = competition["id"], competition["submission_deadline"]
            if self.clock() < deadline:
                # Opening a competition also checks already received bots on its pinned runtime.
                for row in self.rows("SELECT g.team_id,g.sha FROM github_observations o JOIN github_commits g USING(team_id,sha) WHERE o.id=(SELECT MAX(x.id) FROM github_observations x WHERE x.team_id=o.team_id) AND g.submission_id IS NOT NULL"):
                    self.ensure_check(row["team_id"], row["sha"], ident)
                continue
            for repo in self.rows("SELECT r.* FROM github_repositories r JOIN teams t ON t.id=r.team_id WHERE t.archived_ms IS NULL AND t.season_id=?", (self.teams.season,)):
                choice = self.one("SELECT * FROM github_deadlines WHERE competition_id=? AND team_id=?", (ident, repo["team_id"]))
                if not choice:
                    self.snapshot_deadline(competition, repo)
                    choice = self.one("SELECT * FROM github_deadlines WHERE competition_id=? AND team_id=?", (ident, repo["team_id"]))
                if choice["state"] == "checking":
                    self.resolve_deadline(competition, repo, choice)

    def snapshot_deadline(self, competition, repo):
        ident, team, deadline = competition["id"], repo["team_id"], competition["submission_deadline"]
        source, state, error, candidates = "github_activity", "checking", None, []
        access_checked = False
        try:
            installation = self.client.installation(repo["owner"], repo["name"])
            metadata = self.client.get(repo["owner"], repo["name"], installation)[2]
            if metadata.get("private") is not True or metadata.get("id") != repo["repo_id"]:
                state, error = "no_entry", "Repo không còn Private hoặc đã thay đổi"
            else:
                access_checked = True
                history = self.client.activities(repo["owner"], repo["name"], installation, deadline)
                # API is descending push order. Keep repeated pushes of the same SHA once.
                seen = set()
                for row in history:
                    if row["after"] not in seen:
                        candidates.append({"sha": row["after"], "pushed_ms": unix_ms(row["pushed_at"])})
                        seen.add(row["after"])
                    if len(candidates) == 6:
                        break
                if not candidates or candidates[0]["sha"] == "0" * 40:
                    state, error, candidates = "no_entry", "Nhánh main chưa có hoặc đã bị xóa tại hạn", []
        except GitHubError as exc:
            if not access_checked and exc.status in (401, 403, 404):
                state, error = "no_entry", "Không đọc được repo tại hạn; cần giữ quyền App đến lúc chốt"
            else:
                source, state = "hub_observed", "needs_approval"
                error = "[HỎI] Không đọc được activity GitHub; BTC cần duyệt rõ nguồn dự phòng trước khi tạo bài dự giải"
                candidates = self.observations(team, deadline)
        with self.store.transaction():
            self.execute("INSERT OR IGNORE INTO github_deadlines(competition_id,team_id,source,candidates,state,error) VALUES(?,?,?,?,?,?)",
                         (ident, team, source, json.dumps(candidates), state, error))
            if state in ("no_entry", "needs_approval"):
                self.execute("DELETE FROM competition_entries WHERE competition_id=? AND team_id=?", (ident, team))

    def approve_fallback(self, ident, team, actor):
        with self.store.transaction():
            competition = self.competitions.get(ident)
            self.competitions.require_status(competition, "open")
            row = self.one("SELECT * FROM github_deadlines WHERE competition_id=? AND team_id=?", (ident, team))
            if not row or row["state"] != "needs_approval" or row["source"] != "hub_observed":
                raise Fault("admin_conflict", "Không có nguồn dự phòng đang chờ duyệt")
            self.db.execute("UPDATE github_deadlines SET state='checking',error=NULL WHERE competition_id=? AND team_id=?", (ident, team))
            self.a.audit(actor, "github.deadline_fallback_approved", ident + ":" + team)
        self.wake.set()
        return {"approved": True, "source": "hub_observed"}

    def resolve_deadline(self, competition, repo, choice):
        ident, team = competition["id"], repo["team_id"]
        for candidate in json.loads(choice["candidates"]):
            sha = candidate["sha"]
            if sha == "0" * 40:
                continue
            row = self.one("SELECT * FROM github_commits WHERE team_id=? AND sha=?", (team, sha))
            if not row:
                self.detect(repo, sha, candidate["pushed_ms"], observed=False)
                return
            if row["state"] == "detected":
                return
            if not row["submission_id"]:  # Invalid bot/ archive.
                continue
            self.ensure_check(team, sha, ident)
            check = self.one("SELECT * FROM github_checks WHERE team_id=? AND sha=? AND purpose=?", (team, sha, ident))
            if check["state"] == "replaced":
                self.execute("UPDATE github_checks SET state='waiting',job_id=NULL WHERE team_id=? AND sha=? AND purpose=?", (team, sha, ident))
                return
            if check["state"] not in ("pass", "fail"):
                return
            if check["state"] == "fail":
                continue
            with self.store.transaction():
                current = self.competitions.get(ident)
                self.competitions.require_status(current, "open")
                try:
                    self.teams.eligible(team)
                    snapshot = self.competitions.qualification(current["config"], team, row["submission_id"], check["job_id"])
                except Fault as exc:
                    self.db.execute("UPDATE github_deadlines SET state='no_entry',error=? WHERE competition_id=? AND team_id=?", (exc.detail, ident, team))
                    self.db.execute("DELETE FROM competition_entries WHERE competition_id=? AND team_id=?", (ident, team))
                    return
                count = self.db.execute("SELECT COUNT(*) FROM competition_entries WHERE competition_id=? AND team_id<>?", (ident, team)).fetchone()[0]
                if count >= current["config"]["max_teams"]:
                    self.db.execute("UPDATE github_deadlines SET state='no_entry',error='Giải đã đủ đội' WHERE competition_id=? AND team_id=?", (ident, team))
                    self.db.execute("DELETE FROM competition_entries WHERE competition_id=? AND team_id=?", (ident, team))
                    return
                snapshot.update(github_sha=sha, github_pushed_ms=candidate["pushed_ms"], deadline_source=choice["source"])
                self.db.execute("INSERT INTO competition_entries VALUES(?,?,?,?,?,?) ON CONFLICT(competition_id,team_id) DO UPDATE SET submission_id=excluded.submission_id,benchmark_id=excluded.benchmark_id,snapshot=excluded.snapshot,accepted_ms=excluded.accepted_ms",
                                (ident, team, row["submission_id"], check["job_id"], json.dumps(snapshot), self.clock()))
                self.db.execute("UPDATE github_deadlines SET state='locked',selected_sha=?,pushed_ms=?,error=NULL WHERE competition_id=? AND team_id=?",
                                (sha, candidate["pushed_ms"], ident, team))
                self.a.audit("system", "github.deadline_locked", ident + ":" + team + ":" + sha)
            return
        with self.store.transaction():
            self.execute("UPDATE github_deadlines SET state='no_entry',error='Không có bản Đạt trong bản tại hạn và tối đa 5 lần push lùi' WHERE competition_id=? AND team_id=?", (ident, team))
            self.execute("DELETE FROM competition_entries WHERE competition_id=? AND team_id=?", (ident, team))

    def view(self, team):
        repo = self.one("SELECT * FROM github_repositories WHERE team_id=?", (team,))
        commits = self.rows("SELECT * FROM github_commits WHERE team_id=? ORDER BY received_ms DESC LIMIT 20", (team,))
        queue_total = self.service.pending.qsize()
        for commit in commits:
            checks = self.rows("SELECT * FROM github_checks WHERE team_id=? AND sha=?", (team, commit["sha"]))
            commit["checks"] = checks
            for check in checks:
                if check["job_id"]:
                    job = self.store.get(check["job_id"], team, "job")
                    check["phase"] = job["status"]
                    check["queue_position"] = self.service.pending.position(job["id"])
                    check["queue_total"] = queue_total
                    report = job.get("report") or {}
                    progress = [e["data"].get("completed", 0) for e in self.store.events(job["id"]) if e["type"] == "progress"]
                    check["samples"] = max([len(report.get("samples", [])), *progress])
        previews = []
        for comp in self.rows("SELECT id,title,submission_deadline FROM competitions WHERE status='open' AND submission_deadline>? AND archived_ms IS NULL", (self.clock(),)):
            passed = next((r for r in self.observations(team, self.clock()) if self.one("SELECT 1 FROM github_checks WHERE team_id=? AND sha=? AND purpose=? AND state='pass'", (team, r["sha"], comp["id"]))), None)
            previews.append({"competition_id": comp["id"], "title": comp["title"], "deadline": comp["submission_deadline"], "sha": passed["sha"] if passed else None})
        return {"repo": {k: repo[k] for k in ("owner", "name", "state", "error", "last_poll_ms")} if repo else None,
                "repo_code": self.a.team(team)["repo_code"], "repo_prefix": self.settings.github.repo_prefix,
                "install_url": INSTALL_URL, "starter_url": "https://github.com/uet-bot-arena/ott-bot-starter",
                "commits": commits, "previews": previews, "deadlines": self.rows("SELECT * FROM github_deadlines WHERE team_id=?", (team,))}
