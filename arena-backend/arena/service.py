"""Hub service: submissions, the durable job queue and leases for remote graders.

The hub never runs a bot. Graders (arena.grader) lease jobs over HTTP, stream events back and
submit a report guarded by a fencing token; the hub replays the moves before accepting it
(arena.verify). A lease that is not renewed expires: the job goes back to the queue, or an
official fixture gets a new attempt (Competitions.reconcile).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid

from arena import engine, experimental, basic_rules
from arena.adjudication import CURRENT
from arena.job_queue import DurableJobQueue
from arena.archive import unpack
from arena.config import profile, request_profile, stored_profile, scale_profile
from arena.protocol import Fault
from arena.runner import TERMINAL, digest, engine_for, fixtures, summary  # noqa: F401 (re-exported)
from arena.verify import verify

MAX_QUEUED_PER_TEAM = 4
PLATFORMS = ("linux", "windows")
RUNTIME_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ONLINE_MS = 20_000
# Grader-reported event types the hub accepts; terminal events come only from the result call.
EVENT_TYPES = {"phase", "build", "state", "turn_started", "timeout_pending", "timeout_claimed", "progress",
               "infrastructure_retry"}
MAX_EVENTS_PER_CALL = 600


def now_ms():
    return time.time_ns() // 1_000_000


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def queue_slot(job):
    """Waiting jobs of one team that serve the same purpose share a slot (see Service.enqueue).

    Benchmarks for one competition, or for one clock/rules combination, share a slot; all
    practice matches of a team share one slot.
    """
    if job.get("kind") != "benchmark":
        return "practice"
    if job.get("competition_id"):
        return "benchmark:competition:" + job["competition_id"]
    return "benchmark:" + "|".join(((job.get("profile") or {}).get("id", ""), (job.get("ruleset") or {}).get("id") or "",
                                     (job.get("basic_rules") or {}).get("hash") or ""))


class GraderDefaults:
    lease_seconds, heartbeat_seconds, max_attempts, long_poll_seconds = 30, 5, 3, 25


class Service:
    def __init__(self, store, start_worker=True, workers=None, grader=None, artifacts=None):
        self.store = store
        self.rulesets = None
        self.competitions = None
        self.grader = grader or GraderDefaults()
        # Anchor lease timestamps once: wall-clock corrections must not shorten a live lease.
        self.lease_epoch_ms = now_ms() - time.monotonic_ns() // 1_000_000
        self.artifacts = Path(artifacts or store.root / "grader-artifacts")
        with store.lock, store.db:
            store.db.executescript("""
              CREATE TABLE IF NOT EXISTS graders(id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                created_ms INTEGER NOT NULL, created_by TEXT NOT NULL, revoked_ms INTEGER, platform TEXT, backend TEXT,
                runtime_id TEXT, slots INTEGER, version TEXT, hello_ms INTEGER, last_seen_ms INTEGER,
                jobs_done INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS grader_leases(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, grader_id TEXT NOT NULL,
                fencing INTEGER NOT NULL UNIQUE, created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','completed','expired','released','revoked','rejected')),
                ended_ms INTEGER, reason TEXT);
              CREATE INDEX IF NOT EXISTS grader_leases_active ON grader_leases(status, expires_ms);
              CREATE TABLE IF NOT EXISTS builds(submission_id TEXT NOT NULL, platform TEXT NOT NULL, runtime_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL, grader_id TEXT, created_ms INTEGER NOT NULL,
                PRIMARY KEY(submission_id, platform, runtime_id));
            """)
        self.pending = DurableJobQueue(store)
        self.before_next = None
        self.stop = threading.Event()
        self.started_workers = start_worker
        self.reaper_thread = threading.Thread(target=self.reap_loop, daemon=True, name="lease-reaper")
        if start_worker:
            self.reaper_thread.start()

    # ---- compatibility for dashboards ---------------------------------------------------
    @property
    def worker_count(self):
        """Grader slots online now (at least 1, so the scheduler keeps one official job ready)."""
        return max(1, sum(g["slots"] or 0 for g in self.graders(online_only=True)))

    @property
    def active_jobs(self):
        with self.store.lock:
            rows = self.store.db.execute("SELECT grader_id, job_id FROM grader_leases WHERE status='active'").fetchall()
        return {grader + ":" + job: job for grader, job in rows}

    @property
    def active(self):
        return next(iter(self.active_jobs.values()), None)

    def join(self):
        if self.reaper_thread.is_alive():
            self.reaper_thread.join()

    def health(self):
        online = self.graders(online_only=True)
        platforms = {}
        for g in online:
            item = platforms.setdefault(g["platform"], {"graders": 0, "slots": 0, "runtime_ids": []})
            item["graders"] += 1
            item["slots"] += g["slots"] or 0
            if g["runtime_id"] not in item["runtime_ids"]:
                item["runtime_ids"].append(g["runtime_id"])
        # The hub is ready without any grader: jobs wait in the queue until one connects.
        return {"ready": True, "runtime": "remote-graders", "graders_online": len(online),
                "grader_slots": sum(g["slots"] or 0 for g in online), "platforms": platforms, "codon_enabled": False}

    # ---- submissions --------------------------------------------------------------------
    def submit(self, team, version, runtime, raw, key, protocol_version=2):
        if type(protocol_version) is not int or protocol_version not in (2, 3):
            raise Fault('unsupported_protocol', 'Chỉ hỗ trợ SDK v2 (OTTv2) hoặc v3 (Experimental)')
        if runtime not in ("cpp17", "c11", "python3"):
            raise Fault("unsupported_runtime", "Choose cpp17, c11 or python3")
        if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,24})?", version):
            raise Fault("invalid_request", "Expected semantic version, e.g. v1.0.0")
        source_hash = hashlib.sha256(raw).hexdigest()
        operation_hash = digest({"version": version, "runtime": runtime, "zip_sha256": source_hash,
                                 **({'protocol_version': 3} if protocol_version == 3 else {})})

        def check():
            if len(self.store.list("submission", team)) >= 20:
                raise Fault("quota_exceeded", "Local beta allows 20 submissions per team")
            if any(s["version"] == version for s in self.store.list("submission", team)):
                raise Fault("version_conflict", "Version already exists; submit a new version")

        def prepare():
            with self.store.lock:
                check()
            directory = self.store.root / "sources" / uuid.uuid4().hex
            manifest = unpack(raw, directory, profile(), runtime)
            return directory, manifest

        def create(prepared):
            check()
            directory, manifest = prepared
            return self.store.create("submission", team, {"version": version, "runtime": runtime,
                "protocol_version": protocol_version, "source_path": str(directory), "source": manifest, "artifact": None})
        return self.store.deduplicate(team, "submission", key, operation_hash, create, prepare)

    def public_submission(self, item):
        return {k: v for k, v in item.items() if k != "source_path"}

    def enqueue(self, team, kind, body, key):
        if kind not in ("benchmark", "match"):
            raise Fault("invalid_request", "Unknown job kind")
        # Practice runs on any grader; a competition's benchmark must use its platform and toolchain.
        platform = runtime_id = None
        try:
            ruleset_id = body.get('ruleset_id')
            p = request_profile(body)
            pack = basic_rules.validated_snapshot(body["basic_rules"]) if body.get("basic_rules") else None
            if body.get("competition_id"):
                config = self.competitions.view(body["competition_id"])["config"]
                platform, runtime_id = config.get("grader_platform", "linux"), config.get("runtime_id") or config.get("image_id")
                pack = config.get("basic_rules")
                pinned_ruleset = (config.get('ruleset') or {}).get('id')
                if ruleset_id and ruleset_id != pinned_ruleset:
                    raise ValueError("Use the competition's pinned ruleset")
                ruleset_id = pinned_ruleset
                if (body.get("match_ms") is not None or body.get("turn_ms") is not None) and p.public() != config["profile"]:
                    raise ValueError("Use the competition's pinned time control")
                p = stored_profile(config["profile"])
            elif pack:
                d = pack["definition"]
                p = scale_profile(p, d["rows"] * d["cols"])
            if pack and ruleset_id:
                raise ValueError("Basic v2 rules cannot combine with SDK v3 add-ons")
        except ValueError as exc:
            raise Fault("invalid_request", str(exc)) from exc
        if kind == "benchmark":
            if set(body) - {"submission_id", "turn_ms", "match_ms", "ruleset_id", "basic_rules", "competition_id"}:
                raise Fault("invalid_request", "Unexpected benchmark fields")
            ids = [body.get("submission_id")]
        else:
            if set(body) - {"red_submission_id", "blue_submission_id", "sample_bot_id", "sample_side", "turn_ms", "match_ms", "delegate_timeout_claims", "ruleset_id", "basic_rules", "competition_id"}:
                raise Fault("invalid_request", "Unexpected match fields")
            if body.get("delegate_timeout_claims") is not True:
                raise Fault("invalid_request", "Both practice bots must delegate timeout claims to the server")
            ids = [body.get("red_submission_id"), body.get("blue_submission_id")]
        sample, sample_index = None, None
        if body.get("sample_bot_id"):
            if body.get("blue_submission_id"):
                raise Fault("invalid_request", "Choose one opponent")
            if body.get("sample_side", "blue") not in ("red", "blue"):
                raise Fault("invalid_request", "sample_side must be red or blue")
            sample = self.store.get(body["sample_bot_id"], kind="sample_bot")
            if not sample["enabled"]:
                raise Fault("not_found", "Sample bot not found")
            # The team's bot arrives as red_submission_id; sample_side decides the colours.
            sample_index = 0 if body.get("sample_side") == "red" else 1
            ids = [sample["submission_id"], ids[0]] if sample_index == 0 else [ids[0], sample["submission_id"]]
        elif body.get("sample_side"):
            raise Fault("invalid_request", "sample_side requires sample_bot_id")
        for index, ident in enumerate(ids):
            if not isinstance(ident, str):
                raise Fault("invalid_request", "Missing submission ID")
            self.store.get(ident, sample["owner"] if sample and index == sample_index else team, "submission")
        slot = queue_slot({"kind": kind, "profile": p.public(), "competition_id": body.get("competition_id"),
                           "basic_rules": pack, "ruleset": {"id": ruleset_id} if ruleset_id else None})

        def check(replaced=0):
            jobs = self.store.list("job", team)
            if len(jobs) >= 200:
                raise Fault("quota_exceeded", "Local beta allows 200 jobs per team")
            active = [j for j in jobs if j["status"] not in (*TERMINAL, "queued")]
            if len(active) >= 4:
                raise Fault("quota_exceeded", "At most four running jobs per team")
            if sum(j["status"] == "queued" for j in jobs) - replaced >= MAX_QUEUED_PER_TEAM:
                raise Fault("quota_exceeded", f"At most {MAX_QUEUED_PER_TEAM} waiting jobs per team; wait for one to start")

        def prepare():
            with self.store.lock:
                check(replaced=len(waiting_in_slot()))
            snapshot = self.rulesets.resolve(ruleset_id) if self.rulesets else None
            if ruleset_id and not snapshot:
                raise Fault('invalid_request', 'Experimental catalog is unavailable')
            if snapshot and any(self.store.get(ident).get('protocol_version') != 3 for ident in ids):
                raise Fault('unsupported_protocol', 'Experimental cần bot SDK v3. Nộp một version mới và chọn SDK Experimental khi tải ZIP lên.')
            return snapshot

        def waiting_in_slot():
            return [j for j in self.store.list("job", team) if j["status"] == "queued" and queue_slot(j) == slot]

        def create(snapshot):
            waiting = waiting_in_slot()
            identity = (ids, (sample or {}).get("id"), p.public()["id"], (pack or {}).get("hash"), (snapshot or {}).get("hash"))
            for old in waiting:
                if (old["submission_ids"], (old.get("sample_bot") or {}).get("id"), old["profile"]["id"],
                        (old.get("basic_rules") or {}).get("hash"), (old.get("ruleset") or {}).get("hash")) == identity:
                    # Same request is already waiting: keep its place instead of re-queuing.
                    return {**old, "queue_position": self.pending.position(old["id"])}
            check(replaced=len(waiting))
            for old in waiting:
                # Same purpose (same competition / clock / rules) and not started yet: only the
                # newest request is useful, so the older one gives its place up. Running jobs
                # and requests for other competitions or settings are never touched.
                self.pending.remove(old["id"])
                self.store.update(old["id"], status="aborted", error={"code": "superseded", "detail": "Replaced by the team's newer request for the same purpose before it started"})
                self.store.event(old["id"], "aborted", {"code": "superseded"})
            job = self.store.create("job", team, {"kind": kind, "status": "queued", "submission_ids": ids,
                "profile": p.public(), "requires": {"platform": platform, "runtime_id": runtime_id},
                "engine_id": engine.ENGINE_ID, "engine_sha256": engine.ENGINE_SHA256, "adjudication": dict(CURRENT),
                "report": None, "error": None,
                **({"competition_id": body['competition_id']} if body.get('competition_id') else {}),
                **({"basic_rules": pack} if pack else {}),
                **({"sample_bot": {"id": sample["id"], "owner": sample["owner"], "submission_id": sample["submission_id"],
                                   "side": engine.SIDES[sample_index]}} if sample else {}),
                **({"ruleset": snapshot, "protocol_version": 3, "engine_id": experimental.ENGINE_ID, "engine_sha256": experimental.ENGINE_SHA256} if snapshot else {})})
            self.store.event(job["id"], "queued", {"kind": kind})
            self.pending.put(job["id"], kind=kind, platform=platform, runtime_id=runtime_id)
            return {**job, "queue_position": self.pending.position(job["id"])}
        return self.store.deduplicate(team, kind, key, digest(body), create, prepare)

    # ---- grader registry ----------------------------------------------------------------
    def create_grader(self, name, actor):
        name = (name or "").strip()
        if not 1 <= len(name) <= 60 or any(ord(c) < 32 for c in name):
            raise Fault("invalid_request", "Tên máy chấm 1..60 ký tự")
        token = "uag_" + secrets.token_urlsafe(32)
        ident = uuid.uuid4().hex
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO graders(id,name,token_hash,created_ms,created_by) VALUES(?,?,?,?,?)",
                                  (ident, name, token_hash(token), now_ms(), actor))
        return {**self.grader_view(self.grader_row(ident)), "token": token, "token_visibility": "this_response_only"}

    def revoke_grader(self, ident, actor):
        with self.store.lock, self.store.db:
            row = self.grader_row(ident)
            if row["revoked_ms"] is None:
                self.store.db.execute("UPDATE graders SET revoked_ms=? WHERE id=?", (now_ms(), ident))
            leases = self.store.db.execute("SELECT id FROM grader_leases WHERE grader_id=? AND status='active'", (ident,)).fetchall()
        for (lease_id,) in leases:
            self.end_lease(lease_id, "revoked", "grader_revoked")
        return self.grader_view(self.grader_row(ident))

    def grader_row(self, ident):
        with self.store.lock:
            cursor = self.store.db.execute("SELECT * FROM graders WHERE id=?", (ident,))
            row = cursor.fetchone()
            if not row:
                raise Fault("not_found", "Grader not found")
            return dict(zip([c[0] for c in cursor.description], row))

    def grader_view(self, row):
        online = row["revoked_ms"] is None and row["last_seen_ms"] is not None and now_ms() - row["last_seen_ms"] < ONLINE_MS
        with self.store.lock:
            running = self.store.db.execute("SELECT COUNT(*) FROM grader_leases WHERE grader_id=? AND status='active'", (row["id"],)).fetchone()[0]
        return {k: row[k] for k in ("id", "name", "created_ms", "revoked_ms", "platform", "backend", "runtime_id", "slots",
                                    "version", "hello_ms", "last_seen_ms", "jobs_done")} | {"online": online, "running": running}

    def graders(self, online_only=False):
        with self.store.lock:
            cursor = self.store.db.execute("SELECT * FROM graders ORDER BY created_ms DESC")
            keys = [c[0] for c in cursor.description]
            rows = [dict(zip(keys, r)) for r in cursor.fetchall()]
        views = [self.grader_view(r) for r in rows]
        return [v for v in views if v["online"]] if online_only else views

    def runtime_for(self, platform):
        """runtime_id of the newest grader seen for a platform (pinned when a competition is created)."""
        with self.store.lock:
            row = self.store.db.execute("SELECT runtime_id FROM graders WHERE platform=? AND revoked_ms IS NULL AND runtime_id IS NOT NULL "
                                        "ORDER BY last_seen_ms DESC LIMIT 1", (platform,)).fetchone()
        return row[0] if row else None

    def authenticate(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 128:
            raise Fault("unauthorized", "Grader token required")
        with self.store.lock:
            cursor = self.store.db.execute("SELECT * FROM graders WHERE token_hash=?", (token_hash(token),))
            row = cursor.fetchone()
            if not row:
                raise Fault("unauthorized", "Unknown grader token")
            grader = dict(zip([c[0] for c in cursor.description], row))
            if grader["revoked_ms"] is not None:
                raise Fault("unauthorized", "Grader token revoked")
            with self.store.db:
                self.store.db.execute("UPDATE graders SET last_seen_ms=? WHERE id=?", (now_ms(), grader["id"]))
        return grader

    def hello(self, grader, info):
        platform, runtime_id = info.get("platform"), info.get("runtime_id")
        if platform not in PLATFORMS or not isinstance(runtime_id, str) or not RUNTIME_ID.match(runtime_id):
            raise Fault("invalid_request", "platform must be linux|windows and runtime_id sha256:<64 hex>")
        slots, backend, version = info.get("slots"), info.get("backend"), info.get("version")
        if type(slots) is not int or not 1 <= slots <= 64 or backend not in ("docker", "bwrap", "windows") or not isinstance(version, str) or len(version) > 40:
            raise Fault("invalid_request", "slots 1..64, backend docker|bwrap|windows, version <= 40 chars")
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE graders SET platform=?,backend=?,runtime_id=?,slots=?,version=?,hello_ms=?,last_seen_ms=? WHERE id=?",
                                  (platform, backend, runtime_id, slots, version, now_ms(), now_ms(), grader["id"]))
            # A restarted grader cannot still be running its old leases.
            leases = self.store.db.execute("SELECT id FROM grader_leases WHERE grader_id=? AND status='active'", (grader["id"],)).fetchall()
        for (lease_id,) in leases:
            self.end_lease(lease_id, "released", "grader_restarted")
        return {"grader_id": grader["id"], "name": grader["name"], "lease_seconds": self.grader.lease_seconds,
                "heartbeat_seconds": self.grader.heartbeat_seconds, "long_poll_seconds": self.grader.long_poll_seconds,
                "hub_time_ms": now_ms()}

    # ---- leases -------------------------------------------------------------------------
    def lease_now_ms(self):
        return self.lease_epoch_ms + time.monotonic_ns() // 1_000_000

    def try_lease(self, grader):
        """Claim the best eligible queued job for this grader, or None."""
        if self.before_next:
            self.before_next()
        if not grader.get("platform"):
            raise Fault("invalid_request", "Call /grader/hello first")
        with self.store.lock, self.store.db:
            ident = self.pending.claim(grader["platform"], grader["runtime_id"])
            if ident is None:
                return None
            job = self.store.get(ident)
            fencing = self.store.db.execute("SELECT COALESCE(MAX(fencing),0)+1 FROM grader_leases").fetchone()[0]
            lease_id = uuid.uuid4().hex
            created = self.lease_now_ms()
            expires = created + self.grader.lease_seconds * 1000
            self.store.db.execute("INSERT INTO grader_leases(id,job_id,grader_id,fencing,created_ms,expires_ms,status) VALUES(?,?,?,?,?,?,'active')",
                                  (lease_id, ident, grader["id"], fencing, created, expires))
            job = self.store.update(ident, status="starting", lease_id=lease_id, fencing=fencing, lease_until_ms=expires,
                                    worker=grader["name"], grader_id=grader["id"],
                                    runtime={"platform": grader["platform"], "runtime_id": grader["runtime_id"], "backend": grader["backend"]},
                                    leases=job.get("leases", 0) + 1)
            self.store.event(ident, "leased", {"grader": grader["name"], "platform": grader["platform"], "fencing": fencing})
            submissions = [self.lease_submission(job, index, ident_, grader) for index, ident_ in enumerate(job["submission_ids"])]
        return {"lease_id": lease_id, "fencing": fencing, "expires_ms": expires, "heartbeat_seconds": self.grader.heartbeat_seconds,
                "job": {k: v for k, v in job.items() if k not in ("report", "error")}, "submissions": submissions}

    def lease_submission(self, job, index, ident, grader):
        official = job.get("official")
        if official:
            owner = official["entries"][index]["team_id"]
        elif job.get("sample_bot") and engine.SIDES[index] == job["sample_bot"].get("side", "blue"):
            owner = job["sample_bot"]["owner"]
        else:
            owner = job["team_id"]
        item = self.store.get(ident, owner, "submission")
        build = self.store.db.execute("SELECT artifact_sha256 FROM builds WHERE submission_id=? AND platform=? AND runtime_id=?",
                                      (ident, grader["platform"], grader["runtime_id"])).fetchone()
        required = official["entries"][index]["artifact_sha256"] if official else None
        artifact = required or (build[0] if build and (self.artifacts / grader["platform"] / build[0]).is_file() else None)
        return {"id": ident, "team_id": owner, "version": item.get("version"), "runtime": item.get("runtime", "cpp17"),
                "protocol_version": item.get("protocol_version", 2), "source": item["source"],
                "source_url": f"/api/v1/grader/sources/{ident}",
                "artifact_sha256": artifact, "artifact_required": bool(required),
                "artifact_url": f"/api/v1/grader/artifacts/{grader['platform']}/{artifact}" if artifact else None}

    def active_lease(self, grader, lease_id, fencing):
        with self.store.lock:
            row = self.store.db.execute("SELECT job_id,grader_id,fencing,status,expires_ms FROM grader_leases WHERE id=?", (lease_id,)).fetchone()
        if not row or row[1] != grader["id"]:
            raise Fault("not_found", "Lease not found")
        if row[3] != "active" or row[2] != fencing or row[4] < self.lease_now_ms():
            raise Fault("stale_lease", "Lease is no longer held by this grader; stop the job")
        return row[0]

    def heartbeat(self, grader, lease_id, fencing):
        with self.store.lock, self.store.db:
            job_id = self.active_lease(grader, lease_id, fencing)
            expires = self.lease_now_ms() + self.grader.lease_seconds * 1000
            self.store.db.execute("UPDATE grader_leases SET expires_ms=? WHERE id=?", (expires, lease_id))
            self.store.update(job_id, lease_until_ms=expires, heartbeat_ms=self.lease_now_ms())
        return {"ok": True, "expires_ms": expires}

    def post_events(self, grader, lease_id, fencing, events):
        job_id = self.active_lease(grader, lease_id, fencing)
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_CALL:
            raise Fault("invalid_request", f"events must be a list of at most {MAX_EVENTS_PER_CALL}")
        for item in events:
            if not isinstance(item, dict) or item.get("type") not in EVENT_TYPES or not isinstance(item.get("data"), dict):
                raise Fault("invalid_request", "Unknown or malformed event")
        with self.store.lock:
            for item in events:
                if item["type"] == "phase":
                    phase = item["data"].get("phase")
                    if phase not in ("building", "validating", "benchmarking", "playing"):
                        raise Fault("invalid_request", "Unknown phase")
                    self.store.update(job_id, status=phase)
                self.store.event(job_id, item["type"], item["data"])
        return {"ok": True, "accepted": len(events)}

    def store_artifact(self, grader, lease_id, fencing, submission_id, sha256, data):
        job_id = self.active_lease(grader, lease_id, fencing)
        job = self.store.get(job_id)
        if submission_id not in job["submission_ids"] or not SHA256.match(sha256 or ""):
            raise Fault("invalid_request", "Artifact is not part of this lease")
        if hashlib.sha256(data).hexdigest() != sha256 or not data or len(data) > 16 * 1024 * 1024:
            raise Fault("invalid_request", "Artifact SHA-256 or size mismatch")
        folder = self.artifacts / grader["platform"]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / sha256
        if not target.is_file():
            partial = folder / (sha256 + "." + uuid.uuid4().hex + ".part")
            partial.write_bytes(data)
            partial.replace(target)
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO builds VALUES(?,?,?,?,?,?)",
                                  (submission_id, grader["platform"], grader["runtime_id"], sha256, grader["id"], now_ms()))
        return {"ok": True, "artifact_sha256": sha256}

    def artifact_path(self, grader, platform, sha256):
        if platform not in PLATFORMS or not SHA256.match(sha256 or ""):
            raise Fault("not_found", "Artifact not found")
        path = self.artifacts / platform / sha256
        if not path.is_file():
            raise Fault("not_found", "Artifact not found")
        return path

    def source_archive(self, grader, submission_id):
        """Source files of a submission in an active lease of this grader, as a ZIP."""
        import io
        import zipfile
        with self.store.lock:
            rows = self.store.db.execute("SELECT job_id FROM grader_leases WHERE grader_id=? AND status='active'", (grader["id"],)).fetchall()
        for (job_id,) in rows:
            job = self.store.get(job_id)
            if submission_id in job["submission_ids"]:
                item = self.store.get(submission_id, kind="submission")
                folder = Path(item["source_path"])
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
                    for path in sorted(folder.iterdir()):
                        if path.is_file() and not path.is_symlink():
                            bundle.write(path, path.name)
                return buffer.getvalue()
        raise Fault("not_found", "Source not part of an active lease")

    def submit_result(self, grader, lease_id, fencing, status, report, error):
        job_id = self.active_lease(grader, lease_id, fencing)
        if status not in TERMINAL:
            raise Fault("invalid_request", "status must be completed, failed or aborted")
        job = self.store.get(job_id)
        if status == "failed" and not (isinstance(error, dict) and isinstance(error.get("code"), str)):
            raise Fault("invalid_request", "failed needs error.code")
        if report is not None and not isinstance(report, dict):
            raise Fault("invalid_request", "report must be an object")
        if report:
            # The grader's identity is authoritative only as the hub recorded it at lease time.
            report.update(job.get("runtime") or {})
        try:
            verify(job, status, report)
        except Fault as exc:
            status, error = "failed", {"code": exc.code, "detail": exc.detail[:500], "stage": "verify"}
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE grader_leases SET status='completed',ended_ms=? WHERE id=?", (self.lease_now_ms(), lease_id))
            self.store.db.execute("UPDATE graders SET jobs_done=jobs_done+1 WHERE id=?", (grader["id"],))
            fields = {"status": status, "report": report}
            if status == "failed":
                fields["error"] = error
            self.store.update(job_id, **fields)
            if status == "failed":
                self.store.event(job_id, "failed", error)
            else:
                self.store.event(job_id, status, {"result": (report or {}).get("result"), "summary": (report or {}).get("turn_wall_ms"),
                                                  "reason": (report or {}).get("aborted")})
        if self.competitions and job.get("official"):
            self.competitions.reconcile()
        return {"ok": True, "status": status, **({"error": error} if status == "failed" else {})}

    def release(self, grader, lease_id, fencing, reason="released"):
        self.active_lease(grader, lease_id, fencing)
        self.end_lease(lease_id, "released", reason if isinstance(reason, str) and len(reason) <= 60 else "released")
        return {"ok": True}

    def end_lease(self, lease_id, status, reason):
        """Lease lost (expired, released, revoked): the job never keeps a half result."""
        with self.store.lock, self.store.db:
            row = self.store.db.execute("SELECT job_id,status,expires_ms FROM grader_leases WHERE id=?", (lease_id,)).fetchone()
            if not row or row[1] != "active":
                return
            if status == "expired" and row[2] >= self.lease_now_ms():
                return  # A heartbeat may have renewed it after the reaper's snapshot.
            self.store.db.execute("UPDATE grader_leases SET status=?,ended_ms=?,reason=? WHERE id=?", (status, self.lease_now_ms(), reason, lease_id))
            job = self.store.get(row[0])
            if job["status"] in TERMINAL or job.get("lease_id") != lease_id:
                return
            if job.get("official"):
                # Infrastructure failure: no result. Competitions.reconcile schedules a new attempt.
                self.store.update(job["id"], status="aborted", error={"code": "worker_lost", "detail": f"Grader lease {status}: {reason}"})
                self.store.event(job["id"], "aborted", {"code": "worker_lost"})
            else:
                restarts = job.get("restarts", 0) + 1
                self.store.update(job["id"], status="queued", report=None, error=None, lease_id=None, restarts=restarts)
                self.store.event(job["id"], "requeued", {"reason": reason, "restart": restarts})
                requires = job.get("requires") or {}
                self.pending.put(job["id"], kind=job.get("kind"), platform=requires.get("platform"),
                                 runtime_id=requires.get("runtime_id"), front=True)
        if job.get("official") and self.competitions:
            self.competitions.reconcile()

    def reap(self):
        with self.store.lock:
            expired = [r[0] for r in self.store.db.execute("SELECT id FROM grader_leases WHERE status='active' AND expires_ms<?", (self.lease_now_ms(),))]
        for lease_id in expired:
            self.end_lease(lease_id, "expired", "lease_expired")
        return len(expired)

    def reap_loop(self):
        while not self.stop.wait(1):
            try:
                self.reap()
            except Exception:  # pragma: no cover - logged by the caller's process
                import logging
                logging.getLogger("arena.service").exception("Lease reaper failed")
