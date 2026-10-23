"""Gói 3: remote graders — tokens, leases, fencing, expiry, retries, hub-side verification."""
import hashlib
import io
import json
import socket
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from arena import engine
from arena.adjudication import CURRENT
from arena.config import match_profile, profile
from arena.protocol import Fault
from arena.runner import fixtures
from arena.verify import verify
import test_competitions as competition_tests
from test_web_api import APIHarness

LINUX = "sha256:" + "5" * 64
WINDOWS = "sha256:" + "1" * 64


def zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, text in files.items():
            bundle.writestr(name, text)
    return out.getvalue()


def play_out(job, max_plies=None, tamper=None):
    """A complete, honest match report: both sides always play the first legal move."""
    p = match_profile(60_000)
    state, moves = engine.initial_state(), []
    limit = max_plies or p.max_plies
    seen = {}
    from arena.adjudication import position_key
    seen[position_key(state)] = 1
    for turn in range(1, limit + 1):
        action = engine.legal_moves(state)[0]
        moves.append({"side": state["to_move"], "turn_id": str(turn), "action": action})
        state = engine.apply(state, action)
        if not state["result"]:
            key = position_key(state)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= CURRENT["repetition_count"]:
                state = engine.finish(state, None, "threefold_repetition")
        if state["result"]:
            break
    if not state["result"]:
        state = engine.finish(state, None, "ply_limit")
    report = {"result": state["result"], "final_state": state, "moves": moves, "eligible_for_tournament": bool(job.get("official")),
              "adjudication": dict(CURRENT), "profile": job["profile"], "engine_sha256": job.get("engine_sha256", engine.ENGINE_SHA256), "samples": []}
    if tamper:
        tamper(report)
    return report


class Verification(unittest.TestCase):
    def job(self):
        return {"id": "j", "kind": "match", "profile": match_profile(60_000).public(), "adjudication": dict(CURRENT)}

    def test_honest_reports_pass_and_tampered_ones_fail(self):
        job = self.job()
        verify(job, "completed", play_out(job))
        cases = {
            "result flipped": lambda r: r.update(result={**r["result"], "winner": "blue" if r["result"]["winner"] != "blue" else "red"}),
            "illegal move": lambda r: r["moves"][0].update(action={"from": "a1", "to": "i9"}),
            "wrong side": lambda r: r["moves"][1].update(side=r["moves"][0]["side"]),
            "move dropped": lambda r: r["moves"].pop(3),
            "board edited": lambda r: r["final_state"].update(ply=r["final_state"]["ply"] + 1),
            "moves missing": lambda r: r.pop("moves"),
            "infra fault scored": lambda r: r.update(fault={"side": "red", "code": "worker_error"}),
        }
        for name, tamper in cases.items():
            with self.subTest(name), self.assertRaises(Fault) as caught:
                verify(job, "completed", play_out(job, tamper=tamper))
            self.assertEqual(caught.exception.code, "result_mismatch")
        verify(job, "failed", None)  # failures carry no result to check

    def test_bot_faults_startup_failures_and_limits_replay_exactly(self):
        job = self.job()
        state = engine.initial_state()
        first = engine.legal_moves(state)[0]
        after = engine.apply(state, first)
        timeout = engine.finish(after, "red", "timeout")
        verify(job, "completed", {"moves": [{"side": "red", "turn_id": "1", "action": first}],
                                  "fault": {"side": "blue", "code": "timeout"}, "result": timeout["result"], "final_state": timeout})
        with self.assertRaises(Fault):
            verify(job, "completed", {"moves": [{"side": "red", "turn_id": "1", "action": first}],
                                      "fault": {"side": "red", "code": "timeout"}, "result": timeout["result"], "final_state": timeout})
        start = engine.finish(engine.initial_state(), "blue", "startup_failure")
        verify(job, "completed", {"moves": [], "startup_faults": [{"side": "red", "code": "crash"}], "result": start["result"], "final_state": start})
        wall = engine.finish(after, None, "wall_limit")
        verify(job, "completed", {"moves": [{"side": "red", "turn_id": "1", "action": first}], "result": wall["result"], "final_state": wall})
        early = engine.finish(after, None, "ply_limit")
        with self.assertRaises(Fault):  # ply_limit only after max_plies moves
            verify(job, "completed", {"moves": [{"side": "red", "turn_id": "1", "action": first}], "result": early["result"], "final_state": early})

    def test_benchmark_samples_are_replayed_on_the_public_suite(self):
        job = {"id": "b", "kind": "benchmark", "profile": profile().public()}
        cases = fixtures()
        from arena.runner import digest
        samples = [{"case_id": c["id"], "action": engine.legal_moves(c["state"])[0]} for c in cases * 3]
        report = {"passed": True, "samples": samples, "suite_sha256": digest(cases)}
        verify(job, "completed", report)
        bad = json.loads(json.dumps(report))
        bad["samples"][5]["action"] = {"from": "a1", "to": "i9"}
        with self.assertRaises(Fault):
            verify(job, "completed", bad)
        with self.assertRaises(Fault):
            verify(job, "completed", {**report, "samples": samples[:71]})


class GraderAPI(APIHarness):
    post = competition_tests.CompetitionAPI.post
    qualified = competition_tests.CompetitionAPI.qualified
    prepared = competition_tests.CompetitionAPI.prepared
    create_comp = competition_tests.CompetitionAPI.create_comp

    def setUp(self):
        super().setUp()
        self.comp, self.store = self.app.state.competitions, self.app.state.store
        self.service = self.app.state.service
        runtime = patch("arena.service.Service.runtime_for", return_value=WINDOWS)
        runtime.start()
        self.addCleanup(runtime.stop)

    def machine(self, name="PC chấm", platform="linux", runtime=LINUX, slots=2):
        item = self.post(self.client, "/admin/graders", {"name": name}, expected=201)
        client = self.other_client()
        client.headers["Authorization"] = "Bearer " + item["token"]
        hello = client.post("/api/v1/grader/hello", json={"platform": platform, "runtime_id": runtime, "slots": slots,
                                                         "backend": "bwrap" if platform == "linux" else "windows", "version": "test"})
        self.assertEqual(hello.status_code, 200, hello.text)
        return client, item

    def lease(self, client, wait=0, expected=200):
        response = client.post("/api/v1/grader/lease", json={"wait_seconds": wait})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json().get("lease") if expected == 200 else response.json()

    def practice(self, team_client, team_id):
        submission = self.store.create("submission", team_id, {"version": "v1.0.0", "runtime": "cpp17", "protocol_version": 2,
                                                              "source_path": "/synthetic", "source": {"files": {}}})
        response = team_client.post("/api/v1/matches", json={"red_submission_id": submission["id"], "blue_submission_id": submission["id"],
                                                             "delegate_timeout_claims": True, "match_ms": 60000},
                                    headers={"Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_tokens_are_shown_once_hashed_and_never_listed(self):
        created = self.post(self.client, "/admin/graders", {"name": "Máy 1"}, expected=201)
        self.assertTrue(created["token"].startswith("uag_"))
        listed = self.client.get("/api/v1/admin/graders").text
        self.assertNotIn(created["token"], listed)
        self.assertNotIn("token_hash", listed)
        stored = self.store.db.execute("SELECT token_hash FROM graders").fetchone()[0]
        self.assertEqual(stored, hashlib.sha256(created["token"].encode()).hexdigest())
        team, _ = self.create_team()
        self.assertEqual(team.post("/api/v1/admin/graders", json={"name": "x"}).status_code, 403)
        bare = self.other_client()
        self.assertEqual(bare.post("/api/v1/grader/lease", json={}).status_code, 401)
        bare.headers["Authorization"] = "Bearer uag_" + "x" * 40
        self.assertEqual(bare.post("/api/v1/grader/lease", json={}).status_code, 401)

    def test_listing_shows_online_platform_slots_and_runtime(self):
        self.machine("Win A", "windows", WINDOWS, 2)
        self.machine("WSL", "linux", LINUX, 1)
        items = {g["name"]: g for g in self.client.get("/api/v1/admin/graders").json()["items"]}
        self.assertTrue(items["Win A"]["online"])
        self.assertEqual((items["Win A"]["platform"], items["Win A"]["slots"], items["Win A"]["runtime_id"]), ("windows", 2, WINDOWS))
        self.assertEqual(self.client.get("/api/v1/health/ready").json()["grader_slots"], 3)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE graders SET last_seen_ms=0 WHERE name='WSL'")
        items = {g["name"]: g for g in self.client.get("/api/v1/admin/graders").json()["items"]}
        self.assertFalse(items["WSL"]["online"])

    def test_official_work_only_goes_to_the_competition_platform(self):
        comp, _, _, _ = self.prepared()  # windows, runtime WINDOWS (patched runtime_for)
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        linux, _ = self.machine("WSL", "linux", LINUX)
        other_windows, _ = self.machine("Win khác", "windows", "sha256:" + "7" * 64)
        windows, _ = self.machine("Win", "windows", WINDOWS)
        self.assertIsNone(self.lease(linux))
        self.assertIsNone(self.lease(other_windows))
        leased = self.lease(windows)
        self.assertEqual(leased["job"]["official"]["competition_id"], comp["id"])
        self.assertEqual(len(leased["submissions"]), 2)
        self.assertTrue(all(s["artifact_required"] and s["artifact_sha256"] == "4" * 64 for s in leased["submissions"]))

    def test_practice_goes_to_any_platform_and_lease_payload_has_no_paths(self):
        team, issued = self.create_team()
        job = self.practice(team, issued["account"]["team_id"])
        linux, _ = self.machine()
        leased = self.lease(linux)
        self.assertEqual(leased["job"]["id"], job["id"])
        self.assertNotIn("source_path", json.dumps(leased))
        self.assertEqual(self.store.get(job["id"])["status"], "starting")
        self.assertEqual(self.store.get(job["id"])["runtime"], {"platform": "linux", "runtime_id": LINUX, "backend": "bwrap"})

    def test_fencing_rejects_a_stale_grader_after_expiry_and_requeues_practice_first(self):
        team, issued = self.create_team()
        self.practice(team, issued["account"]["team_id"])
        old, _ = self.machine("Cũ")
        first = self.lease(old)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE grader_leases SET expires_ms=0 WHERE id=?", (first["lease_id"],))
        self.assertEqual(self.service.reap(), 1)
        requeued = self.store.get(first["job"]["id"])
        self.assertEqual((requeued["status"], requeued["restarts"]), ("queued", 1))
        self.assertIn("requeued", [e["type"] for e in self.store.events(requeued["id"])])
        new, _ = self.machine("Mới")
        second = self.lease(new)
        self.assertEqual(second["job"]["id"], first["job"]["id"], "a lost job keeps its turn")
        self.assertGreater(second["fencing"], first["fencing"])
        for action, body in (("heartbeat", {}), ("events", {"events": []}), ("result", {"status": "aborted"})):
            response = old.post(f"/api/v1/grader/leases/{first['lease_id']}/{action}", json={"fencing": first["fencing"], **body})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "stale_lease")
        wrong = new.post(f"/api/v1/grader/leases/{second['lease_id']}/heartbeat", json={"fencing": first["fencing"]})
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(new.post(f"/api/v1/grader/leases/{second['lease_id']}/heartbeat", json={"fencing": second["fencing"]}).status_code, 200)

    def test_hub_restart_invalidates_lease_and_requeues_practice_with_fresh_report(self):
        from arena.service import Service
        from arena.store import Store
        team, issued = self.create_team()
        job = self.practice(team, issued["account"]["team_id"])
        client, machine = self.machine()
        first = self.lease(client)
        self.store.update(job["id"], status="playing", report={"moves": ["partial"]})
        reopened = Store(self.settings.data)
        try:
            service = Service(reopened, start_worker=False)
            recovered = reopened.get(job["id"])
            self.assertEqual((recovered["status"], recovered["restarts"]), ("queued", 1))
            self.assertIsNone(recovered["report"])
            self.assertIsNone(recovered["lease_id"])
            self.assertEqual(reopened.db.execute("SELECT status,reason FROM grader_leases WHERE id=?",
                                                (first["lease_id"],)).fetchone(), ("expired", "server_restart"))
            for action, body in (("heartbeat", {}), ("events", {"events": []}), ("result", {"status": "aborted"})):
                response = client.post(f"/api/v1/grader/leases/{first['lease_id']}/{action}", json={"fencing": first["fencing"], **body})
                self.assertEqual(response.status_code, 409, response.text)
            grader = service.grader_row(machine["id"])
            second = service.try_lease(grader)
            self.assertEqual(second["job"]["id"], first["job"]["id"])
            self.assertGreater(second["fencing"], first["fencing"])
        finally:
            reopened.db.close()

    def test_hub_restart_retries_official_attempt_but_preserves_manual_pause(self):
        from arena.service import Service
        from arena.store import Store
        from arena_api.accounts import Accounts
        from arena_api.competitions import Competitions
        comp, _, _, _ = self.prepared()
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        client, _ = self.machine("Windows", "windows", WINDOWS)
        first = self.lease(client)
        self.store.update(first["job"]["id"], status="playing")
        self.post(self.client, f"/admin/competitions/{comp['id']}/pause")
        reopened = Store(self.settings.data)
        try:
            service = Service(reopened, start_worker=False)
            manager = Competitions(reopened, service, Accounts(reopened))
            fixture = first["job"]["official"]["fixture_id"]
            self.assertEqual(reopened.get(first["job"]["id"])["error"]["code"], "server_restart")
            self.assertEqual(manager.fixture(comp["id"], fixture)["status"], "pending")
            self.assertEqual(manager.fixture(comp["id"], fixture)["auto_retries"], 1)
            self.assertEqual(manager.get(comp["id"])["status"], "paused")
            manager.schedule_once()
            self.assertEqual(len(manager.attempts(comp["id"], fixture)["items"]), 1)
        finally:
            reopened.db.close()

    def test_lease_only_expires_at_its_deadline_and_heartbeat_extends_it(self):
        team, issued = self.create_team()
        self.practice(team, issued["account"]["team_id"])
        client, item = self.machine()
        machine = self.service.grader_row(item["id"])
        with patch.object(self.service, "lease_now_ms", return_value=1_000_000):
            leased = self.lease(client)
        deadline = leased["expires_ms"]
        self.assertEqual(deadline, 1_030_000)
        with patch.object(self.service, "lease_now_ms", return_value=1_005_000):
            beat = self.service.heartbeat(machine, leased["lease_id"], leased["fencing"])
        self.assertEqual(beat["expires_ms"], 1_035_000)
        for tick in range(1_006_000, 1_035_000, 1000):
            with patch.object(self.service, "lease_now_ms", return_value=tick):
                self.assertEqual(self.service.reap(), 0)
        with patch.object(self.service, "lease_now_ms", return_value=1_035_001):
            self.assertEqual(self.service.reap(), 1)
        row = self.store.db.execute("SELECT status,reason,ended_ms FROM grader_leases WHERE id=?", (leased["lease_id"],)).fetchone()
        self.assertEqual(row, ("expired", "lease_expired", 1_035_001))

    def test_wall_clock_corrections_do_not_shorten_or_extend_a_live_lease(self):
        team, issued = self.create_team()
        self.practice(team, issued["account"]["team_id"])
        client, item = self.machine()
        machine = self.service.grader_row(item["id"])
        mono = time.monotonic_ns()
        with patch("arena.service.time.monotonic_ns", return_value=mono):
            leased = self.lease(client)
        for jump in (-3_600_000, 3_600_000):
            with self.subTest(wall_jump_ms=jump), patch("arena.service.now_ms", return_value=leased["expires_ms"] + jump), \
                    patch("arena.service.time.monotonic_ns", return_value=mono + 20_000_000_000):
                self.assertEqual(self.service.reap(), 0)
                self.assertEqual(self.service.active_lease(machine, leased["lease_id"], leased["fencing"]), leased["job"]["id"])
        with patch("arena.service.now_ms", return_value=0), patch("arena.service.time.monotonic_ns", return_value=mono + 31_000_000_000):
            self.assertEqual(self.service.reap(), 1)

    def test_reaper_rechecks_a_lease_renewed_after_its_snapshot(self):
        team, issued = self.create_team()
        self.practice(team, issued["account"]["team_id"])
        client, _ = self.machine()
        leased = self.lease(client)
        end_lease = self.service.end_lease
        def renewed(lease_id, status, reason):
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE grader_leases SET expires_ms=? WHERE id=?", (self.service.lease_now_ms() + 30_000, lease_id))
            end_lease(lease_id, status, reason)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE grader_leases SET expires_ms=0 WHERE id=?", (leased["lease_id"],))
        with patch.object(self.service, "end_lease", side_effect=renewed):
            self.service.reap()
        self.assertEqual(self.store.db.execute("SELECT status FROM grader_leases WHERE id=?", (leased["lease_id"],)).fetchone()[0], "active")
        self.assertEqual(self.store.get(leased["job"]["id"])["status"], "starting")

    def test_events_update_status_and_terminal_events_only_come_with_the_result(self):
        team, issued = self.create_team()
        self.practice(team, issued["account"]["team_id"])
        machine, _ = self.machine()
        leased = self.lease(machine)
        path = f"/api/v1/grader/leases/{leased['lease_id']}/events"
        ok = machine.post(path, json={"fencing": leased["fencing"], "events": [
            {"type": "phase", "data": {"phase": "playing"}}, {"type": "state", "data": {"state": engine.initial_state(), "counts": {}}}]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.store.get(leased["job"]["id"])["status"], "playing")
        for bad in ({"type": "completed", "data": {}}, {"type": "phase", "data": {"phase": "completed"}}, {"type": "x", "data": {}}):
            self.assertEqual(machine.post(path, json={"fencing": leased["fencing"], "events": [bad]}).status_code, 400)

    def test_result_losing_lease_during_verification_cannot_overwrite_the_next_run(self):
        team, issued = self.create_team()
        job = self.practice(team, issued["account"]["team_id"])
        client, machine = self.machine()
        first = self.lease(client)
        report = play_out(self.store.get(job["id"]))
        second = []
        def lose_lease(*_):
            self.service.end_lease(first["lease_id"], "expired", "server_restart")
            second.append(self.service.try_lease(self.service.grader_row(machine["id"])))
        with patch("arena.service.verify", side_effect=lose_lease):
            response = client.post(f"/api/v1/grader/leases/{first['lease_id']}/result",
                                   json={"fencing": first["fencing"], "status": "completed", "report": report})
        self.assertEqual(response.status_code, 409, response.text)
        current = self.store.get(job["id"])
        self.assertEqual((current["status"], current["lease_id"]), ("starting", second[0]["lease_id"]))
        self.assertIsNone(current["report"])
        self.assertEqual(self.service.grader_row(machine["id"])["jobs_done"], 0)

    def test_events_and_artifacts_recheck_the_lease_before_publishing(self):
        team, issued = self.create_team()
        job = self.practice(team, issued["account"]["team_id"])
        client, machine = self.machine()
        leased = self.lease(client)
        for operation in ("events", "artifact"):
            with self.subTest(operation=operation):
                active = self.service.active_lease
                next_run = []
                def change_owner(*args):
                    result = active(*args)
                    if not next_run:
                        self.service.end_lease(leased["lease_id"], "expired", "server_restart")
                        next_run.append(self.service.try_lease(self.service.grader_row(machine["id"])))
                    return result
                with patch.object(self.service, "active_lease", side_effect=change_owner):
                    if operation == "events":
                        response = client.post(f"/api/v1/grader/leases/{leased['lease_id']}/events",
                                               json={"fencing": leased["fencing"], "events": [{"type": "phase", "data": {"phase": "playing"}}]})
                    else:
                        binary = b"\x7fELF" + b"a" * 20
                        sha = hashlib.sha256(binary).hexdigest()
                        response = client.put(f"/api/v1/grader/artifacts/linux/{sha}", content=binary,
                                              headers={"X-Lease-Id": leased["lease_id"], "X-Fencing": str(leased["fencing"]),
                                                       "X-Submission-Id": leased["submissions"][0]["id"], "Content-Type": "application/octet-stream"})
                self.assertEqual(response.status_code, 409, response.text)
                leased = next_run[0]
                self.assertEqual(self.store.get(job["id"])["status"], "starting")
                self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM builds").fetchone()[0], 0)

    def test_verified_result_completes_and_a_tampered_one_never_scores(self):
        comp, _, _, _ = self.prepared()
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        windows, _ = self.machine("Win", "windows", WINDOWS)
        leased = self.lease(windows)
        job = self.store.get(leased["job"]["id"])
        report = play_out(job, tamper=lambda r: r.update(result={"winner": "red", "reason": "base_capture"}))
        answer = windows.post(f"/api/v1/grader/leases/{leased['lease_id']}/result", json={"fencing": leased["fencing"], "status": "completed", "report": report})
        self.assertEqual(answer.json()["status"], "failed")
        self.assertEqual(self.store.get(job["id"])["error"]["code"], "result_mismatch")
        fixture = job["official"]["fixture_id"]
        self.assertEqual(self.comp.fixture(comp["id"], fixture)["status"], "attention")
        self.assertTrue(all(s["played"] == 0 for s in self.comp.standings(comp["id"])["items"]))
        self.post(self.client, f"/admin/competitions/{comp['id']}/fixtures/{fixture}/retry")
        leased = self.lease(windows)
        honest = play_out(self.store.get(leased["job"]["id"]))
        answer = windows.post(f"/api/v1/grader/leases/{leased['lease_id']}/result", json={"fencing": leased["fencing"], "status": "completed", "report": honest})
        self.assertEqual(answer.json()["status"], "completed", answer.text)
        self.assertEqual(self.comp.fixture(comp["id"], fixture)["status"], "completed")
        played = sum(s["played"] for s in self.comp.standings(comp["id"])["items"])
        self.assertEqual(played, 2)
        again = windows.post(f"/api/v1/grader/leases/{leased['lease_id']}/result", json={"fencing": leased["fencing"], "status": "completed", "report": honest})
        self.assertEqual(again.status_code, 409, "a result is accepted once per lease")
        self.assertEqual(sum(s["played"] for s in self.comp.standings(comp["id"])["items"]), 2)

    def test_lost_official_attempts_retry_k_times_then_need_the_organiser(self):
        comp, _, _, _ = self.prepared()
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        windows, _ = self.machine("Win", "windows", WINDOWS)
        fixture = None
        for attempt in range(1, self.comp.max_attempts + 2):
            leased = self.lease(windows)
            self.assertEqual(leased["job"]["official"]["attempt"], attempt)
            fixture = fixture or leased["job"]["official"]["fixture_id"]
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE grader_leases SET expires_ms=0 WHERE id=?", (leased["lease_id"],))
            self.service.reap()
            self.assertEqual(self.store.get(leased["job"]["id"])["error"]["code"], "worker_lost")
            if attempt <= self.comp.max_attempts:
                self.assertEqual(self.comp.fixture(comp["id"], fixture)["status"], "pending")
        self.assertEqual(self.comp.fixture(comp["id"], fixture)["status"], "attention")
        self.assertEqual(len(self.comp.attempts(comp["id"], fixture)["items"]), self.comp.max_attempts + 1)
        self.assertTrue(all(s["played"] == 0 for s in self.comp.standings(comp["id"])["items"]))
        self.post(self.client, f"/admin/competitions/{comp['id']}/fixtures/{fixture}/retry")
        self.assertEqual(self.lease(windows)["job"]["official"]["attempt"], self.comp.max_attempts + 2)

    def test_revoked_grader_is_cut_off_and_its_job_returns_to_the_queue(self):
        team, issued = self.create_team()
        self.practice(team, issued["account"]["team_id"])
        machine, item = self.machine()
        leased = self.lease(machine)
        self.post(self.client, f"/admin/graders/{item['id']}/revoke")
        self.assertEqual(self.store.get(leased["job"]["id"])["status"], "queued")
        self.assertEqual(machine.post("/api/v1/grader/lease", json={"wait_seconds": 0}).status_code, 401)
        self.assertEqual(machine.post(f"/api/v1/grader/leases/{leased['lease_id']}/heartbeat", json={"fencing": leased["fencing"]}).status_code, 401)
        self.assertFalse(next(g for g in self.client.get("/api/v1/admin/graders").json()["items"] if g["id"] == item["id"])["online"])

    def test_long_poll_waits_then_returns_nothing_and_revocation_ends_it_quickly(self):
        machine, item = self.machine()
        started = time.monotonic()
        self.assertIsNone(self.lease(machine, wait=1))
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        threading.Timer(0.5, lambda: self.service.revoke_grader(item["id"], "test")).start()
        started = time.monotonic()
        response = machine.post("/api/v1/grader/lease", json={"wait_seconds": 20})
        self.assertEqual(response.status_code, 401)
        self.assertLess(time.monotonic() - started, 3)

    def test_artifacts_are_checked_stored_once_and_offered_to_later_leases(self):
        team, issued = self.create_team()
        team_id = issued["account"]["team_id"]
        raw = zipped({"bot.cpp": "int main(){}"})
        uploaded = team.post("/api/v1/submissions", content=raw, headers={"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0",
                                                                          "Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        sid = uploaded.json()["id"]
        bench = team.post("/api/v1/benchmarks", json={"submission_id": sid}, headers={"Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(bench.status_code, 202)
        machine, _ = self.machine()
        leased = self.lease(machine)
        source = machine.get(leased["submissions"][0]["source_url"])
        self.assertEqual(source.status_code, 200)
        self.assertEqual(zipfile.ZipFile(io.BytesIO(source.content)).read("bot.cpp"), b"int main(){}")
        other, _ = self.machine("Khác")
        self.assertEqual(other.get(leased["submissions"][0]["source_url"]).status_code, 404, "source only for the lease holder")
        binary = b"\x7fELF" + b"\1" * 100
        sha = hashlib.sha256(binary).hexdigest()
        headers = {"X-Lease-Id": leased["lease_id"], "X-Fencing": str(leased["fencing"]), "X-Submission-Id": sid,
                   "Content-Type": "application/octet-stream"}
        self.assertEqual(machine.put(f"/api/v1/grader/artifacts/linux/{'0' * 64}", content=binary, headers=headers).status_code, 400)
        self.assertEqual(machine.put(f"/api/v1/grader/artifacts/windows/{sha}", content=binary, headers=headers).status_code, 403)
        self.assertEqual(machine.put(f"/api/v1/grader/artifacts/linux/{sha}", content=binary, headers=headers).status_code, 200)
        self.assertEqual(machine.get(f"/api/v1/grader/artifacts/linux/{sha}").content, binary)
        self.assertEqual(team.get(f"/api/v1/grader/artifacts/linux/{sha}").status_code, 401)
        machine.post(f"/api/v1/grader/leases/{leased['lease_id']}/result", json={"fencing": leased["fencing"], "status": "failed",
                                                                               "error": {"code": "build_error", "detail": "x"}})
        again = team.post("/api/v1/benchmarks", json={"submission_id": sid, "match_ms": 60000}, headers={"Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(again.status_code, 202)
        offered = self.lease(machine)["submissions"][0]
        self.assertEqual((offered["artifact_sha256"], offered["artifact_required"]), (sha, False))


class FakeWorker:
    def __init__(self):
        self.transport = type("T", (), {"kill": lambda self: None})()

    def start(self, *_):
        return {"wall_ms": 1.0}

    def turn(self, message, on_started=None):
        if on_started:
            on_started({"budget_ms": 1000, "started_mono_ns": "0"})
        return {"message": {"payload": message["payload"]["legal_moves"][0]}, "wall_ms": 1.0, "remaining_ms": 59000}

    def close(self):
        return {"exit_code": 0}


class FakeBackend:
    name, platform = "bwrap", "linux"

    def __init__(self, *_):
        pass

    def identity(self):
        return {"backend": "bwrap", "platform": "linux", "runtime_id": LINUX}

    def build(self, source, out, p, runtime="cpp17"):
        out.mkdir()
        data = b"\x7fELF" + (source / "bot.cpp").read_bytes()
        (out / "bot").write_bytes(data)
        (out / "runtime.json").write_text(json.dumps({"runtime": runtime}))
        return {"ok": True, "code": None, "log": "", "artifact_sha256": hashlib.sha256(data).hexdigest(), "wall_ms": 1.0}

    def worker(self, artifact, p):
        return FakeWorker()


class GraderLifecycle(unittest.TestCase):
    def setUp(self):
        from arena.grader import Grader
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.grader = Grader("http://localhost", "test", state_dir=self.tmp.name, backend_factory=FakeBackend)
        self.grader.client = Mock()
        self.lease = {"lease_id": "lease-test", "fencing": 7, "job": {"id": "job-test", "kind": "match"}}

    def test_release_all_stops_bots_and_returns_fenced_leases(self):
        runner = Mock(lease=self.lease)
        self.grader.active["lease-test"] = runner
        self.grader.release_all()
        runner.abandon.assert_called_once()
        self.grader.client.call.assert_called_once_with("POST", "/api/v1/grader/leases/lease-test/release",
                                                       {"fencing": 7, "reason": "grader_stopping"}, timeout=5)
        self.assertTrue(self.grader.stopping.is_set())

    def test_lease_arriving_after_stop_is_returned_without_starting_bots(self):
        self.grader.release_all()
        with patch("arena.grader.GraderRunner") as runner:
            self.grader.run_lease(0, FakeBackend(), self.lease)
        runner.assert_not_called()
        self.assertEqual(self.grader.client.call.call_args.args[1], "/api/v1/grader/leases/lease-test/release")
        self.assertFalse(self.grader.active)

    def test_first_stop_finishes_the_running_match_and_posts_its_result(self):
        runner = Mock()
        runner.lost = threading.Event()
        runner.lease, runner.report, runner.error = self.lease, {"result": "test"}, None
        runner.client = self.grader.client
        runner.run.side_effect = lambda *_args, **_kw: (self.grader.stopping.set() or "completed")
        with patch("arena.grader.GraderRunner", return_value=runner):
            self.grader.run_lease(0, FakeBackend(), self.lease)
        runner.abandon.assert_not_called()
        self.assertEqual(self.grader.client.call.call_args.args[2]["status"], "completed")
        self.assertEqual(self.grader.done, 1)

    def test_stop_file_returns_leases(self):
        stop = Path(self.tmp.name) / "stop"
        stop.touch()
        thread = Mock()
        thread.is_alive.side_effect = [True, False]
        self.grader.threads = [thread]
        runner = Mock(lease=self.lease)
        self.grader.active["lease-test"] = runner
        self.grader.wait(stop)
        runner.abandon.assert_called_once()

    def test_pythonw_check_handles_missing_console_streams(self):
        from arena.grader import main
        token = Path(self.tmp.name) / "grader-token"
        token.write_text("synthetic-test-token")
        with patch("arena.grader.Grader", return_value=self.grader), patch("arena.grader.logging.basicConfig"), \
                patch("sys.stdout", None), patch("sys.stderr", None):
            self.grader.client.call.return_value = {"name": "test"}
            main(["--hub", "http://localhost", "--check", "--token-file", str(token)])
        self.grader.client.call.assert_called_once()

    def test_revocation_during_start_prevents_service_retries(self):
        from arena.grader import Revoked
        self.grader.client.call.side_effect = Revoked("revoked")
        self.grader.start()
        self.assertTrue(self.grader.revoked.is_set())
        self.assertFalse(self.grader.threads)

    def test_revoked_check_exits_with_the_service_stop_code(self):
        from arena.grader import Revoked, main
        token = Path(self.tmp.name) / "grader-token"
        token.write_text("synthetic-test-token")
        self.grader.client.call.side_effect = Revoked("revoked")
        with patch("arena.grader.Grader", return_value=self.grader), patch("arena.grader.logging.basicConfig"), \
                self.assertRaises(SystemExit) as caught:
            main(["--hub", "http://localhost", "--check", "--token-file", str(token)])
        self.assertEqual(caught.exception.code, 3)


class GraderManifest(unittest.TestCase):
    def test_experimental_and_classic_cached_artifacts_keep_their_own_sdk_identity(self):
        from arena import experimental, sandbox
        from arena.grader import GraderRunner
        runner = object.__new__(GraderRunner)
        runner.grader = Mock(identity={"platform": "linux", "backend": "bwrap", "runtime_id": LINUX})
        runner.grader.cached_artifact.return_value = Path("cached")
        submission = {"runtime": "cpp17", "source": {}, "artifact_sha256": "a" * 64}
        _, classic = runner.ensure_artifact({}, submission, profile())
        _, expanded = runner.ensure_artifact({"ruleset": {"hash": "rules"}}, submission, profile())
        self.assertEqual(classic["sdk_sha256"], sandbox.sdk_hash())
        self.assertEqual(expanded["sdk_sha256"], experimental.sdk_hash())
        self.assertNotEqual(classic["sdk_sha256"], expanded["sdk_sha256"])


class GraderClientEndToEnd(unittest.TestCase):
    """The real grader client over real HTTP (uvicorn) with a fake sandbox."""

    def test_grader_builds_uploads_streams_events_and_submits_a_verified_result(self):
        import uvicorn
        from arena.grader import Grader
        from arena.conf import Secrets
        from arena_api.app import create_app
        from arena_api.settings import Settings
        with tempfile.TemporaryDirectory() as tmp:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen(64)
            origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
            from arena_api.settings import GraderSettings
            app = create_app(Settings(data=Path(tmp) / "hub", origin=origin, secrets=Secrets({}),
                                      grader=GraderSettings(long_poll_seconds=1)))
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            grader = None
            try:
                until = time.monotonic() + 20
                while not server.started and time.monotonic() < until:
                    time.sleep(0.05)
                service, store = app.state.service, app.state.store
                token = service.create_grader("e2e", "test")["token"]
                submission = service.submit("team-x", "v1.0.0", "cpp17", zipped({"bot.cpp": "int main(){}"}), uuid.uuid4().hex)
                job = service.enqueue("team-x", "match", {"red_submission_id": submission["id"], "blue_submission_id": submission["id"],
                                                          "delegate_timeout_claims": True, "match_ms": 60000}, uuid.uuid4().hex)
                grader = Grader(origin, token, "bwrap", 1, cpus=[0], state_dir=Path(tmp) / "grader", backend_factory=FakeBackend)
                grader.start()
                until = time.monotonic() + 30
                while store.get(job["id"])["status"] not in ("completed", "failed", "aborted") and time.monotonic() < until:
                    time.sleep(0.1)
                done = store.get(job["id"])
                self.assertEqual(done["status"], "completed", done.get("error"))
                self.assertTrue(done["report"]["result"])
                self.assertEqual(done["report"]["runtime_id"], LINUX)
                types = [e["type"] for e in store.events(job["id"])]
                for expected in ("leased", "phase", "build", "state", "turn_started", "completed"):
                    self.assertIn(expected, types)
                self.assertEqual(types.count("completed"), 1)
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM builds").fetchone()[0], 1)
                self.assertEqual(service.graders()[0]["jobs_done"], 1)
            finally:
                if grader:
                    grader.stopping.set()
                server.should_exit = True
                thread.join(10)


if __name__ == "__main__":
    unittest.main()
