"""Durable competition scheduling. SQL owns fixtures; the worker queue is a cache.

Practice documents remain private. Official jobs use a controller-only namespace
and explicitly record the owner of each locked submission.
"""
import json
import logging
import math
import threading
import uuid

from arena import engine, sandbox, experimental, basic_rules
from arena.adjudication import CURRENT, LEGACY, policy, valid_result
from arena.config import benchmark_profile, BUILD_FLAGS, PYTHON_TOOLCHAIN, request_profile, stored_profile, scale_profile, runtime_flags
from arena.protocol import Fault
from arena.service import TERMINAL, digest, fixtures
from arena_api.accounts import now_ms

SCORING = {'win': 3, 'draw': 1, 'loss': 0}
# Attempt failures caused by the platform, never by a bot: retried automatically up to K times.
INFRASTRUCTURE = ("worker_lost", "worker_error", "worker_unavailable", "server_restart", "server_shutdown")


def runtime_of(report):
    """runtime_id recorded in a report or build manifest (older reports used image_id)."""
    return (report or {}).get("runtime_id") or (report or {}).get("image_id")


class Competitions:
    def __init__(self, store, service, accounts, max_attempts=3):
        self.store, self.service, self.accounts = store, service, accounts
        self.max_attempts = max_attempts
        self.schedule_lock = threading.Lock()
        service.competitions = self
        self.db = store.db
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="competition-scheduler")
        self.scheduler_error = None
        self.suite_sha256 = digest(fixtures())
        from arena_api.elo_scheduler import EloScheduler
        self.elo = EloScheduler(self)
        from arena_api.finals import Finals
        self.finals = Finals(self)
        # Running competitions keep scheduling; interrupted games get a fresh attempt.
        # A competition explicitly paused by the organiser stays paused.
        self.reconcile()
        self.service.before_next = self.schedule_once

    def one(self, sql, args=()):
        return self.accounts.one(sql, args)

    def rows(self, sql, args=()):
        return self.accounts.rows(sql, args)

    def get(self, ident):
        row = self.one("SELECT * FROM competitions WHERE id=?", (ident,))
        if row is None:
            raise Fault("not_found", "Competition not found")
        row["config"] = json.loads(row["config"])
        return row

    @staticmethod
    def require_status(item, *allowed):
        if item.get("archived_ms"):
            raise Fault("admin_conflict", "Restore this competition before changing it")
        if item["status"] not in allowed:
            raise Fault("competition_conflict", "Operation is not allowed in competition state: " + item["status"])

    def command(self, actor, operation, key, body, action, prepare=None):
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise Fault("invalid_request", "Idempotency-Key of 8..128 characters required")
        hashed = digest(body)
        def existing():
            old = self.one("SELECT * FROM competition_commands WHERE actor=? AND operation=? AND key=?", (actor, operation, key))
            if old and old["digest"] != hashed:
                raise Fault("idempotency_conflict", "Key already used for different input")
            return old
        with self.store.lock:
            old = existing()
            if old:
                return json.loads(old["response"])
        # Prepare outside the SQLite/controller lock; exact retries reuse the saved response.
        prepared = prepare() if prepare else None
        with self.store.lock, self.db:
            old = existing()
            if old:
                return json.loads(old["response"])
            # action uses SQL only: record, audit and idempotency commit together.
            result = action(prepared) if prepare else action()
            self.accounts.audit(actor, "competition." + operation.split(":")[0], result["id"])
            self.db.execute("INSERT INTO competition_commands VALUES(?,?,?,?,?)", (actor, operation, key, hashed, json.dumps(result)))
            return result

    @staticmethod
    def platform_of(config):
        """(grader_platform, runtime_id) official work must run on. Older configs: linux + image_id."""
        return config.get("grader_platform", "linux"), runtime_of(config)

    def config_for(self, body):
        snapshot = self.service.rulesets.resolve(body.get("ruleset_id")) if self.service.rulesets else None
        platform = body.get("grader_platform", "windows")
        runtime_id = body.get("runtime_id") or self.service.runtime_for(platform)
        if not runtime_id:
            raise Fault("competition_conflict", f"Chưa có máy chấm {platform} nào kết nối; chạy máy chấm trước hoặc ghi rõ runtime_id")
        config = {"format": "double_round_robin", "ranking": "points_h2h_wins", "scoring": dict(body.get("scoring", SCORING)), "max_teams": body["max_teams"], "adjudication": dict(CURRENT),
                  "profile": request_profile(body).public(), "runtime": "cpp17", "protocol_version": 2,
                  "grader_platform": platform, "runtime_id": runtime_id, "engine_id": engine.ENGINE_ID, "engine_sha256": engine.ENGINE_SHA256,
                  "sdk_sha256": sandbox.sdk_hash(),
                  "build_flags": BUILD_FLAGS, "suite_id": "ottv2-public-smoke-v1", "suite_sha256": self.suite_sha256}
        if snapshot:
            config.update(ruleset=snapshot,protocol_version=3,engine_id=experimental.ENGINE_ID,
                          engine_sha256=experimental.ENGINE_SHA256,sdk_sha256=experimental.sdk_hash(),suite_id='ottv2-experimental-smoke-v1',
                          suite_sha256=digest(fixtures(experimental.provider(snapshot))))
        if body.get("basic_rules"):
            pack = basic_rules.validated_snapshot(body["basic_rules"])
            d = pack["definition"]
            config.update(basic_rules=pack, profile=scale_profile(request_profile(body), d["rows"]*d["cols"]).public(),
                          suite_sha256=digest(fixtures(basic_rules.provider(pack))))
        config.update(format=body.get("format", "double_round_robin"), rounds=body.get("rounds"),
                      runtime_flags={r:runtime_flags(r) for r in ("cpp17","c11","python3")},
                      runtimes=["cpp17", "c11", "python3"], python_interpreter="pypy3",
                      toolchains={"cpp17":"g++ -std=c++17 -O2", "c11":"gcc -std=c11 -O2", "python3":PYTHON_TOOLCHAIN})
        if config["format"] == "swiss":
            config["ranking"] = "points_buchholz"
        if config['format']=='elo':
            config.update(ladder=body.get('ladder',dict(round_hours=2)),official=dict({'match_ms':10000},**body.get('official',dict(general_rounds=20,group_size=64,finalists=16,k=[[20,32],[None,16]]))),ranking='round_robin_points',scoring=dict(win=1,draw=.5,loss=0))
        return config

    def create(self, actor, body, key, *, pinned_config=None):
        def prepare():
            return dict(pinned_config, max_teams=body["max_teams"]) if pinned_config else self.config_for(body)
        def action(config):
            if self.db.execute("SELECT COUNT(*) FROM competitions").fetchone()[0] >= 100:
                raise Fault("quota_exceeded", "Local beta allows 100 competitions")
            ident = uuid.uuid4().hex
            self.db.execute("INSERT INTO competitions(id,title,status,config,created_ms,submission_deadline) VALUES(?,?,'draft',?,?,?)",
                            (ident, body["title"], json.dumps(config), now_ms(), body.get("submission_deadline")))
            return self.get(ident)
        return self.command(actor, "create", key, body, action, prepare)

    def edit(self, actor, ident, body, key):
        def action():
            item = self.get(ident)
            self.require_status(item, 'draft', 'open')
            if item['revision'] != body['expected_revision']:
                raise Fault('admin_conflict', 'Competition changed; refresh before editing')
            entries = self.db.execute('SELECT COUNT(*) FROM competition_entries WHERE competition_id=?', (ident,)).fetchone()[0]
            if body['max_teams'] < entries:
                raise Fault('admin_conflict', 'Capacity cannot be smaller than the current entry count')
            config = item['config']
            if body.get('ruleset_id') and body['ruleset_id'] != (config.get('ruleset') or {}).get('id'):
                raise Fault('admin_conflict','Ruleset is immutable for this competition; create a new competition')
            requested = request_profile(body).public()
            if config.get("basic_rules"):
                d = config["basic_rules"]["definition"]
                requested = scale_profile(request_profile(body), d["rows"]*d["cols"]).public()
            if body.get("basic_rules") and basic_rules.snapshot(body["basic_rules"]) != config.get("basic_rules"):
                raise Fault("admin_conflict", "Basic rules are immutable; create another competition")
            if body.get("format", config["format"]) != config["format"] or body.get("rounds") not in (None, config.get("rounds")):
                raise Fault("admin_conflict", "Format and rounds are immutable; create another competition")
            if config['format']=='elo' and any(body.get(k,config[k])!=config[k] for k in ('ladder','official')):
                raise Fault('admin_conflict','Luật Elo giữ cố định từ khi tạo giải')
            if requested != config['profile']:
                if item['status'] != 'draft' or entries:
                    raise Fault('admin_conflict', 'Time control can only change in an empty draft')
                if bool(requested.get('match_ms')) != bool(config['profile'].get('match_ms')):
                    raise Fault('admin_conflict', 'Create a new competition to switch from legacy turn clocks to match clocks')
                config['profile'] = requested
            config['max_teams'] = body['max_teams']
            deadline = body.get("submission_deadline", item.get("submission_deadline"))
            if item.get("submission_deadline") is not None and now_ms() >= item["submission_deadline"] and deadline != item["submission_deadline"]:
                raise Fault("admin_conflict", "Hạn đã qua; tạo giải khác để giữ nguồn chốt bất biến")
            self.db.execute('UPDATE competitions SET title=?,config=?,submission_deadline=?,revision=revision+1 WHERE id=?', (body['title'],json.dumps(config),deadline,ident))
            return self.get(ident)
        return self.command(actor, 'edit:'+ident, key, body, action)

    def archive(self, actor, ident, expected, archived, key):
        def action():
            item = self.get(ident)
            if item['revision'] != expected:
                raise Fault('admin_conflict', 'Competition changed; refresh before deleting/restoring')
            if archived:
                self.require_status(item, 'draft', 'open', 'completed')
                entries = self.db.execute('SELECT COUNT(*) FROM competition_entries WHERE competition_id=?', (ident,)).fetchone()[0]
                if item['status'] != 'completed' and entries:
                    raise Fault('admin_conflict', 'Remove all open entries before deleting this competition')
            self.db.execute('UPDATE competitions SET archived_ms=?,revision=revision+1 WHERE id=?', (now_ms() if archived else None, ident))
            return self.get(ident)
        return self.command(actor, ('archive:' if archived else 'restore:')+ident, key, {'expected_revision':expected}, action)

    def transition(self, actor, ident, target, key):
        self.reconcile()
        def action():
            item = self.get(ident)
            allowed = {"open": ("draft",), "running": ("locked", "paused"), "paused": ("running",)}
            self.require_status(item, *allowed[target])
            if target == "running":
                self.check_runtime(item["config"])
                if item["config"].pop("pairing_error", None) or item["config"].pop("elo_error", None):
                    # Resuming retries the Swiss pairing once on the next scheduler tick.
                    self.db.execute("UPDATE competitions SET config=? WHERE id=?", (json.dumps(item["config"]), ident))
            self.db.execute("UPDATE competitions SET status=?,revision=revision+1 WHERE id=?", (target, ident))
            if target=='open' and item['config']['format']=='elo':
                from arena.ladder_clock import boundary
                self.db.execute('INSERT OR IGNORE INTO elo_calendar VALUES(?,?)',(ident,boundary(now_ms(),item['config']['ladder']['round_hours'])))
            return self.get(ident)
        return self.command(actor, target + ":" + ident, key, {}, action)

    @staticmethod
    def check_runtime(config):
        policy(config.get("adjudication"))
        experimental.provider(config.get("ruleset"))
        basic_rules.provider(config.get("basic_rules"))
        expected_engine = experimental.ENGINE_SHA256 if config.get("ruleset") else engine.ENGINE_SHA256
        expected_sdk = experimental.sdk_hash() if config.get("ruleset") else sandbox.sdk_hash()
        if (config["engine_sha256"] != expected_engine or config["build_flags"] != BUILD_FLAGS
                or config.get("runtime_flags") is not None and config["runtime_flags"] != {r:runtime_flags(r) for r in ("cpp17","c11","python3")}
                or config["sdk_sha256"] != expected_sdk
                or config["profile"] != stored_profile(config["profile"]).public()):
            raise Fault("provenance_mismatch", "Pinned competition runtime changed; create a new competition")

    def qualification(self, config, team, submission_id, benchmark_id):
        source = self.store.get(submission_id, team, "submission")
        if source.get("channel") == "practice":
            raise Fault('qualification_required', 'Bản thử nhanh chỉ dùng để kiểm và luyện tập; giải nhận bot từ GitHub')
        job = self.store.get(benchmark_id, team, "job")
        report = job.get("report") or job.get('qualification') or {}
        check_profile=benchmark_profile()
        if config.get('basic_rules'):
            d=config['basic_rules']['definition'];check_profile=scale_profile(check_profile,d['rows']*d['cols'])
        artifacts = report.get("artifacts") or []
        artifact = artifacts[0] if len(artifacts) == 1 else {}
        if (source.get("runtime") not in ("cpp17", "c11", "python3") or job.get("kind") != "benchmark" or job["status"] != "completed"
                or job.get("submission_ids") != [submission_id] or report.get("passed") is not True
                or (report.get("ruleset") or {}).get("hash") != (config.get("ruleset") or {}).get("hash")
                or (report.get("basic_rules") or {}).get("hash") != (config.get("basic_rules") or {}).get("hash")
                or report.get("error") or report.get("aborted") or (len(report.get('samples', [])) if job.get('report') else report.get('sample_count')) != 72
                or (report.get("turn_wall_ms") or {}).get("samples") != 72
                or report.get("profile") not in (check_profile.public(),config["profile"])
                or job.get("profile",report.get("profile"))!=report.get("profile") or runtime_of(report) != runtime_of(config)
                or report.get("engine_sha256") != config["engine_sha256"] or report.get("suite_sha256") != config["suite_sha256"]
                or report.get("suite_id") != config["suite_id"] or artifact.get("source") != source.get("source")
                or runtime_of(artifact) != runtime_of(config) or artifact.get("sdk_sha256") != config["sdk_sha256"]
                or artifact.get("flags") != config.get("runtime_flags",{"cpp17":config["build_flags"]}).get(source.get("runtime")) or not artifact.get("artifact_sha256")
                or artifact.get("runtime", "cpp17") != source.get("runtime")):
            raise Fault("qualification_required", "A completed 72-sample check under the pinned rules/toolchain is required")
        return {"team_id": team, "submission_id": submission_id, "version": source["version"], "source": source["source"],
                "artifact_sha256": artifact["artifact_sha256"], "benchmark_id": benchmark_id,
                "benchmark_report_sha256": digest(report) if job.get('report') else report['report_sha256']}

    def latest_qualified(self, config, team):
        jobs = self.store.list('job', team)
        for source in self.store.list('submission', team):
            for job in jobs:
                if job.get('submission_ids') != [source['id']] or job.get('kind') != 'benchmark':
                    continue
                try:
                    self.qualification(config, team, source['id'], job['id'])
                    return {'submission_id':source['id'], 'benchmark_id':job['id']}
                except Fault:
                    continue
        raise Fault('qualification_required', 'Đội chưa có bản Đạt đủ 72 mẫu theo luật và cấu hình máy chấm của giải')

    def add_team(self, actor, team, ident, key):
        return self.enter(actor, team, ident, {'team_id':team}, key, admin=True)

    def enter(self, actor, team, ident, body, key, *, admin=False, pinned=False):
        def action():
            if hasattr(self, "teams"):
                self.teams.eligible(team)
            item = self.get(ident)
            self.require_status(item, *( ("draft", "open") if admin else ("open",) ))
            if item.get("submission_deadline") is not None:
                raise Fault("forbidden", "Hub tự chốt bài GitHub khi hết hạn; đội không chọn bài tay")
            count = self.db.execute("SELECT COUNT(*) FROM competition_entries WHERE competition_id=? AND team_id<>?", (ident, team)).fetchone()[0]
            if count >= item["config"]["max_teams"]:
                raise Fault("quota_exceeded", "Competition entry limit reached")
            selected = self.latest_qualified(item["config"], team) if admin and not pinned else body
            snapshot = self.qualification(item["config"], team, selected["submission_id"], selected["benchmark_id"])
            self.db.execute("INSERT INTO competition_entries VALUES(?,?,?,?,?,?) ON CONFLICT(competition_id,team_id) DO UPDATE SET submission_id=excluded.submission_id, benchmark_id=excluded.benchmark_id, snapshot=excluded.snapshot, accepted_ms=excluded.accepted_ms",
                            (ident, team, selected["submission_id"], selected["benchmark_id"], json.dumps(snapshot), now_ms()))
            return {"id": ident, "team_id": team, "submission_id": selected["submission_id"], "version": snapshot["version"],
                    "delegate_timeout_claims": True, "status": "entered"}
        return self.command(actor, ("add_team:" if admin else "entry:") + ident, key, body, action)

    def lock(self, actor, ident, key):
        def action():
            item = self.get(ident)
            self.require_status(item, "open")
            if item.get("submission_deadline") is not None:
                if now_ms() < item["submission_deadline"]:
                    raise Fault("competition_conflict", "Chưa hết hạn nộp GitHub")
                remaining = self.db.execute("SELECT COUNT(*) FROM github_repositories r JOIN teams t ON t.id=r.team_id LEFT JOIN github_deadlines d ON d.team_id=r.team_id AND d.competition_id=? WHERE t.archived_ms IS NULL AND t.season_id=? AND (d.state IS NULL OR d.state NOT IN ('locked','no_entry'))",
                                            (ident, self.teams.season)).fetchone()[0]
                if remaining:
                    raise Fault("competition_conflict", "Hub đang kiểm bài tại hạn hoặc chờ BTC duyệt nguồn dự phòng")
            self.check_runtime(item["config"])
            entries = self.rows("SELECT * FROM competition_entries WHERE competition_id=? ORDER BY team_id", (ident,))
            if len(entries) < 2:
                raise Fault("competition_conflict", "At least two qualified entries are required")
            for entry in entries:
                if hasattr(self, "teams"):
                    self.teams.eligible(entry["team_id"])
                account = self.one("SELECT id FROM accounts WHERE team_id=? AND locked=0 AND must_change=0", (entry["team_id"],))
                if not account:
                    raise Fault("competition_conflict", "All entered teams must have active accounts at lock time")
                other=self.one("SELECT e.submission_id FROM competition_entries e JOIN competitions c ON c.id=e.competition_id WHERE e.team_id=? AND e.competition_id<>? AND c.status IN ('locked','running','paused') AND e.submission_id<>? LIMIT 1",(entry['team_id'],ident,entry['submission_id']))
                if other:
                    raise Fault('competition_conflict','Đội đang có bot khác được ghim trong giải chưa xong; hoàn tất giải đó trước để giữ đúng giới hạn cache')
                current = self.qualification(item["config"], entry["team_id"], entry["submission_id"], entry["benchmark_id"])
                if not self.service.has_artifact(item['config']['grader_platform'],current['artifact_sha256']):
                    raise Fault('qualification_required','Binary cũ đã dọn; kiểm tra lại commit trước khi chốt bot dự giải')
                saved = json.loads(entry["snapshot"])
                for key in ("github_sha", "github_pushed_ms", "deadline_source"):
                    saved.pop(key, None)
                if current != saved:
                    raise Fault("provenance_mismatch", "Entry qualification changed; team must enter again")
            if item['config']['format']=='elo':
                if item.get('submission_deadline') is None:
                    raise Fault('competition_conflict','Giải Elo cần hạn nộp GitHub')
                self.db.execute("UPDATE competitions SET status='locked',locked_ms=?,revision=revision+1 WHERE id=?",(now_ms(),ident))
                return {**self.get(ident),'fixture_count':0}
            if item["config"]["format"] == "swiss":
                config = item["config"]
                count = len(entries)
                maximum = count if count % 2 else count-1
                rounds = config.get("rounds") or min(maximum, math.ceil(math.log2(count))+2)
                if rounds > maximum:
                    raise Fault("competition_conflict", "Too many Swiss rounds without repeated opponents/byes")
                config.update(rounds=rounds, current_round=0)
                self.db.execute("UPDATE competitions SET config=? WHERE id=?", (json.dumps(config), ident))
                self._swiss_round(ident)
                self.db.execute("UPDATE competitions SET status='locked',locked_ms=?,revision=revision+1 WHERE id=?", (now_ms(), ident))
                return {**self.get(ident), "fixture_count":count//2*2}
            # Circle method: everyone plays once per round, each pair swaps
            # colours in the second leg; odd counts receive one bye per leg.
            teams = [e["team_id"] for e in entries]
            if len(teams) % 2:
                teams.append(None)
            first_leg = []
            for round_index in range(len(teams) - 1):
                for i in range(len(teams) // 2):
                    red, blue = teams[i], teams[-i - 1]
                    if (round_index + i) % 2:
                        red, blue = blue, red
                    if red is not None and blue is not None:
                        first_leg.append((red, blue))
                teams = [teams[0], teams[-1], *teams[1:-1]]
            pairings = first_leg + [(blue, red) for red, blue in first_leg]
            for ordinal, (red, blue) in enumerate(pairings, 1):
                self.db.execute("INSERT INTO competition_fixtures(id,competition_id,ordinal,red_team_id,blue_team_id,status) VALUES(?,?,?,?,?,'pending')",
                                (uuid.uuid4().hex, ident, ordinal, red, blue))
            self.db.execute("UPDATE competitions SET status='locked',locked_ms=?,revision=revision+1 WHERE id=?", (now_ms(), ident))
            return {**self.get(ident), "fixture_count": len(pairings)}
        return self.command(actor, "lock:" + ident, key, {}, action)

    def withdraw(self, actor, team, ident, key):
        def action():
            self.require_status(self.get(ident), "open")
            cursor = self.db.execute("DELETE FROM competition_entries WHERE competition_id=? AND team_id=?", (ident, team))
            if not cursor.rowcount:
                raise Fault("not_found", "Entry not found")
            return {"id": ident, "team_id": team, "status": "withdrawn"}
        return self.command(actor, "withdraw:" + ident + ":" + team, key, {}, action)

    def fixture(self, ident, fixture_id):
        if self.get(ident)['config']['format']=='elo':
            row=self.one('SELECT g.*,r.competition_id,r.phase,r.number AS round_number FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=? AND g.id=?',(ident,fixture_id))
            if row is None:raise Fault('not_found','Không có ván này')
            return row
        row = self.one("SELECT * FROM competition_fixtures WHERE competition_id=? AND id=?", (ident, fixture_id))
        if row is None:
            raise Fault("not_found", "Fixture not found")
        return row

    def retry(self, actor, ident, fixture_id, key):
        if self.get(ident)['config']['format']=='elo':
            def action():
                self.require_status(self.get(ident),'open','running','paused','locked')
                row=self.fixture(ident,fixture_id)
                if row['status']!='attention':raise Fault('competition_conflict','Chỉ chạy lại ván cần BTC xử lý')
                self.db.execute("UPDATE elo_games SET status='pending',auto_retries=0 WHERE id=?",(fixture_id,))
                self.db.execute("UPDATE elo_rounds SET status='running' WHERE id=?",(row['round_id'],))
                return dict(id=ident,fixture_id=fixture_id,status='pending')
            return self.command(actor,'retry:'+fixture_id,key,{},action)
        self.reconcile()
        def action():
            self.require_status(self.get(ident), "locked", "running", "paused")
            row = self.fixture(ident, fixture_id)
            if row["status"] != "attention":
                raise Fault("competition_conflict", "Only an unscored failed/aborted attempt can be retried")
            number = self.db.execute("SELECT COUNT(*) FROM competition_attempts WHERE fixture_id=?", (fixture_id,)).fetchone()[0]
            # A manual retry gives the fixture a fresh budget of automatic infrastructure retries.
            self.db.execute("UPDATE competition_fixtures SET status='pending',auto_retries=0 WHERE id=?", (fixture_id,))
            return {"id": fixture_id, "status": "pending", "next_attempt": number + 1}
        return self.command(actor, "retry:" + fixture_id, key, {}, action)

    @staticmethod
    def valid_report(job,config):
        report=job.get('report') or {};result=report.get('result')
        rules=policy(config.get('adjudication'))
        return (job["status"] == "completed" and report.get("eligible_for_tournament") is True
                  and not report.get("error") and not report.get("aborted") and isinstance(result, dict)
                  and valid_result(result, rules, experimental=bool(config.get("ruleset"))) and report.get("adjudication", LEGACY) == rules
                  and (report.get("final_state") or {}).get("result") == result
                  and (report.get("ruleset") or {}).get("hash") == (config.get("ruleset") or {}).get("hash")
                  and (report.get("basic_rules") or {}).get("hash") == (config.get("basic_rules") or {}).get("hash")
                  and report.get("engine_sha256") == config["engine_sha256"]
                  and runtime_of(report) == runtime_of(config) and report.get("profile") == config["profile"])

    def reconcile(self):
        if hasattr(self, "teams"):
            self.teams.close_due()
        next_rounds = []
        with self.store.lock, self.db:
            for row in self.rows("SELECT * FROM competition_fixtures WHERE status='queued'"):
                job = self.store.get(row["current_job_id"], "competition:" + row["competition_id"], "job")
                if job["status"] not in TERMINAL:
                    continue
                report = job.get("report") or {}
                result = report.get("result")
                config = self.get(row["competition_id"])["config"]
                valid = self.valid_report(job,config)
                status = "completed" if valid else "attention"
                error = None if valid else (job.get("error") or report.get("error") or {}).get("code", "unscored_attempt")
                retry = not valid and error in INFRASTRUCTURE and row["auto_retries"] < self.max_attempts
                fixture_status = "pending" if retry else status
                self.db.execute("UPDATE competition_fixtures SET status=?,result=?,auto_retries=auto_retries+? WHERE id=?",
                                (fixture_status, json.dumps(result) if valid else None, int(retry), row["id"]))
                self.db.execute("UPDATE competition_attempts SET status=?,error_code=? WHERE job_id=?", (status, error, job["id"]))
                self.accounts.audit("system", "competition.result" if valid else "competition.retry" if retry else "competition.attention", row["id"])
            for row in self.rows("SELECT id FROM competitions WHERE status IN ('running','paused') AND COALESCE(json_extract(config,'$.format'),'double_round_robin')<>'elo'"):
                remaining = self.db.execute("SELECT COUNT(*) FROM competition_fixtures WHERE competition_id=? AND status<>'completed'", (row["id"],)).fetchone()[0]
                if remaining == 0 and self.get(row["id"])["config"].get("format") == "swiss":
                    config = self.get(row["id"])["config"]
                    if config.get("current_round", 0) < config["rounds"]:
                        # A failed pairing waits for the organiser; retrying it every tick
                        # would flood logs and block scheduling of other competitions.
                        if not config.get("pairing_error"):
                            next_rounds.append(row["id"])
                        continue
                if remaining == 0:
                    self.db.execute("UPDATE competitions SET status='completed',completed_ms=?,revision=revision+1 WHERE id=?", (now_ms(), row["id"]))
                    self.accounts.audit("system", "competition.completed", row["id"])
        for ident in next_rounds:
            try:
                self._swiss_round(ident)
            except Exception as exc:
                # Isolate one competition's pairing failure from every other competition.
                logging.getLogger("arena.competitions").exception("Swiss pairing failed for %s", ident)
                with self.store.lock, self.db:
                    config = self.get(ident)["config"]
                    config["pairing_error"] = (exc.detail if isinstance(exc, Fault) else "Swiss pairing failed")[:500]
                    self.db.execute("UPDATE competitions SET status='paused',config=?,revision=revision+1 WHERE id=?", (json.dumps(config), ident))
                    self.accounts.audit("system", "competition.pairing_attention", ident)

    def _swiss_round(self, ident):
        from arena_api.swiss import pair
        with self.store.lock:
            item = self.get(ident)
            config = item["config"]
            current = config.get("current_round", 0)
            if current and self.db.execute("SELECT 1 FROM competition_fixtures WHERE competition_id=? AND status<>'completed' LIMIT 1", (ident,)).fetchone():
                return
            teams = [r["team_id"] for r in self.rows("SELECT team_id FROM competition_entries WHERE competition_id=? ORDER BY team_id", (ident,))]
            byes = {r["team_id"] for r in self.rows("SELECT team_id FROM competition_byes WHERE competition_id=?", (ident,))}
            opponents = {t:set() for t in teams}
            for row in self.rows("SELECT red_team_id,blue_team_id FROM competition_fixtures WHERE competition_id=?", (ident,)):
                opponents[row["red_team_id"]].add(row["blue_team_id"])
            points = {r["team_id"]:r["points"] for r in self.standings(ident, True)["items"]}
        # Round zero has no history; adjacent score order is a perfect matching.
        # Later blossom matching runs outside the global database lock.
        if current == 0:
            ordered = sorted(teams)
            bye = ordered.pop() if len(ordered)%2 else None
            pairs = list(zip(ordered[::2], ordered[1::2]))
        else:
            pairs, bye = pair(teams, points, opponents, byes)
        with self.store.lock, self.db:
            latest = self.get(ident)
            if latest["config"].get("current_round",0) != current or current and latest["status"] not in ("running", "paused"):
                return
            number = current+1
            ordinal = self.db.execute("SELECT COALESCE(MAX(ordinal),0) FROM competition_fixtures WHERE competition_id=?", (ident,)).fetchone()[0]
            for a,b in pairs:
                # Both colours in each pair keep every team's colour totals equal.
                for red,blue in ((a,b),(b,a)):
                    ordinal += 1
                    self.db.execute("INSERT INTO competition_fixtures(id,competition_id,ordinal,red_team_id,blue_team_id,status,round_number) VALUES(?,?,?,?,?,'pending',?)",
                                    (uuid.uuid4().hex,ident,ordinal,red,blue,number))
            if bye:
                self.db.execute("INSERT INTO competition_byes VALUES(?,?,?)", (ident,number,bye))
            config["current_round"] = number
            self.db.execute("UPDATE competitions SET config=?,revision=revision+1 WHERE id=?", (json.dumps(config),ident))
            self.accounts.audit("system", "competition.swiss_round", ident)

    def match_job(self,item,entries,fixture_id,number,config=None):
        config=config or item['config']
        platform,runtime_id=self.platform_of(config)
        job = {"id": uuid.uuid4().hex, "team_id": "competition:" + item["id"], "created_unix_ms": now_ms(),
               "kind": "match", "status": "queued", "submission_ids": [e["submission_id"] for e in entries],
               "profile": config["profile"], "requires": {"platform": platform, "runtime_id": runtime_id}, "engine_id": config["engine_id"],
               "engine_sha256": config["engine_sha256"], "report": None, "error": None,
               **({"basic_rules":config["basic_rules"]} if config.get("basic_rules") else {}),
               **({"ruleset":config["ruleset"],"protocol_version":3} if config.get("ruleset") else {}),
               "official": {"competition_id": item["id"], "fixture_id": fixture_id, "attempt": number,
                            "entries": entries, **({"ruleset":config["ruleset"]} if config.get("ruleset") else {}),
                            **({"basic_rules":config["basic_rules"]} if config.get("basic_rules") else {}), "engine_sha256": config["engine_sha256"],
                            "sdk_sha256": config["sdk_sha256"], "build_flags": config["build_flags"],
                            **({"runtime_flags":config["runtime_flags"]} if config.get("runtime_flags") else {}),
                            "adjudication": policy(config.get("adjudication"))}}
        return job

    def tick(self):
        self.elo.tick()
        self.reconcile()
        self.finals.tick()
        with self.store.lock:
            if self.stop.is_set() or self.service.stop.is_set() or self.service.pending.full():
                return
            # Keep enough official work outstanding to occupy every worker.
            if self.db.execute("SELECT COUNT(*) FROM competition_fixtures WHERE status='queued'").fetchone()[0] >= self.service.worker_count:
                return
            row = self.one("SELECT f.* FROM competition_fixtures f JOIN competitions c ON c.id=f.competition_id WHERE f.status='pending' AND c.status='running' ORDER BY c.created_ms,c.id,f.ordinal LIMIT 1")
            if row is None:
                return
            item = self.get(row["competition_id"])
            config = item["config"]
            self.check_runtime(config)
            entries = [json.loads(self.one("SELECT snapshot FROM competition_entries WHERE competition_id=? AND team_id=?", (item["id"], row[side + "_team_id"]))["snapshot"]) for side in engine.SIDES]
            number = self.db.execute("SELECT COUNT(*)+1 FROM competition_attempts WHERE fixture_id=?", (row["id"],)).fetchone()[0]
            platform, runtime_id = self.platform_of(config)
            job = self.match_job(item,entries,row['id'],number)
            with self.db:
                self.db.execute("INSERT INTO documents VALUES(?,?,?,?)", (job["id"], "job", job["team_id"], json.dumps(job)))
                self.db.execute("UPDATE competition_fixtures SET status='queued',current_job_id=? WHERE id=?", (job["id"], row["id"]))
                self.db.execute("INSERT INTO competition_attempts VALUES(?,?,?,'queued',NULL,?)", (row["id"], number, job["id"], now_ms()))
                event = {"seq": 1, "type": "queued", "server_unix_ms": now_ms(), "data": {"kind": "match"}}
                self.db.execute("INSERT INTO events VALUES(?,?,?)", (job["id"], 1, json.dumps(event)))
            # Practice enqueue also holds store.lock; the only other queue
            # operation here is dequeue. A slot cannot be stolen after commit.
            self.service.pending.put_official(job["id"], platform, runtime_id)

    def loop(self):
        while not self.stop.wait(0.2):
            self.schedule_once()

    def schedule_once(self):
        # Called by both scheduler and worker before selecting its next job.
        # Reconcile/dispatch immediately after a game so practice cannot slip
        # ahead of the next official fixture between periodic scheduler ticks.
        if not self.schedule_lock.acquire(blocking=False):
            return
        try:
            self.tick()
            self.scheduler_error = None
        except Exception:
            if self.scheduler_error is None:
                logging.getLogger("arena.competitions").exception("Competition scheduler failed")
            self.scheduler_error = "scheduler_error"
        finally:
            self.schedule_lock.release()

    def listing(self, admin=False, limit=50, offset=0):
        with self.store.lock:
            where = "" if admin else " WHERE status<>'draft' AND archived_ms IS NULL"
            total = self.db.execute("SELECT COUNT(*) FROM competitions" + where).fetchone()[0]
            items = self.rows("SELECT id FROM competitions" + where + " ORDER BY created_ms DESC,id LIMIT ? OFFSET ?", (limit, offset))
            values = [self.get(r["id"]) for r in items]
            if not admin:
                for item in values:
                    item["config"] = self.public_config(item["config"])
            return {"items": values, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def public_config(config):
        value = {k:v for k,v in config.items() if k not in ("image_id", "runtime_id", "engine_sha256", "sdk_sha256", "suite_sha256")}
        if value.get("ruleset"):
            value["ruleset"] = {k:v for k,v in value["ruleset"].items() if k not in ("engine_sha256", "sdk_sha256")}
        return value

    def view(self, ident, admin=False):
        item = self.visible(ident, admin)
        entries = self.rows("SELECT e.team_id,t.name,e.accepted_ms,json_extract(e.snapshot,'$.version') AS version FROM competition_entries e JOIN teams t ON t.id=e.team_id WHERE e.competition_id=? ORDER BY e.team_id", (ident,))
        # Deliberate whitelist: no contact info, binary/build log, benchmark
        # report or private worker job ID in the shared competition view.
        item["entries"] = entries
        if item['config']['format']=='elo':
            item['elo']=self.elo.public(ident)
            known={r['id']:r['name'] for r in self.rows("SELECT t.id,t.name FROM teams t JOIN elo_ratings r ON r.team_id=t.id WHERE r.competition_id=?",(ident,))}
            existing={e['team_id'] for e in item['entries']}
            item['entries'].extend(dict(team_id=t,name=name,version='',accepted_ms=None) for t,name in known.items() if t not in existing)
            counts={r['status']:r['n'] for r in self.rows('SELECT g.status,COUNT(*) AS n FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=? GROUP BY g.status',(ident,))}
        else:
            counts = {r["status"]: r["n"] for r in self.rows("SELECT status,COUNT(*) n FROM competition_fixtures WHERE competition_id=? GROUP BY status", (ident,))}
        item["fixture_counts"] = {s: counts.get(s, 0) for s in ("pending", "queued", "completed", "attention")}
        if not admin:
            item["config"] = self.public_config(item["config"])
        return item

    @staticmethod
    def fixture_view(row):
        return {"id": row["id"], "competition_id": row["competition_id"], "ordinal": row["ordinal"],
                "red_team_id": row["red_team_id"], "blue_team_id": row["blue_team_id"], "status": row["status"],
                "result": json.loads(row["result"]) if row["result"] else None}

    def schedule(self, ident, admin=False, limit=50, offset=0):
        item=self.view(ident, admin)
        if item['config']['format']=='elo':
            with self.store.lock:
                total=self.one('SELECT COUNT(*) AS n FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=?',(ident,))['n']
                rows=self.rows('SELECT g.*,r.competition_id,r.phase FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=? ORDER BY r.started_ms,r.phase,r.number,g.ordinal LIMIT ? OFFSET ?',(ident,limit,offset))
                return dict(items=[self.fixture_view(r)|dict(phase=r['phase']) for r in rows],total=total,limit=limit,offset=offset)
        with self.store.lock:
            total = self.db.execute("SELECT COUNT(*) FROM competition_fixtures WHERE competition_id=?", (ident,)).fetchone()[0]
            rows = self.rows("SELECT * FROM competition_fixtures WHERE competition_id=? ORDER BY ordinal LIMIT ? OFFSET ?", (ident, limit, offset))
            return {"items": [self.fixture_view(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def standings(self, ident, admin=False):
        item = self.visible(ident, admin)
        if item['config']['format']=='elo':return self.elo.standings(ident)
        item['entries']=self.rows("SELECT e.team_id,t.name,e.accepted_ms,json_extract(e.snapshot,'$.version') AS version FROM competition_entries e JOIN teams t ON t.id=e.team_id WHERE e.competition_id=? ORDER BY e.team_id",(ident,))
        scores = {e["team_id"]: {**e, "played": 0, "wins": 0, "draws": 0, "losses": 0} for e in item["entries"]}
        for row in self.rows("SELECT * FROM competition_fixtures WHERE competition_id=? AND status='completed'", (ident,)):
            result = json.loads(row["result"])
            winner = result["winner"]
            for side in engine.SIDES:
                team = scores[row[side + "_team_id"]]
                team["played"] += 1
                outcome = "wins" if side == winner else "draws" if winner is None and result["reason"] != "double_startup_failure" else "losses"
                team[outcome] += 1
        scoring = item['config'].get('scoring', SCORING)
        for team in scores.values():
            team['point_breakdown'] = {'wins': team['wins'] * scoring['win'],
                                       'draws': team['draws'] * scoring['draw'],
                                       'losses': team['losses'] * scoring['loss']}
            team['points'] = sum(team['point_breakdown'].values())
        swiss = item["config"].get("format") == "swiss"
        if swiss:
            for bye in self.rows("SELECT team_id,round_number FROM competition_byes WHERE competition_id=?", (ident,)):
                team = scores[bye["team_id"]]
                # A bye is scored as winning the whole round: two games x 3 points.
                # It is listed separately and is not counted as played games.
                team["bye_round"] = bye["round_number"]
                team["bye_points"] = 2 * scoring["win"]
                team["points"] += team["bye_points"]
                team["point_breakdown"]["bye"] = team["bye_points"]
            opponents = {t:set() for t in scores}
            for fixture in self.rows("SELECT red_team_id,blue_team_id FROM competition_fixtures WHERE competition_id=? AND status='completed'", (ident,)):
                opponents[fixture["red_team_id"]].add(fixture["blue_team_id"])
            for t, team in scores.items():
                team["buchholz"] = sum(scores[o]["points"] for o in opponents[t])
        if not swiss:
            from arena_api.ranking import head_to_head
            head_to_head(scores, self.rows("SELECT * FROM competition_fixtures WHERE competition_id=? AND status='completed'", (ident,)), scoring)
        rows = sorted(scores.values(), key=lambda s: (-s["points"], -s.get("buchholz",0), -s.get("head_to_head",0), -s["wins"] if not swiss else 0, s["team_id"]))
        previous, rank = None, 0
        index = 0
        for row in rows:
            guest = self.one("SELECT is_guest FROM teams WHERE id=?", (row["team_id"],))["is_guest"]
            row["is_guest"] = bool(guest)
            if guest:
                row["rank"] = None
                continue
            index += 1
            key = (row["points"], row.get("buchholz",0)) if swiss else (row["points"], row["head_to_head"], row["wins"])
            if key != previous:
                rank = index
            row["rank"], previous = rank, key
        return {"competition_id": ident, "ranking": "points_buchholz" if swiss else "points_h2h_wins", "scoring": dict(scoring),
                "provisional": item["status"] != "completed", "items": rows}

    def attempts(self, ident, fixture_id):
        self.reconcile()
        with self.store.lock:
            self.fixture(ident, fixture_id)
            table,field=("elo_attempts","game_id") if self.get(ident)["config"]["format"]=="elo" else ("competition_attempts","fixture_id")
            return {"items":self.rows(f"SELECT * FROM {table} WHERE {field}=? ORDER BY number",(fixture_id,))}

    def attempt_job(self, ident, fixture_id, number):
        with self.store.lock:
            self.fixture(ident, fixture_id)
            table,field=("elo_attempts","game_id") if self.get(ident)["config"]["format"]=="elo" else ("competition_attempts","fixture_id")
            row = self.one(f"SELECT job_id FROM {table} WHERE {field}=? AND number=?", (fixture_id, number))
            if row is None:
                raise Fault("not_found", "Attempt not found")
            return self.store.get(row["job_id"], "competition:" + ident, "job")

    def visible(self, ident, admin=False):
        item = self.get(ident)
        if (item['status'] == 'draft' or item['archived_ms'] is not None) and not admin:
            raise Fault('not_found', 'Competition not found')
        return item

    def live_fixture(self, ident, fixture_id, admin=False):
        with self.store.lock:
            self.visible(ident, admin)
            return self.fixture(ident, fixture_id)

    def watch_state(self, ident, admin=False):
        """Public fixture progress only; never expose attempt IDs or bot reports."""
        with self.store.lock:
            item = self.visible(ident, admin)
            if item['config']['format']=='elo':
                rows=self.rows('SELECT g.*,r.competition_id FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=? ORDER BY r.started_ms,g.ordinal',(ident,))
            else:
                rows = self.rows('SELECT * FROM competition_fixtures WHERE competition_id=? ORDER BY ordinal', (ident,))
            return {'competition_id': ident, 'status': item['status'],
                    'fixtures': [self.fixture_view(row) for row in rows]}

    def safe_events(self, job_id, after):
        """Cursor advances across hidden events without revealing their data."""
        raw = self.store.events(job_id, after) if job_id else []
        fields = {"state": ("state", "counts", "ruleset", "events", "clocks"),
                  "turn_started": ("side", "turn_id", "budget_ms", "estimated_started_unix_ms", "clock_uncertainty_ms", "clocks"),
                  "timeout_pending": ("side", "grace_ms", "clocks"), "timeout_claimed": ("claimant", "by"),
                  "completed": ("result",), "aborted": (), "failed": ()}
        safe = []
        for event in raw:
            if event["type"] in fields:
                data = {k: event["data"][k] for k in fields[event["type"]] if k in event["data"]}
                if data.get("ruleset"):
                    data["ruleset"] = {k:v for k,v in data["ruleset"].items() if k not in ("engine_sha256", "sdk_sha256")}
                safe.append({**event, "data": data})
        return safe, raw[-1]["seq"] if raw else after
