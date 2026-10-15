import hashlib
import json
import math
from pathlib import Path
import queue
import random
import re
import threading
import time

from arena import engine, sandbox, experimental
from arena.adjudication import CURRENT, BOT_FAULTS, policy, position_key
from arena.job_queue import JobQueue
from arena.archive import unpack
from arena.config import BUILD_FLAGS, profile, request_profile, stored_profile, worker_image
from arena.protocol import Fault

TERMINAL = ("completed", "failed", "aborted")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fixtures(move_engine=None):
    move_engine = move_engine or engine
    rng, state, cases = random.Random(20261010), move_engine.initial_state(), []
    for index in range(24):
        for _ in range(3):
            if state["result"] or not move_engine.legal_moves(state):
                state = move_engine.initial_state()
            state = move_engine.apply(state, rng.choice(move_engine.legal_moves(state)))
        if state["result"] or not move_engine.legal_moves(state):
            state = move_engine.initial_state()
        if state["to_move"] != "red":
            state = move_engine.apply(state, rng.choice(move_engine.legal_moves(state)))
            if state["result"] or not move_engine.legal_moves(state):
                state = move_engine.initial_state()
        cases.append({"id": f"public-{index:02}", "state": state})
    return cases


def summary(samples):
    values = sorted(s["wall_ms"] for s in samples)
    if not values:
        return None
    middle = len(values) // 2
    return {"samples": len(values), "median_ms": values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2,
            "p95_ms": values[math.ceil(len(values) * 0.95) - 1], "max_ms": values[-1]}


class Service:
    def __init__(self, store, start_worker=True):
        self.store = store
        self.rulesets = None
        self.pending = JobQueue(maxsize=16)
        self.before_next = None
        self.stop = threading.Event()
        self.active = None
        self.worker_thread = threading.Thread(target=self.loop, daemon=True)
        if start_worker:
            self.worker_thread.start()

    def health(self):
        try:
            image = sandbox.image_id(worker_image(request_profile({})))
            return {"ready": True, "image_id": image, "runtime": "cpp17", "codon_enabled": False}
        except Fault as exc:
            return {"ready": False, "code": exc.code, "detail": exc.detail, "runtime": "cpp17", "codon_enabled": False}

    def submit(self, team, version, runtime, raw, key, protocol_version=2):
        if type(protocol_version) is not int or protocol_version not in (2,3):
            raise Fault('unsupported_protocol','Chỉ hỗ trợ SDK v2 (OTTv2) hoặc v3 (Experimental)')
        if runtime != "cpp17":
            raise Fault("unsupported_runtime", "Only C++17 execution is enabled in backend v1")
        if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,24})?", version):
            raise Fault("invalid_request", "Expected semantic version, e.g. v1.0.0")
        source_hash = hashlib.sha256(raw).hexdigest()
        operation_hash = digest({"version": version, "runtime": runtime, "zip_sha256": source_hash,
                                 **({'protocol_version':3} if protocol_version==3 else {})})
        def create():
            if len(self.store.list("submission", team)) >= 20:
                raise Fault("quota_exceeded", "Local beta allows 20 submissions per team")
            if any(s["version"] == version for s in self.store.list("submission", team)):
                raise Fault("version_conflict", "Version already exists; submit a new version")
            import uuid
            directory = self.store.root / "sources" / uuid.uuid4().hex
            manifest = unpack(raw, directory, profile())
            return self.store.create("submission", team, {"version": version, "runtime": runtime,
                "protocol_version": protocol_version, "source_path": str(directory), "source": manifest, "artifact": None})
        return self.store.deduplicate(team, "submission", key, operation_hash, create)

    def public_submission(self, item):
        return {k: v for k, v in item.items() if k != "source_path"}

    def enqueue(self, team, kind, body, key):
        if kind not in ("benchmark", "match"):
            raise Fault("invalid_request", "Unknown job kind")
        try:
            p = request_profile(body)
        except ValueError as exc:
            raise Fault("invalid_request", str(exc)) from exc
        if kind == "benchmark":
            if set(body) - {"submission_id", "turn_ms", "match_ms", "ruleset_id"}:
                raise Fault("invalid_request", "Unexpected benchmark fields")
            ids = [body.get("submission_id")]
        else:
            if set(body) - {"red_submission_id", "blue_submission_id", "turn_ms", "match_ms", "delegate_timeout_claims", "ruleset_id"}:
                raise Fault("invalid_request", "Unexpected match fields")
            if body.get("delegate_timeout_claims") is not True:
                raise Fault("invalid_request", "Both practice bots must delegate timeout claims to the server")
            ids = [body.get("red_submission_id"), body.get("blue_submission_id")]
        for ident in ids:
            if not isinstance(ident, str):
                raise Fault("invalid_request", "Missing submission ID")
            self.store.get(ident, team, "submission")
        def create():
            snapshot = self.rulesets.resolve(body.get('ruleset_id')) if self.rulesets else None
            if body.get('ruleset_id') and not snapshot:raise Fault('invalid_request','Experimental catalog is unavailable')
            if snapshot and any(self.store.get(ident).get('protocol_version')!=3 for ident in ids):
                raise Fault('unsupported_protocol','Experimental cần bot SDK v3. Nộp một version mới và chọn SDK Experimental khi tải ZIP lên.')
            image = sandbox.image_id(worker_image(p, bool(snapshot)))
            if len(self.store.list("job", team)) >= 200:
                raise Fault("quota_exceeded", "Local beta allows 200 jobs per team")
            active = [j for j in self.store.list("job", team) if j["status"] not in TERMINAL]
            if len(active) >= 4 or self.pending.practice_full():
                raise Fault("quota_exceeded", "Queue limit reached")
            job = self.store.create("job", team, {"kind": kind, "status": "queued", "submission_ids": ids,
                "profile": p.public(), "image_id": image, "engine_id": engine.ENGINE_ID,
                "engine_sha256": engine.ENGINE_SHA256, "adjudication": dict(CURRENT), "report": None, "error": None,
                **({"ruleset":snapshot,"protocol_version":3,"engine_id":experimental.ENGINE_ID,"engine_sha256":experimental.ENGINE_SHA256} if snapshot else {})})
            self.store.event(job["id"], "queued", {"kind": kind})
            self.pending.put(job["id"])
            return job
        return self.store.deduplicate(team, kind, key, digest(body), create)

    def phase(self, job, phase, **data):
        self.store.update(job["id"], status=phase)
        self.store.event(job["id"], "phase", {"phase": phase, **data})

    def ensure_artifact(self, job, submission, p, require_existing=False):
        # Build per immutable submission AND image/flags/SDK, never silently reuse
        # an artifact produced under a different toolchain.
        sdk_hash = experimental.sdk_hash() if job.get("ruleset") else hashlib.sha256((sandbox.ROOT / "sdk" / "json.hpp").read_bytes()).hexdigest()
        build_key = digest({"source": submission["source"], "image": job["image_id"], "flags": BUILD_FLAGS, "sdk": sdk_hash})
        folder = self.store.root / "artifacts" / build_key
        artifact = folder / "bot"
        manifest_file = folder / "manifest.json"
        if manifest_file.is_file() and artifact.is_file():
            manifest = json.loads(manifest_file.read_text())
            if hashlib.sha256(artifact.read_bytes()).hexdigest() == manifest["artifact_sha256"]:
                return folder, manifest
            raise Fault("artifact_corrupt", "Stored binary hash mismatch")
        if require_existing:
            raise Fault("artifact_missing", "Locked binary/manifest missing; restore the verified artifact before retry")
        if folder.exists():
            # Interrupted output is retained for inspection; a retry gets a
            # fresh, controlled staging directory, not a recursive deletion.
            folder.rename(folder.with_name(build_key + "-interrupted-" + str(time.time_ns())))
        folder.parent.mkdir(parents=True, exist_ok=True)
        result = sandbox.build(Path(submission["source_path"]), folder, p, job["image_id"])
        self.store.event(job["id"], "build", {"submission_id": submission["id"], **result})
        if not result["ok"]:
            raise Fault(result["code"], result["log"] or "Compilation failed")
        manifest = {**result, "build_key": build_key, "source": submission["source"], "image_id": job["image_id"],
                    "flags": BUILD_FLAGS, "sdk_sha256": sdk_hash}
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.update(submission["id"], artifact={k: v for k, v in manifest.items() if k != "log"})
        return folder, manifest

    def loop(self):
        while not self.stop.is_set():
            if self.before_next:
                self.before_next()
            try:
                ident = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            job = self.store.get(ident)
            self.active = ident
            try:
                self.execute(job)
            except Exception as exc:
                code = exc.code if isinstance(exc, Fault) else "worker_error"
                detail = str(exc)[:4000]
                phase = self.store.get(ident).get("status")
                error = {"code": code, "detail": detail, "stage": {"building": "build", "validating": "startup", "playing": "turn"}.get(phase, "preflight")}
                self.store.update(ident, status="failed", error=error)
                self.store.event(ident, "failed", error)
            finally:
                self.active = None
                self.pending.task_done()

    def execute(self, job):
        p = stored_profile(job["profile"])
        move_engine = experimental.provider(job.get("ruleset"))
        expected_engine = experimental.ENGINE_SHA256 if job.get("ruleset") else engine.ENGINE_SHA256
        expected_sdk = experimental.sdk_hash() if job.get("ruleset") else hashlib.sha256((sandbox.ROOT / "sdk" / "json.hpp").read_bytes()).hexdigest()
        official = job.get("official")
        rules = policy(official.get("adjudication") if official else job.get("adjudication"))
        if official:
            # Only the trusted competition scheduler can create this scope.
            # Practice HTTP inputs never accept owners or official metadata.
            if (job["kind"] != "match" or job["team_id"] != "competition:" + official["competition_id"]
                    or len(official["entries"]) != 2 or official["engine_sha256"] != expected_engine
                    or len(job["submission_ids"]) != 2 or job["profile"] != p.public()
                    or official["sdk_sha256"] != expected_sdk
                    or official.get("ruleset") != job.get("ruleset")
                    or official["build_flags"] != BUILD_FLAGS):
                raise Fault("provenance_mismatch", "Competition runtime no longer matches its locked configuration")
            submissions = [self.store.get(s, entry["team_id"], "submission")
                           for s, entry in zip(job["submission_ids"], official["entries"])]
            if len(submissions) != 2 or any(s["source"] != entry["source"] for s, entry in zip(submissions, official["entries"])):
                raise Fault("provenance_mismatch", "Locked source manifest changed")
        else:
            submissions = [self.store.get(s, job["team_id"], "submission") for s in job["submission_ids"]]
        self.phase(job, "building")
        built = [self.ensure_artifact(job, s, p, require_existing=bool(official) and rules == CURRENT) for s in submissions]
        if official and any(b[1]["artifact_sha256"] != entry["artifact_sha256"] for b, entry in zip(built, official["entries"])):
            raise Fault("provenance_mismatch", "Competition must use the exact benchmarked binary")
        report = {"job_id": job["id"], "kind": job["kind"], "engine_id": job.get("engine_id", engine.ENGINE_ID),
                  "engine_sha256": expected_engine, "image_id": job["image_id"], "profile": p.public(),
                  "artifacts": [b[1] for b in built], "samples": [], "startup": [], "sandbox_metrics": [],
                  "clock_authority": "trusted-worker-monotonic", "eligible_for_tournament": bool(official), "adjudication": rules,
                  "memory_scope": "whole container, including trusted Python supervisor",
                  "cpu_scope": "whole container CPU delta; diagnostic, wall deadline decides timeout"}
        if job.get("ruleset"):report.update(ruleset=job["ruleset"],protocol_version=3)
        workers = []
        startup_faults = []
        try:
            self.phase(job, "validating")
            for i, (artifact, _) in enumerate(built):
                if self.stop.is_set():
                    raise Fault("worker_error", "Controller stopped during startup; no result awarded")
                w = sandbox.Worker(artifact, p, job["image_id"])
                workers.append(w)
                side = ("red", "blue")[i]
                try:
                    report["startup"].append({"side": side, **w.start(job["id"], side, {"kind": job["kind"], "adjudication": rules, **({"ruleset":job["ruleset"]} if job.get("ruleset") else {})})})
                except Fault as exc:
                    # Only a trusted supervisor response can establish a bot
                    # fault. Transport/Docker/artifact failures never award wins.
                    if (job["kind"] != "match" or rules != CURRENT or exc.code not in BOT_FAULTS
                            or not hasattr(exc, "worker_data")):
                        raise
                    startup_faults.append({"side": side, "stage": "startup", "code": exc.code, "detail": exc.detail})
            if job["kind"] == "benchmark":
                self.run_benchmark(job, workers[0], report, p)
            elif startup_faults:
                if self.stop.is_set():
                    raise Fault("worker_error", "Controller stopped during startup; no result awarded")
                report["startup_faults"] = startup_faults
                state = move_engine.initial_state()
                clocks = {side:p.match_ms for side in engine.SIDES} if p.match_ms is not None else None
                self.emit_state(job, state, clocks=clocks)
                winner = engine.other(startup_faults[0]["side"]) if len(startup_faults) == 1 else None
                reason = "startup_failure" if winner else "double_startup_failure"
                state = engine.finish(state, winner, reason)
                report["result"], report["final_state"] = state["result"], state
                self.emit_state(job, state, clocks=clocks)
                if clocks is not None:report["clocks"] = clocks
            else:
                self.run_match(job, workers, report, p)
        except Fault as exc:
            report["error"] = {"code": exc.code, "detail": exc.detail}
            raise
        finally:
            for w in workers:
                report["sandbox_metrics"].append(w.close())
            report["turn_wall_ms"] = summary(report["samples"])
            self.store.update(job["id"], report=report)
        status = "aborted" if report.get("aborted") else "completed"
        self.store.update(job["id"], status=status)
        self.store.event(job["id"], status, {"result": report.get("result"), "summary": report["turn_wall_ms"], "reason": report.get("aborted")})

    def emit_state(self, job, state, trace=None, clocks=None):
        data = {"state":state,"counts":engine.counts(state)}
        if clocks is not None:data["clocks"] = dict(clocks)
        if job.get('ruleset'):data.update(ruleset=job['ruleset'],events=trace or [])
        self.store.event(job['id'],'state',data)

    def message(self, job, turn_id, state, clocks=None):
        payload = experimental.provider(job.get("ruleset")).observation(state)
        payload["adjudication"] = policy(job.get("official", {}).get("adjudication") if job.get("official") else job.get("adjudication"))
        if clocks is not None:payload["clocks"] = dict(clocks)
        return {"protocol_version": 3 if job.get("ruleset") else 2, "type": "observation", "match_id": job["id"],
                "turn_id": str(turn_id), "state_version": state["ply"], "payload": payload}

    def run_benchmark(self, job, worker, report, p):
        move_engine = experimental.provider(job.get("ruleset"))
        cases = fixtures(move_engine)
        report["suite_id"] = "ottv2-experimental-smoke-v1" if job.get("ruleset") else "ottv2-public-smoke-v1"
        report["suite_sha256"] = digest(cases)
        report["warmups"] = 0
        clocks = {side:p.match_ms for side in engine.SIDES} if p.match_ms is not None else None
        if clocks is not None:report["clock_scope"] = "one cumulative bank across all 72 benchmark samples"
        self.phase(job, "benchmarking", total=len(cases) * 3)
        for index, case in enumerate(cases * 3):
            if self.stop.is_set():
                raise Fault("worker_error", "Controller stopping")
            result = worker.turn(self.message(job, index + 1, case["state"], clocks))
            if clocks is not None:clocks["red"] = result["remaining_ms"]
            action = result.pop("message")["payload"]
            if isinstance(action, dict) and "resign" in action:
                raise Fault("illegal_action", "Benchmark expects a legal move, not resignation")
            try:
                move_engine.apply(case["state"], action)
            except ValueError as exc:
                raise Fault("illegal_action", str(exc)) from exc
            report["samples"].append({"case_id": case["id"], "repeat": index // len(cases), **result})
            self.store.event(job["id"], "progress", {"completed": index + 1, "total": len(cases) * 3})
        report["passed"] = True
        if clocks is not None:report["clocks"] = clocks

    def run_match(self, job, workers, report, p):
        self.phase(job, "playing")
        move_engine = experimental.provider(job.get("ruleset"))
        key_of = move_engine.position_key if job.get("ruleset") else position_key
        state = move_engine.initial_state()
        rules = policy(job.get("official", {}).get("adjudication") if job.get("official") else job.get("adjudication"))
        seen = {key_of(state): 1}
        started = time.monotonic_ns()
        clocks = {side:p.match_ms for side in engine.SIDES} if p.match_ms is not None else None
        self.emit_state(job, state, clocks=clocks)
        for turn in range(1, p.max_plies + 1):
            if self.stop.is_set():
                report["aborted"] = "server_shutdown"
                break
            if (time.monotonic_ns() - started) / 1e6 >= p.job_wall_ms:
                if rules == CURRENT:
                    state = engine.finish(state, None, "wall_limit")
                else:
                    report["aborted"] = "job_wall_limit_or_shutdown"
                break
            side = state["to_move"]
            worker = workers[0 if side == "red" else 1]
            def on_started(data):
                self.store.event(job["id"], "turn_started", {"side": side, "turn_id": str(turn), **data,
                    **({"clocks":dict(clocks)} if clocks is not None else {})})
            action = None
            trace = []
            try:
                result = worker.turn(self.message(job, turn, state, clocks), on_started)
                if clocks is not None:clocks[side] = result["remaining_ms"]
                action = result.pop("message")["payload"]
                report["samples"].append({"side": side, "turn_id": str(turn), **result})
                state, trace = move_engine.transition(state, action) if job.get("ruleset") else (engine.apply(state, action), [])
            except (Fault, ValueError) as exc:
                code = exc.code if isinstance(exc, Fault) else "illegal_action"
                if code in ("worker_error", "worker_unavailable"):
                    raise
                if clocks is not None:
                    clocks[side] = getattr(exc, "worker_data", {}).get("remaining_ms", clocks[side])
                if code == "timeout":
                    if clocks is not None:clocks[side] = 0
                    # Grace is exclusively a delay before the opponent's
                    # delegated claim; late actions never revive this turn.
                    self.store.event(job["id"], "timeout_pending", {"side": side, "grace_ms": p.grace_ms,
                        **({"clocks":dict(clocks)} if clocks is not None else {})})
                    grace_started_ns = time.monotonic_ns()
                    if self.stop.wait(p.grace_ms / 1000):
                        raise Fault("worker_error", "Shutdown during grace; no time loss awarded")
                    grace_waited_ms = (time.monotonic_ns() - grace_started_ns) / 1e6
                    self.store.event(job["id"], "timeout_claimed", {"claimant": engine.other(side), "by": "server_delegate",
                        "grace_waited_ms": grace_waited_ms, "grace_clock": "controller-monotonic"})
                state = engine.finish(state, engine.other(side), code)
                report["fault"] = {"side": side, "stage": "turn", "turn_id": str(turn), "state_version": state["ply"],
                                   "code": code, "detail": str(exc), "action": action}
            if not state["result"] and rules == CURRENT:
                key = key_of(state)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] >= rules["repetition_count"]:
                    state = engine.finish(state, None, "threefold_repetition")
            self.emit_state(job, state, trace, clocks=clocks)
            if state["result"]:
                report["result"] = state["result"]
                break
        else:
            if rules == CURRENT:
                state = engine.finish(state, None, "ply_limit")
            else:
                report["aborted"] = "ply_limit"
        # Limits are checked between turns, never by UTC or browser clocks.
        # Emit the terminal board as well as completed for replay consistency.
        if state["result"] and "result" not in report:
            report["result"] = state["result"]
            self.emit_state(job, state, clocks=clocks)
        report["final_state"] = state
        if clocks is not None:report["clocks"] = clocks
