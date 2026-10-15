"""Rules placement/validation, fixed clocks, Swiss robustness, queue slots and practice privacy."""
import random
import tempfile
import unittest
import uuid
from unittest.mock import patch

from arena import basic_rules, engine
from arena.config import match_profile, scale_profile, stored_profile
from arena.protocol import Fault
from arena.service import MAX_QUEUED_PER_TEAM, Service
from arena.store import Store
from arena_api.arena_routes import PRIVATE_SAMPLE, public_job, private_event
from arena_api.swiss import pair
import test_competitions as competition_tests
from test_web_api import APIHarness


class FlowHarness(APIHarness):
    """API harness with the competition helpers, without re-running NewFlows' own tests."""
    post = competition_tests.CompetitionAPI.post
    qualified = competition_tests.CompetitionAPI.qualified
    finish = competition_tests.CompetitionAPI.finish

    def setUp(self):
        super().setUp()
        self.store, self.comp = self.app.state.store, self.app.state.competitions
        image = patch("arena.sandbox.image_id", return_value="sha256:" + "1" * 64)
        image.start()
        self.addCleanup(image.stop)


def layout(state):
    return sorted((engine.square(r, c, len(state["board"])), p["side"], p["kind"])
                  for r, row in enumerate(state["board"]) for c, p in enumerate(row) if p)


class Rules(unittest.TestCase):
    def test_nine_by_nine_variants_keep_the_classic_opening(self):
        for definition in ({"eliminate_types": 2}, {"base_capture": False}, {"rows": 9, "cols": 9, "eliminate_types": 2}):
            self.assertEqual(layout(basic_rules.Rules(definition).initial_state()), layout(engine.initial_state()), definition)

    def test_every_board_size_is_symmetric_with_three_pieces_of_each_kind(self):
        for rows in range(7, 16):
            for cols in range(7, 16):
                definition = basic_rules.normalize({"rows": rows, "cols": cols})
                state = basic_rules.Rules(definition).initial_state()
                self.assertEqual(engine.counts(state), {s: {k: 3 for k in engine.BEATS} for s in engine.SIDES})
                for r in range(rows):
                    for c in range(cols):
                        a, b = state["board"][r][c], state["board"][rows-1-r][cols-1-c]
                        self.assertEqual(a is None, b is None)
                        if a:
                            self.assertEqual((a["kind"], a["side"] != b["side"]), (b["kind"], True))
                basic_rules.check_playable(definition)

    def test_rules_where_a_side_wins_on_its_first_move_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "first move"):
            basic_rules.validated_snapshot({"bases": {"red": "a1", "blue": "e3"}})
        with self.assertRaisesRegex(ValueError, "first move"):
            basic_rules.validated_snapshot({"bases": {"red": "e7", "blue": "i9"}})
        self.assertTrue(basic_rules.validated_snapshot({"rows": 10, "cols": 10, "bases": {"red": "b1", "blue": "f6"}})["hash"])

    def test_clock_bank_is_identical_on_every_board_and_only_the_ply_cap_scales(self):
        for match_ms in (60_000, 300_000):
            base = match_profile(match_ms)
            for size in (7, 10, 15):
                scaled = scale_profile(base, size * size)
                self.assertEqual((scaled.match_ms, scaled.turn_ms, scaled.grace_ms, scaled.job_wall_ms),
                                 (base.match_ms, base.turn_ms, base.grace_ms, base.job_wall_ms))
                self.assertEqual(scaled.max_plies, round(512 * size * size / 81))
                self.assertEqual(stored_profile(scaled.public()), scaled)
            self.assertEqual(scale_profile(base, 81), base)


class SwissPairing(unittest.TestCase):
    def test_small_events_never_stop_and_avoid_rematches_until_impossible(self):
        rng = random.Random(7)
        for count, rounds in ((5, 5), (6, 5), (7, 5), (8, 5)):
            for _ in range(60):
                teams = [f"t{i}" for i in range(count)]
                points, opponents, byes, rematches = {t: 0 for t in teams}, {t: set() for t in teams}, set(), 0
                for _ in range(rounds):
                    pairs, bye = pair(teams, points, opponents, byes)
                    self.assertEqual(len(pairs), count // 2)
                    self.assertEqual(len({t for p in pairs for t in p} | ({bye} if bye else set())), count)
                    if bye:
                        self.assertNotIn(bye, byes)
                        byes.add(bye)
                    for a, b in pairs:
                        rematches += b in opponents[a]
                        opponents[a].add(b); opponents[b].add(a)
                        points[a] += rng.choice((0, 3, 6)); points[b] += rng.choice((0, 3, 6))
                if count >= 7:
                    self.assertEqual(rematches, 0)


class QueueSlots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.patch = patch("arena.sandbox.image_id", return_value="sha256:" + "1" * 64)
        self.patch.start()
        self.service = Service(self.store, start_worker=False)
        self.service.competitions = type("Competitions", (), {"view": lambda _, ident: {"config": {"profile": match_profile(60_000).public()}}})()

    def tearDown(self):
        self.patch.stop()
        self.store.db.close()
        self.tmp.cleanup()

    def submission(self, team="team-a", version="v1.0.0"):
        return self.store.create("submission", team, {"version": version, "runtime": "cpp17", "protocol_version": 2,
                                                     "source": {"zip_sha256": uuid.uuid4().hex}})["id"]

    def benchmark(self, submission, **extra):
        return self.service.enqueue("team-a", "benchmark", {"submission_id": submission, "match_ms": 60_000, **extra}, uuid.uuid4().hex)

    def test_newer_request_replaces_only_a_waiting_job_with_the_same_purpose(self):
        v1, v2 = self.submission(), self.submission(version="v1.0.1")
        first = self.benchmark(v1, competition_id="c" * 32)
        other = self.benchmark(v1, competition_id="d" * 32)
        self.assertEqual(self.benchmark(v1, competition_id="c" * 32)["id"], first["id"])   # same request keeps its place
        newer = self.benchmark(v2, competition_id="c" * 32)
        self.assertEqual(self.store.get(first["id"])["error"]["code"], "superseded")
        self.assertEqual(self.store.get(other["id"])["status"], "queued")
        self.assertEqual(self.store.get(newer["id"])["status"], "queued")
        self.assertEqual(self.service.pending.qsize(), 2)

    def test_running_jobs_are_never_replaced_and_waiting_jobs_are_capped(self):
        v1 = self.submission()
        running = self.benchmark(v1, competition_id="c" * 32)
        self.assertEqual(self.service.pending.get_nowait(), running["id"])
        self.benchmark(v1, competition_id="c" * 32)
        self.assertEqual(self.store.get(running["id"])["status"], "starting")
        for index in range(MAX_QUEUED_PER_TEAM - 1):
            self.benchmark(v1, competition_id=f"{index:032x}")
        with self.assertRaises(Fault) as caught:
            self.benchmark(v1, competition_id="e" * 32)
        self.assertEqual(caught.exception.code, "quota_exceeded")

    def test_practice_request_with_another_clock_is_new_and_replaces_the_waiting_one(self):
        red, blue = self.submission(), self.submission(version="v1.0.1")
        body = {"red_submission_id": red, "blue_submission_id": blue, "delegate_timeout_claims": True}
        bullet = self.service.enqueue("team-a", "match", {**body, "match_ms": 60_000}, uuid.uuid4().hex)
        self.assertEqual(self.service.enqueue("team-a", "match", {**body, "match_ms": 60_000}, uuid.uuid4().hex)["id"], bullet["id"])
        normal = self.service.enqueue("team-a", "match", {**body, "match_ms": 300_000}, uuid.uuid4().hex)
        self.assertNotEqual(normal["id"], bullet["id"])
        self.assertEqual(normal["profile"]["match_ms"], 300_000)
        self.assertEqual(self.store.get(bullet["id"])["error"]["code"], "superseded")

    def test_unready_worker_runtime_refuses_work_without_creating_a_job(self):
        submission = self.submission()
        with patch.object(self.service.runtime_ready, "wait", return_value=False):
            with self.assertRaises(Fault) as caught:
                self.benchmark(submission)
        self.assertEqual(caught.exception.code, "worker_unavailable")
        self.assertEqual(self.store.list("job", "team-a"), [])
        self.assertEqual(self.service.pending.qsize(), 0)

    def test_in_place_retry_stops_once_the_board_is_public(self):
        job = self.benchmark(self.submission())
        self.assertTrue(self.service.may_retry_in_place(job["id"]))
        self.store.event(job["id"], "state", {"state": engine.initial_state()})
        self.assertFalse(self.service.may_retry_in_place(job["id"]))


class PracticeWithSampleBots(FlowHarness):
    def test_sample_side_choice_and_only_the_sample_side_is_redacted(self):
        team, issued = self.create_team()
        team_id = issued["account"]["team_id"]
        own = self.store.create("submission", team_id, {"version": "v1.0.0", "runtime": "cpp17", "protocol_version": 2, "source": {}})
        sample_source = self.store.create("submission", "sample:x", {"version": "v1.0.0", "runtime": "cpp17", "protocol_version": 2, "source": {}})
        sample = self.store.create("sample_bot", "organizer", {"name": "Mẫu", "level": "easy", "runtime": "cpp17", "protocol_version": 2,
                                                               "enabled": True, "submission_id": sample_source["id"], "owner": "sample:x"})
        job = self.post(team, "/matches", {"red_submission_id": own["id"], "sample_bot_id": sample["id"], "sample_side": "red",
                                           "delegate_timeout_claims": True}, expected=202)
        self.assertEqual(job["submission_ids"], [sample_source["id"], own["id"]])
        stored = self.store.get(job["id"])
        stored["report"] = {"artifacts": [{"log": "secret-sample"}, {"log": "own-log"}],
                            "sandbox_metrics": [{"stderr": "secret-sample"}, {"stderr": "own-stderr"}],
                            "fault": {"side": "blue", "detail": "own bot crashed"}}
        stored["error"] = {"code": "build_error", "detail": "own compile error", "side": "blue"}
        visible = public_job(stored, full=True)
        self.assertNotIn("secret-sample", str(visible))
        self.assertIn("own-stderr", str(visible))
        self.assertEqual(visible["report"]["fault"]["detail"], "own bot crashed")
        self.assertEqual(visible["error"]["detail"], "own compile error")
        stored["error"] = {"code": "build_error", "detail": "sample compile error", "side": "red"}
        self.assertEqual(public_job(stored, full=True)["error"]["detail"], PRIVATE_SAMPLE)
        failed = private_event(stored, {"type": "failed", "data": {"detail": "sample compile error", "side": "red"}})
        self.assertEqual(failed["data"]["detail"], PRIVATE_SAMPLE)


class SwissIsolation(FlowHarness):
    def test_a_failed_pairing_pauses_only_its_competition_and_resume_retries(self):
        comp = self.post(self.client, "/admin/competitions", {"title": "Swiss lẻ", "format": "swiss", "max_teams": 64, "rounds": 3}, expected=201)
        cid = comp["id"]
        self.post(self.client, f"/admin/competitions/{cid}/open")
        for i in range(5):
            client, issued = self.create_team("Swiss " + str(i), "swiss-" + str(i))
            self.post(client, f"/competitions/{cid}/entry", self.qualified(issued["account"]["team_id"], comp["config"]))
        self.post(self.client, f"/admin/competitions/{cid}/lock")
        self.post(self.client, f"/admin/competitions/{cid}/start")
        with patch("arena_api.swiss.pair", side_effect=Fault("competition_conflict", "forced pairing failure")):
            for _ in range(4):
                self.comp.tick()
                ident = self.app.state.service.pending.get_nowait()
                self.app.state.service.pending.task_done()
                self.finish(self.store.get(ident))          # finish() runs reconcile(); it must not raise
            item = self.comp.get(cid)
            self.assertEqual(item["status"], "paused")
            self.assertIn("forced", item["config"]["pairing_error"])
            self.comp.reconcile()                          # no retry storm while waiting for the organiser
            self.assertEqual(self.comp.get(cid)["config"]["current_round"], 1)
        self.post(self.client, f"/admin/competitions/{cid}/start")
        self.comp.reconcile()
        item = self.comp.get(cid)
        self.assertEqual((item["status"], item["config"]["current_round"]), ("running", 2))
        self.assertNotIn("pairing_error", item["config"])
        standings = self.comp.standings(cid, True)["items"]
        byes = [row for row in standings if row.get("bye_points")]
        self.assertEqual(len(byes), 2)                     # rounds 1 and 2, two different teams
        for row in byes:
            self.assertEqual(row["bye_points"], 6)         # a bye scores like winning both games of the round
            self.assertEqual(row["played"], row["wins"] + row["draws"] + row["losses"])  # and is not a played game


if __name__ == "__main__":
    unittest.main()
