"""Execute one job (benchmark or match) with a sandbox backend.

The hub never runs this: graders do (arena.grader). ``Runner`` holds the adjudication logic;
subclasses decide where events, status, sources and artifacts come from and go to:

  * ``GraderRunner`` (arena.grader) talks to the hub over HTTP;
  * ``LocalRunner`` (below) reads and writes a local Store; unit tests use it.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import random
import threading
import time

from arena import engine, sandbox, experimental, basic_rules
from arena.adjudication import CURRENT, BOT_FAULTS, policy, position_key
from arena.config import BUILD_FLAGS, stored_profile, runtime_flags
from arena.protocol import Fault

TERMINAL = ("completed", "failed", "aborted")
INFRASTRUCTURE = ("worker_error", "worker_unavailable")


def engine_for(job):
    return experimental.provider(job["ruleset"]) if job.get("ruleset") else basic_rules.provider(job.get("basic_rules"))


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


def job_error(exc, phase):
    """The failure record of a job, as the hub stores it."""
    code = exc.code if isinstance(exc, Fault) else "worker_error"
    error = {"code": code, "detail": str(exc)[:4000],
             "stage": {"building": "build", "validating": "startup", "playing": "turn", "benchmarking": "benchmark"}.get(phase, "preflight")}
    if getattr(exc, "side", None):
        error["side"] = exc.side
    return error


class Runner:
    def __init__(self, stop=None):
        self.stop = stop or threading.Event()

    # ---- hooks ---------------------------------------------------------------------------
    def record(self, job_id, kind, data):
        raise NotImplementedError

    def save(self, job_id, **fields):
        raise NotImplementedError

    def submissions_for(self, job):
        raise NotImplementedError

    def ensure_artifact(self, job, submission, p, require_existing=False):
        raise NotImplementedError

    def new_worker(self, artifact, p, job):
        raise NotImplementedError

    def runtime_report(self, job):
        """Toolchain identity written into every report (platform, runtime_id, backend)."""
        raise NotImplementedError

    def published_state(self, job_id):
        """True once a board state is visible; a retry would then splice two games."""
        raise NotImplementedError

    def infrastructure_error(self):
        """Called after worker_error/worker_unavailable, before any retry."""

    # ---- one job, including the single in-place infrastructure retry ---------------------
    def run(self, job, attempt_field="attempt"):
        """Execute; never raises. Returns the final status."""
        ident = job["id"]
        phase = "queued"
        try:
            for attempt in range(2):
                self.save(ident, **{attempt_field: attempt + 1})
                try:
                    self.execute(job)
                    return self.final_status
                except Fault as exc:
                    if exc.code not in INFRASTRUCTURE or attempt or self.stop.is_set():
                        raise
                    if self.published_state(ident):
                        raise
                    self.infrastructure_error()
                    self.record(ident, "infrastructure_retry", {"attempt": attempt + 1, "code": exc.code})
                    self.save(ident, report=None, error=None)
                    if self.stop.wait(0.5):
                        raise
        except Exception as exc:
            phase = getattr(self, "current_phase", phase)
            if isinstance(exc, Fault) and exc.code in INFRASTRUCTURE or not isinstance(exc, Fault):
                self.infrastructure_error()
            error = job_error(exc, phase)
            self.save(ident, status="failed", error=error)
            self.record(ident, "failed", error)
            self.final_status = "failed"
            return "failed"

    def phase(self, job, phase, **data):
        self.current_phase = phase
        self.save(job["id"], status=phase)
        self.record(job["id"], "phase", {"phase": phase, **data})

    def execute(self, job):
        self.current_phase = "preflight"
        p = stored_profile(job["profile"])
        move_engine = engine_for(job)
        expected_engine = experimental.ENGINE_SHA256 if job.get("ruleset") else engine.ENGINE_SHA256
        expected_sdk = experimental.sdk_hash() if job.get("ruleset") else sandbox.sdk_hash()
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
                    or official.get("basic_rules") != job.get("basic_rules")
                    or official.get("runtime_flags") is not None and official["runtime_flags"] != {r: runtime_flags(r) for r in ("cpp17", "c11", "python3")}
                    or official["build_flags"] != BUILD_FLAGS):
                raise Fault("provenance_mismatch", "Competition runtime no longer matches its locked configuration")
        submissions = self.submissions_for(job)
        if official and (len(submissions) != 2 or any(s["source"] != entry["source"] for s, entry in zip(submissions, official["entries"]))):
            raise Fault("provenance_mismatch", "Locked source manifest changed")
        self.phase(job, "building")
        built = []
        for i, s in enumerate(submissions):
            try:
                built.append(self.ensure_artifact(job, s, p, require_existing=bool(official) and rules == CURRENT))
            except Fault as exc:
                if len(submissions) == 2:
                    exc.side = engine.SIDES[i]  # lets the API hide only an organiser bot's build log
                raise
        if official and any(b[1]["artifact_sha256"] != entry["artifact_sha256"] for b, entry in zip(built, official["entries"])):
            raise Fault("provenance_mismatch", "Competition must use the exact benchmarked binary")
        report = {"job_id": job["id"], "kind": job["kind"], "engine_id": job.get("engine_id", engine.ENGINE_ID),
                  "engine_sha256": expected_engine, **self.runtime_report(job), "profile": p.public(),
                  "artifacts": [b[1] for b in built], "samples": [], "startup": [], "sandbox_metrics": [], "moves": [],
                  "clock_authority": "trusted-worker-monotonic", "eligible_for_tournament": bool(official), "adjudication": rules,
                  "memory_scope": "whole sandbox, including trusted Python supervisor",
                  "cpu_scope": "whole sandbox CPU delta; diagnostic, wall deadline decides timeout"}
        if job.get("ruleset"):
            report.update(ruleset=job["ruleset"], protocol_version=3)
        if job.get("basic_rules"):
            report["basic_rules"] = job["basic_rules"]
        workers = []
        startup_faults = []
        try:
            self.phase(job, "validating")
            for i, (artifact, _) in enumerate(built):
                if self.stop.is_set():
                    raise Fault("worker_error", "Controller stopped during startup; no result awarded")
                w = self.new_worker(artifact, p, job)
                workers.append(w)
                side = ("red", "blue")[i]
                try:
                    report["startup"].append({"side": side, **w.start(job["id"], side, {"kind": job["kind"], "adjudication": rules, **({"ruleset": job["ruleset"]} if job.get("ruleset") else {}), **({"basic_rules": job["basic_rules"]} if job.get("basic_rules") else {})})})
                except Fault as exc:
                    # Only a trusted supervisor response can establish a bot
                    # fault. Transport/sandbox/artifact failures never award wins.
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
                clocks = {side: p.match_ms for side in engine.SIDES} if p.match_ms is not None else None
                self.emit_state(job, state, clocks=clocks)
                winner = engine.other(startup_faults[0]["side"]) if len(startup_faults) == 1 else None
                reason = "startup_failure" if winner else "double_startup_failure"
                state = engine.finish(state, winner, reason)
                report["result"], report["final_state"] = state["result"], state
                self.emit_state(job, state, clocks=clocks)
                if clocks is not None:
                    report["clocks"] = clocks
            else:
                self.run_match(job, workers, report, p)
        except Fault as exc:
            report["error"] = {"code": exc.code, "detail": exc.detail}
            raise
        finally:
            for w in workers:
                report["sandbox_metrics"].append(w.close())
            report["turn_wall_ms"] = summary(report["samples"])
            self.save(job["id"], report=report)
        status = "aborted" if report.get("aborted") else "completed"
        self.final_status = status
        self.save(job["id"], status=status)
        self.record(job["id"], status, {"result": report.get("result"), "summary": report["turn_wall_ms"], "reason": report.get("aborted")})

    def emit_state(self, job, state, trace=None, clocks=None):
        data = {"state": state, "counts": engine.counts(state)}
        if clocks is not None:
            data["clocks"] = dict(clocks)
        if job.get('ruleset'):
            data.update(ruleset=job['ruleset'], events=trace or [])
        self.record(job['id'], 'state', data)

    def message(self, job, turn_id, state, clocks=None):
        payload = engine_for(job).observation(state)
        if job.get("basic_rules"):
            payload["basic_rules"] = job["basic_rules"]
        payload["adjudication"] = policy(job.get("official", {}).get("adjudication") if job.get("official") else job.get("adjudication"))
        if clocks is not None:
            payload["clocks"] = dict(clocks)
        return {"protocol_version": 3 if job.get("ruleset") else 2, "type": "observation", "match_id": job["id"],
                "turn_id": str(turn_id), "state_version": state["ply"], "payload": payload}

    def run_benchmark(self, job, worker, report, p):
        move_engine = engine_for(job)
        cases = fixtures(move_engine)
        report["suite_id"] = "ottv2-experimental-smoke-v1" if job.get("ruleset") else "ottv2-public-smoke-v1"
        report["suite_sha256"] = digest(cases)
        report["warmups"] = 0
        clocks = {side: p.match_ms for side in engine.SIDES} if p.match_ms is not None else None
        if clocks is not None:
            report["clock_scope"] = "one cumulative bank across all 72 benchmark samples"
        self.phase(job, "benchmarking", total=len(cases) * 3)
        for index, case in enumerate(cases * 3):
            if self.stop.is_set():
                raise Fault("worker_error", "Controller stopping")
            result = worker.turn(self.message(job, index + 1, case["state"], clocks))
            if clocks is not None:
                clocks["red"] = result["remaining_ms"]
            action = result.pop("message")["payload"]
            if isinstance(action, dict) and "resign" in action:
                raise Fault("illegal_action", "Benchmark expects a legal move, not resignation")
            try:
                move_engine.apply(case["state"], action)
            except ValueError as exc:
                raise Fault("illegal_action", str(exc)) from exc
            report["samples"].append({"case_id": case["id"], "repeat": index // len(cases), "action": action, **result})
            self.record(job["id"], "progress", {"completed": index + 1, "total": len(cases) * 3})
        report["passed"] = True
        if clocks is not None:
            report["clocks"] = clocks

    def run_match(self, job, workers, report, p):
        self.phase(job, "playing")
        move_engine = engine_for(job)
        key_of = move_engine.position_key if job.get("ruleset") else position_key
        state = move_engine.initial_state()
        rules = policy(job.get("official", {}).get("adjudication") if job.get("official") else job.get("adjudication"))
        seen = {key_of(state): 1}
        started = time.monotonic_ns()
        clocks = {side: p.match_ms for side in engine.SIDES} if p.match_ms is not None else None
        report.setdefault("moves", [])
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
                self.record(job["id"], "turn_started", {"side": side, "turn_id": str(turn), **data,
                                                         **({"clocks": dict(clocks)} if clocks is not None else {})})
            action = None
            trace = []
            try:
                result = worker.turn(self.message(job, turn, state, clocks), on_started)
                if clocks is not None:
                    clocks[side] = result["remaining_ms"]
                action = result.pop("message")["payload"]
                report["samples"].append({"side": side, "turn_id": str(turn), **result})
                state, trace = move_engine.transition(state, action) if job.get("ruleset") else (engine.apply(state, action), [])
                report["moves"].append({"side": side, "turn_id": str(turn), "action": action})
            except (Fault, ValueError) as exc:
                code = exc.code if isinstance(exc, Fault) else "illegal_action"
                if code in INFRASTRUCTURE:
                    raise
                if clocks is not None:
                    clocks[side] = getattr(exc, "worker_data", {}).get("remaining_ms", clocks[side])
                if code == "timeout":
                    if clocks is not None:
                        clocks[side] = 0
                    # Grace is exclusively a delay before the opponent's
                    # delegated claim; late actions never revive this turn.
                    self.record(job["id"], "timeout_pending", {"side": side, "grace_ms": p.grace_ms,
                                                                **({"clocks": dict(clocks)} if clocks is not None else {})})
                    grace_started_ns = time.monotonic_ns()
                    if self.stop.wait(p.grace_ms / 1000):
                        raise Fault("worker_error", "Shutdown during grace; no time loss awarded")
                    grace_waited_ms = (time.monotonic_ns() - grace_started_ns) / 1e6
                    self.record(job["id"], "timeout_claimed", {"claimant": engine.other(side), "by": "server_delegate",
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
        if clocks is not None:
            report["clocks"] = clocks


class LocalRunner(Runner):
    """Runs jobs against a local Store with a native sandbox (unit tests and tools)."""

    def __init__(self, store, stop=None, compile_slots=2, backend=None):
        super().__init__(stop)
        self.store = store
        self.build_locks = {}
        self.build_lock = threading.Lock()
        self.compile_slots = threading.BoundedSemaphore(max(1, compile_slots))
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None:
            self._backend = sandbox.get_backend("windows" if os.name == "nt" else "bwrap")
        return self._backend

    def runtime_report(self, job):
        # Old job metadata remains readable, but new work always identifies the
        # toolchain actually executing it, including artifact cache keys.
        return self.backend.identity()

    def record(self, job_id, kind, data):
        return self.store.event(job_id, kind, data)

    def save(self, job_id, **fields):
        return self.store.update(job_id, **fields)

    def published_state(self, job_id):
        return any(e["type"] == "state" for e in self.store.events(job_id))

    def submissions_for(self, job):
        official = job.get("official")
        if official:
            return [self.store.get(s, entry["team_id"], "submission") for s, entry in zip(job["submission_ids"], official["entries"])]
        sample_side = (job.get("sample_bot") or {}).get("side", "blue")
        return [self.store.get(s, job["sample_bot"]["owner"] if job.get("sample_bot") and engine.SIDES[i] == sample_side else job["team_id"], "submission")
                for i, s in enumerate(job["submission_ids"])]

    def new_worker(self, artifact, p, job):
        return self.backend.worker(artifact, p)

    def lock_for_build(self, key):
        with self.build_lock:
            return self.build_locks.setdefault(key, threading.Lock())

    def ensure_artifact(self, job, submission, p, require_existing=False):
        key = digest({"source": submission["source"], "runtime_id": self.runtime_report(job)["runtime_id"], "runtime": submission.get("runtime", "cpp17")})
        with self.lock_for_build(key):
            return self._ensure_artifact(job, submission, p, require_existing)

    def _ensure_artifact(self, job, submission, p, require_existing=False):
        # Build per immutable submission AND toolchain/flags/SDK, never silently reuse
        # an artifact produced under a different toolchain.
        sdk_hash = experimental.sdk_hash() if job.get("ruleset") else sandbox.sdk_hash()
        runtime = submission.get("runtime", "cpp17")
        flags = runtime_flags(runtime)
        identity = self.runtime_report(job)
        build_key = digest({"source": submission["source"], "runtime_id": identity["runtime_id"], "flags": flags, "sdk": sdk_hash, "runtime": runtime})
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
            folder.rename(folder.with_name(build_key + "-interrupted-" + str(time.time_ns())))
        folder.parent.mkdir(parents=True, exist_ok=True)
        source = Path(submission["source_path"])
        if runtime == "cpp17" and self.backend.name == "bwrap" and len(list(source.glob("*.cpp"))) == 1:
            result = sandbox.cached_cpp_build(source, folder, p, self.backend, self.store.root / "compiled-cache", self.lock_for_build, self.compile_slots)
        else:
            with self.compile_slots:
                result = self.backend.build(source, folder, p, runtime=runtime)
        self.record(job["id"], "build", {"submission_id": submission["id"], **result})
        if not result["ok"]:
            raise Fault(result["code"], result["log"] or "Compilation failed")
        manifest = {**result, **identity, "runtime": runtime, "build_key": build_key, "source": submission["source"],
                    "flags": flags, "sdk_sha256": sdk_hash}
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save(submission["id"], artifact={k: v for k, v in manifest.items() if k != "log"})
        return folder, manifest
