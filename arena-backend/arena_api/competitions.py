"""Durable competition scheduling. SQL owns fixtures; the worker queue is a cache.

Practice documents remain private. Official jobs use a controller-only namespace
and explicitly record the owner of each locked submission.
"""
import hashlib
import json
import logging
import threading
import uuid

from arena import engine, sandbox, experimental
from arena.adjudication import CURRENT, LEGACY, policy, valid_result
from arena.config import BUILD_FLAGS, request_profile, stored_profile, worker_image
from arena.protocol import Fault
from arena.service import TERMINAL, digest, fixtures
from arena_api.accounts import now_ms

SCORING = {'win': 3, 'draw': 1, 'loss': 0}


class Competitions:
    def __init__(self, store, service, accounts):
        self.store, self.service, self.accounts = store, service, accounts
        self.db = store.db
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="competition-scheduler")
        self.scheduler_error = None
        self.suite_sha256 = digest(fixtures())
        with store.lock, self.db:
            # Restart never resumes a clock or automatically retries a match.
            for row in self.accounts.rows("SELECT id FROM competitions WHERE status='running'"):
                self.db.execute("UPDATE competitions SET status='paused',revision=revision+1 WHERE id=?", (row["id"],))
                self.accounts.audit("system", "competition.restart_paused", row["id"])
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
        # Resolving Docker may block. Never hold the SQLite/controller lock
        # during this external operation or require Docker for an exact retry.
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

    def create(self, actor, body, key):
        def prepare():
            snapshot = self.service.rulesets.resolve(body.get("ruleset_id")) if self.service.rulesets else None
            config = {"format": "double_round_robin", "ranking": "points_shared_rank", "scoring": dict(SCORING), "max_teams": body["max_teams"], "adjudication": dict(CURRENT),
                      "profile": request_profile(body).public(), "runtime": "cpp17", "protocol_version": 2,
                      "image_id": sandbox.image_id(worker_image(request_profile(body))), "engine_id": engine.ENGINE_ID, "engine_sha256": engine.ENGINE_SHA256,
                      "sdk_sha256": hashlib.sha256((sandbox.ROOT / "sdk" / "json.hpp").read_bytes()).hexdigest(),
                      "build_flags": BUILD_FLAGS, "suite_id": "ottv2-public-smoke-v1", "suite_sha256": self.suite_sha256}
            if snapshot:
                config.update(ruleset=snapshot,protocol_version=3,image_id=sandbox.image_id(worker_image(request_profile(body), True)),engine_id=experimental.ENGINE_ID,
                              engine_sha256=experimental.ENGINE_SHA256,sdk_sha256=experimental.sdk_hash(),suite_id='ottv2-experimental-smoke-v1',
                              suite_sha256=digest(fixtures(experimental.provider(snapshot))))
            return config
        def action(config):
            if self.db.execute("SELECT COUNT(*) FROM competitions").fetchone()[0] >= 100:
                raise Fault("quota_exceeded", "Local beta allows 100 competitions")
            ident = uuid.uuid4().hex
            self.db.execute("INSERT INTO competitions(id,title,status,config,created_ms) VALUES(?,?,'draft',?,?)",
                            (ident, body["title"], json.dumps(config), now_ms()))
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
            if requested != config['profile']:
                if item['status'] != 'draft' or entries:
                    raise Fault('admin_conflict', 'Time control can only change in an empty draft')
                if bool(requested.get('match_ms')) != bool(config['profile'].get('match_ms')):
                    raise Fault('admin_conflict', 'Create a new competition to switch from legacy turn clocks to match clocks')
                config['profile'] = requested
            config['max_teams'] = body['max_teams']
            self.db.execute('UPDATE competitions SET title=?,config=?,revision=revision+1 WHERE id=?', (body['title'],json.dumps(config),ident))
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
            self.db.execute("UPDATE competitions SET status=?,revision=revision+1 WHERE id=?", (target, ident))
            return self.get(ident)
        return self.command(actor, target + ":" + ident, key, {}, action)

    @staticmethod
    def check_runtime(config):
        policy(config.get("adjudication"))
        experimental.provider(config.get("ruleset"))
        expected_engine = experimental.ENGINE_SHA256 if config.get("ruleset") else engine.ENGINE_SHA256
        expected_sdk = experimental.sdk_hash() if config.get("ruleset") else hashlib.sha256((sandbox.ROOT / "sdk" / "json.hpp").read_bytes()).hexdigest()
        if (config["engine_sha256"] != expected_engine or config["build_flags"] != BUILD_FLAGS
                or config["sdk_sha256"] != expected_sdk
                or config["profile"] != stored_profile(config["profile"]).public()):
            raise Fault("provenance_mismatch", "Pinned competition runtime changed; create a new competition")

    def qualification(self, config, team, submission_id, benchmark_id):
        source = self.store.get(submission_id, team, "submission")
        job = self.store.get(benchmark_id, team, "job")
        report = job.get("report") or {}
        artifacts = report.get("artifacts") or []
        artifact = artifacts[0] if len(artifacts) == 1 else {}
        if (source.get("runtime") != "cpp17" or job.get("kind") != "benchmark" or job["status"] != "completed"
                or job.get("submission_ids") != [submission_id] or report.get("passed") is not True
                or (report.get("ruleset") or {}).get("hash") != (config.get("ruleset") or {}).get("hash")
                or report.get("error") or report.get("aborted") or len(report.get("samples", [])) != 72
                or (report.get("turn_wall_ms") or {}).get("samples") != 72
                or report.get("profile") != config["profile"] or report.get("image_id") != config["image_id"]
                or report.get("engine_sha256") != config["engine_sha256"] or report.get("suite_sha256") != config["suite_sha256"]
                or report.get("suite_id") != config["suite_id"] or artifact.get("source") != source.get("source")
                or artifact.get("image_id") != config["image_id"] or artifact.get("sdk_sha256") != config["sdk_sha256"]
                or artifact.get("flags") != config["build_flags"] or not artifact.get("artifact_sha256")):
            raise Fault("qualification_required", "A completed 72-sample benchmark under this exact competition profile/toolchain is required")
        return {"team_id": team, "submission_id": submission_id, "version": source["version"], "source": source["source"],
                "artifact_sha256": artifact["artifact_sha256"], "benchmark_id": benchmark_id, "benchmark_report_sha256": digest(report)}

    def enter(self, actor, team, ident, body, key):
        def action():
            item = self.get(ident)
            self.require_status(item, "open")
            count = self.db.execute("SELECT COUNT(*) FROM competition_entries WHERE competition_id=? AND team_id<>?", (ident, team)).fetchone()[0]
            if count >= item["config"]["max_teams"]:
                raise Fault("quota_exceeded", "Competition entry limit reached")
            snapshot = self.qualification(item["config"], team, body["submission_id"], body["benchmark_id"])
            self.db.execute("INSERT INTO competition_entries VALUES(?,?,?,?,?,?) ON CONFLICT(competition_id,team_id) DO UPDATE SET submission_id=excluded.submission_id, benchmark_id=excluded.benchmark_id, snapshot=excluded.snapshot, accepted_ms=excluded.accepted_ms",
                            (ident, team, body["submission_id"], body["benchmark_id"], json.dumps(snapshot), now_ms()))
            return {"id": ident, "team_id": team, "submission_id": body["submission_id"], "version": snapshot["version"],
                    "delegate_timeout_claims": True, "status": "entered"}
        return self.command(actor, "entry:" + ident, key, body, action)

    def lock(self, actor, ident, key):
        def action():
            item = self.get(ident)
            self.require_status(item, "open")
            self.check_runtime(item["config"])
            entries = self.rows("SELECT * FROM competition_entries WHERE competition_id=? ORDER BY team_id", (ident,))
            if len(entries) < 2:
                raise Fault("competition_conflict", "At least two qualified entries are required")
            for entry in entries:
                account = self.one("SELECT locked,must_change FROM accounts WHERE team_id=?", (entry["team_id"],))
                if not account or account["locked"] or account["must_change"]:
                    raise Fault("competition_conflict", "All entered teams must have active accounts at lock time")
                current = self.qualification(item["config"], entry["team_id"], entry["submission_id"], entry["benchmark_id"])
                if current != json.loads(entry["snapshot"]):
                    raise Fault("provenance_mismatch", "Entry qualification changed; team must enter again")
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
        row = self.one("SELECT * FROM competition_fixtures WHERE competition_id=? AND id=?", (ident, fixture_id))
        if row is None:
            raise Fault("not_found", "Fixture not found")
        return row

    def retry(self, actor, ident, fixture_id, key):
        self.reconcile()
        def action():
            self.require_status(self.get(ident), "locked", "running", "paused")
            row = self.fixture(ident, fixture_id)
            if row["status"] != "attention":
                raise Fault("competition_conflict", "Only an unscored failed/aborted attempt can be retried")
            number = self.db.execute("SELECT COUNT(*) FROM competition_attempts WHERE fixture_id=?", (fixture_id,)).fetchone()[0]
            self.db.execute("UPDATE competition_fixtures SET status='pending' WHERE id=?", (fixture_id,))
            return {"id": fixture_id, "status": "pending", "next_attempt": number + 1}
        return self.command(actor, "retry:" + fixture_id, key, {}, action)

    def reconcile(self):
        with self.store.lock, self.db:
            for row in self.rows("SELECT * FROM competition_fixtures WHERE status='queued'"):
                job = self.store.get(row["current_job_id"], "competition:" + row["competition_id"], "job")
                if job["status"] not in TERMINAL:
                    continue
                report = job.get("report") or {}
                result = report.get("result")
                config = self.get(row["competition_id"])["config"]
                rules = policy(config.get("adjudication"))
                valid = (job["status"] == "completed" and report.get("eligible_for_tournament") is True
                         and not report.get("error") and not report.get("aborted") and isinstance(result, dict)
                         and valid_result(result, rules, experimental=bool(config.get("ruleset"))) and report.get("adjudication", LEGACY) == rules
                         and (report.get("final_state") or {}).get("result") == result
                         and (report.get("ruleset") or {}).get("hash") == (config.get("ruleset") or {}).get("hash")
                         and report.get("engine_sha256") == config["engine_sha256"]
                         and report.get("image_id") == config["image_id"] and report.get("profile") == config["profile"])
                status = "completed" if valid else "attention"
                error = None if valid else (job.get("error") or report.get("error") or {}).get("code", "unscored_attempt")
                self.db.execute("UPDATE competition_fixtures SET status=?,result=? WHERE id=?", (status, json.dumps(result) if valid else None, row["id"]))
                self.db.execute("UPDATE competition_attempts SET status=?,error_code=? WHERE job_id=?", (status, error, job["id"]))
                self.accounts.audit("system", "competition.result" if valid else "competition.attention", row["id"])
            for row in self.rows("SELECT id FROM competitions WHERE status IN ('running','paused')"):
                remaining = self.db.execute("SELECT COUNT(*) FROM competition_fixtures WHERE competition_id=? AND status<>'completed'", (row["id"],)).fetchone()[0]
                if remaining == 0:
                    self.db.execute("UPDATE competitions SET status='completed',completed_ms=?,revision=revision+1 WHERE id=?", (now_ms(), row["id"]))
                    self.accounts.audit("system", "competition.completed", row["id"])

    def tick(self):
        self.reconcile()
        with self.store.lock:
            if self.stop.is_set() or self.service.stop.is_set() or self.service.pending.full():
                return
            # At most one official attempt outstanding globally. Fixtures stay
            # in SQLite, so a large round robin cannot overflow the RAM queue.
            if self.db.execute("SELECT 1 FROM competition_fixtures WHERE status='queued' LIMIT 1").fetchone():
                return
            row = self.one("SELECT f.* FROM competition_fixtures f JOIN competitions c ON c.id=f.competition_id WHERE f.status='pending' AND c.status='running' ORDER BY c.created_ms,c.id,f.ordinal LIMIT 1")
            if row is None:
                return
            item = self.get(row["competition_id"])
            config = item["config"]
            self.check_runtime(config)
            entries = [json.loads(self.one("SELECT snapshot FROM competition_entries WHERE competition_id=? AND team_id=?", (item["id"], row[side + "_team_id"]))["snapshot"]) for side in engine.SIDES]
            number = self.db.execute("SELECT COUNT(*)+1 FROM competition_attempts WHERE fixture_id=?", (row["id"],)).fetchone()[0]
            job = {"id": uuid.uuid4().hex, "team_id": "competition:" + item["id"], "created_unix_ms": now_ms(),
                   "kind": "match", "status": "queued", "submission_ids": [e["submission_id"] for e in entries],
                   "profile": config["profile"], "image_id": config["image_id"], "engine_id": config["engine_id"],
                   "engine_sha256": config["engine_sha256"], "report": None, "error": None,
                   **({"ruleset":config["ruleset"],"protocol_version":3} if config.get("ruleset") else {}),
                   "official": {"competition_id": item["id"], "fixture_id": row["id"], "attempt": number,
                                "entries": entries, **({"ruleset":config["ruleset"]} if config.get("ruleset") else {}), "engine_sha256": config["engine_sha256"],
                                "sdk_sha256": config["sdk_sha256"], "build_flags": config["build_flags"],
                                "adjudication": policy(config.get("adjudication"))}}
            with self.db:
                self.db.execute("INSERT INTO documents VALUES(?,?,?,?)", (job["id"], "job", job["team_id"], json.dumps(job)))
                self.db.execute("UPDATE competition_fixtures SET status='queued',current_job_id=? WHERE id=?", (job["id"], row["id"]))
                self.db.execute("INSERT INTO competition_attempts VALUES(?,?,?,'queued',NULL,?)", (row["id"], number, job["id"], now_ms()))
                event = {"seq": 1, "type": "queued", "server_unix_ms": now_ms(), "data": {"kind": "match"}}
                self.db.execute("INSERT INTO events VALUES(?,?,?)", (job["id"], 1, json.dumps(event)))
            # Practice enqueue also holds store.lock; the only other queue
            # operation here is dequeue. A slot cannot be stolen after commit.
            self.service.pending.put_official(job["id"])

    def loop(self):
        while not self.stop.wait(0.2):
            self.schedule_once()

    def schedule_once(self):
        # Called by both scheduler and worker before selecting its next job.
        # Reconcile/dispatch immediately after a game so practice cannot slip
        # ahead of the next official fixture between periodic scheduler ticks.
        try:
            self.tick()
            self.scheduler_error = None
        except Exception:
            if self.scheduler_error is None:
                logging.getLogger("arena.competitions").exception("Competition scheduler failed")
            self.scheduler_error = "scheduler_error"

    def listing(self, admin=False, limit=50, offset=0):
        self.reconcile()
        with self.store.lock:
            where = "" if admin else " WHERE status<>'draft' AND archived_ms IS NULL"
            total = self.db.execute("SELECT COUNT(*) FROM competitions" + where).fetchone()[0]
            items = self.rows("SELECT id FROM competitions" + where + " ORDER BY created_ms DESC,id LIMIT ? OFFSET ?", (limit, offset))
            return {"items": [self.get(r["id"]) for r in items], "total": total, "limit": limit, "offset": offset}

    def view(self, ident, admin=False):
        self.reconcile()
        with self.store.lock:
            item = self.get(ident)
            if (item["status"] == "draft" or item["archived_ms"] is not None) and not admin:
                raise Fault("not_found", "Competition not found")
            entries = self.rows("SELECT e.*,t.name FROM competition_entries e JOIN teams t ON t.id=e.team_id WHERE e.competition_id=? ORDER BY e.team_id", (ident,))
            # Deliberate whitelist: no contact info, binary/build log, benchmark
            # report or private worker job ID in the shared competition view.
            item["entries"] = [{"team_id": e["team_id"], "name": e["name"], "version": json.loads(e["snapshot"])["version"], "accepted_ms": e["accepted_ms"]} for e in entries]
            counts = {r["status"]: r["n"] for r in self.rows("SELECT status,COUNT(*) n FROM competition_fixtures WHERE competition_id=? GROUP BY status", (ident,))}
            item["fixture_counts"] = {s: counts.get(s, 0) for s in ("pending", "queued", "completed", "attention")}
            return item

    @staticmethod
    def fixture_view(row):
        return {"id": row["id"], "competition_id": row["competition_id"], "ordinal": row["ordinal"],
                "red_team_id": row["red_team_id"], "blue_team_id": row["blue_team_id"], "status": row["status"],
                "result": json.loads(row["result"]) if row["result"] else None}

    def schedule(self, ident, admin=False, limit=50, offset=0):
        self.view(ident, admin)
        with self.store.lock:
            total = self.db.execute("SELECT COUNT(*) FROM competition_fixtures WHERE competition_id=?", (ident,)).fetchone()[0]
            rows = self.rows("SELECT * FROM competition_fixtures WHERE competition_id=? ORDER BY ordinal LIMIT ? OFFSET ?", (ident, limit, offset))
            return {"items": [self.fixture_view(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def standings(self, ident, admin=False):
        with self.store.lock:
            item = self.view(ident, admin)
            scores = {e["team_id"]: {**e, "played": 0, "wins": 0, "draws": 0, "losses": 0} for e in item["entries"]}
            for row in self.rows("SELECT * FROM competition_fixtures WHERE competition_id=? AND status='completed'", (ident,)):
                result = json.loads(row["result"])
                winner = result["winner"]
                for side in engine.SIDES:
                    team = scores[row[side + "_team_id"]]
                    team["played"] += 1
                    outcome = "wins" if side == winner else "draws" if winner is None and result["reason"] != "double_startup_failure" else "losses"
                    team[outcome] += 1
            for team in scores.values():
                team['point_breakdown'] = {'wins': team['wins'] * SCORING['win'],
                                           'draws': team['draws'] * SCORING['draw'],
                                           'losses': team['losses'] * SCORING['loss']}
                team['points'] = sum(team['point_breakdown'].values())
            rows = sorted(scores.values(), key=lambda s: (-s["points"], s["team_id"]))
            previous, rank = None, 0
            for index, row in enumerate(rows, 1):
                if row["points"] != previous:
                    rank = index
                row["rank"], previous = rank, row["points"]
            return {"competition_id": ident, "ranking": "points_shared_rank", "scoring": dict(SCORING),
                    "provisional": item["status"] != "completed", "items": rows}

    def attempts(self, ident, fixture_id):
        self.reconcile()
        with self.store.lock:
            self.fixture(ident, fixture_id)
            return {"items": self.rows("SELECT * FROM competition_attempts WHERE fixture_id=? ORDER BY number", (fixture_id,))}

    def attempt_job(self, ident, fixture_id, number):
        with self.store.lock:
            self.fixture(ident, fixture_id)
            row = self.one("SELECT job_id FROM competition_attempts WHERE fixture_id=? AND number=?", (fixture_id, number))
            if row is None:
                raise Fault("not_found", "Attempt not found")
            return self.store.get(row["job_id"], "competition:" + ident, "job")

    def live_fixture(self, ident, fixture_id, admin=False):
        self.view(ident, admin)
        with self.store.lock:
            return self.fixture(ident, fixture_id)

    def watch_state(self, ident, admin=False):
        """Public fixture progress only; never expose attempt IDs or bot reports."""
        with self.store.lock:
            item = self.view(ident, admin)
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
                safe.append({**event, "data": {k: event["data"][k] for k in fields[event["type"]] if k in event["data"]}})
        return safe, raw[-1]["seq"] if raw else after
