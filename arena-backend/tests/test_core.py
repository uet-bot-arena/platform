from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from arena import engine
from arena.archive import unpack
from arena.config import profile
from arena.protocol import Fault, response, strict_json
from arena.service import Service, fixtures, digest
from arena.store import Store


def empty():
    return {"board": [[None for _ in range(9)] for _ in range(9)], "to_move": "red", "ply": 0, "result": None}


def put(state, cell, side, kind):
    r, c = engine.position(cell)
    state["board"][r][c] = {"side": side, "kind": kind}


def archive(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class Rules(unittest.TestCase):
    def test_initial_placement_and_coordinates(self):
        s = engine.initial_state()
        self.assertEqual(engine.position("a1"), (8, 0))
        self.assertEqual(engine.position("i9"), (0, 8))
        self.assertIsNone(s["board"][8][0])
        self.assertIsNone(s["board"][0][8])
        self.assertEqual(s["to_move"], "red")
        self.assertEqual(engine.counts(s), {side: {kind: 3 for kind in engine.BEATS} for side in engine.SIDES})
        self.assertEqual(s["board"][7][0], {"side": "red", "kind": "scissors"})
        self.assertEqual(s["board"][0][1], {"side": "blue", "kind": "rock"})
        for row in range(9):
            for col in range(9):
                self.assertEqual(engine.position(engine.square(row, col)), (row, col))

    def test_eight_directions_one_step_and_immutable_input(self):
        s = empty()
        put(s, "e5", "red", "rock")
        moves = engine.legal_moves(s)
        self.assertEqual(len(moves), 8)
        self.assertNotIn({"from": "e5", "to": "e7"}, moves)
        before = json.dumps(s)
        engine.apply(s, moves[0])
        self.assertEqual(json.dumps(s), before)

    def test_all_capture_pairs_and_friendly_blockers(self):
        for attacker in engine.BEATS:
            for defender in engine.BEATS:
                for side in engine.SIDES:
                    s = empty()
                    put(s, "e5", "red", attacker)
                    put(s, "e6", side, defender)
                    move = {"from": "e5", "to": "e6"}
                    allowed = side == "blue" and engine.BEATS[attacker] == defender
                    self.assertEqual(move in engine.legal_moves(s), allowed)
                    if not allowed:
                        with self.assertRaises(ValueError):
                            engine.apply(s, move)

    def test_base_priority_over_type_elimination(self):
        s = empty()
        put(s, "h8", "red", "rock")
        put(s, "i9", "blue", "scissors")
        result = engine.apply(s, {"from": "h8", "to": "i9"})
        self.assertEqual(result["result"], {"winner": "red", "reason": "base_capture"})

    def test_type_elimination_even_with_other_pieces_remaining(self):
        s = empty()
        put(s, "e5", "red", "rock")
        put(s, "e6", "blue", "scissors")
        put(s, "a8", "blue", "rock")
        put(s, "b8", "blue", "paper")
        result = engine.apply(s, {"from": "e5", "to": "e6"})
        self.assertEqual(result["result"]["reason"], "type_eliminated")
        self.assertEqual(sum(engine.counts(result)["blue"].values()), 2)

    def test_all_eliminated_priority_and_resign(self):
        s = empty()
        put(s, "e5", "red", "rock")
        put(s, "e6", "blue", "scissors")
        self.assertEqual(engine.apply(s, {"from": "e5", "to": "e6"})["result"]["reason"], "all_pieces_eliminated")
        self.assertEqual(engine.apply(s, {"resign": True})["result"], {"winner": "blue", "reason": "resignation"})
        with self.assertRaises(ValueError):
            engine.apply(s, {"resign": 1})

    def test_stalemate(self):
        s = empty()
        # Three separate trapped blue types; a remote red move cannot create
        # an escape square next to any of them.
        for cell, kind, blocker in (("a5", "rock", "paper"), ("i5", "paper", "scissors"), ("e9", "scissors", "rock")):
            r, c = engine.position(cell)
            put(s, cell, "blue", kind)
            for dr, dc in engine.DIRECTIONS:
                if 0 <= r + dr < 9 and 0 <= c + dc < 9:
                    put(s, engine.square(r + dr, c + dc), "red", blocker)
        put(s, "e1", "red", "paper")
        result = engine.apply(s, {"from": "e1", "to": "e2"})
        self.assertEqual(result["result"]["reason"], "no_legal_moves")

    def test_public_suite_reproducible_and_nonterminal(self):
        self.assertEqual(digest(fixtures()), digest(fixtures()))
        self.assertEqual(len(fixtures()), 24)
        for case in fixtures():
            self.assertIsNone(case["state"]["result"])
            self.assertTrue(engine.legal_moves(case["state"]))


class Protocol(unittest.TestCase):
    def test_strict_json_and_stale_responses(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff'):
            with self.assertRaises(Fault):
                strict_json(raw)
        expected = {"match_id": "match-a", "turn_id": "2", "state_version": 7}
        good = {"protocol_version": 2, "type": "action", **expected, "payload": {"from": "a2", "to": "b3"}}
        self.assertEqual(response(json.dumps(good), expected), good)
        for key, value in [("match_id", "match-b"), ("turn_id", "1"), ("state_version", True), ("state_version", 6), ("protocol_version", True), ("extra", 1)]:
            with self.assertRaises(Fault):
                response(json.dumps({**good, key: value}), expected)

    def test_time_profiles_and_no_client_budget_override(self):
        for ms in (15000, 30000, 60000, 300000):
            self.assertEqual(profile(ms).grace_ms, 3000)
        for value in (True, 15, 15000.0, "15000"):
            with self.assertRaises(ValueError):
                profile(value)


class Upload(unittest.TestCase):
    def test_flat_sources_only_and_zip_limits(self):
        cases = [{"../bot.cpp": b"x"}, {"/bot.cpp": b"x"}, {"folder/bot.cpp": b"x"}, {"bot.cpp": b"x", "bot.exe": b"x"}, {"x.cpp": b"x"}]
        with tempfile.TemporaryDirectory() as tmp:
            for i, files in enumerate(cases):
                with self.assertRaises(Fault):
                    unpack(archive(files), Path(tmp) / str(i), profile())
            with self.assertRaises(Fault):
                unpack(archive({"bot.cpp": b"a" * 10000}), Path(tmp) / "bomb", replace(profile(), max_source_bytes=100))
            manifest = unpack(archive({"bot.cpp": b"int main(){}"}), Path(tmp) / "good", profile())
            self.assertEqual(len(manifest["zip_sha256"]), 64)

    def test_duplicate_and_symlink_rejected(self):
        for special in (False, True):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("bot.cpp", "x")
                entry = zipfile.ZipInfo("x.hpp" if special else "bot.cpp")
                if special:
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(entry, "x")
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(Fault):
                unpack(buf.getvalue(), Path(tmp) / "source", profile())


class Persistence(unittest.TestCase):
    def test_ownership_idempotency_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            s = Service(store, start_worker=False)
            raw = archive({"bot.cpp": "int main(){}"})
            a = s.submit("team-a", "v1.0.0", "cpp17", raw, "submission-key-1")
            b = s.submit("team-a", "v1.0.0", "cpp17", raw, "submission-key-1")
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(store.list("submission", ""), [])
            with self.assertRaises(Fault):
                store.get(a["id"], "team-b")
            with self.assertRaises(Fault):
                s.submit("team-a", "v1.0.1", "cpp17", raw, "submission-key-1")
            job = store.create("job", "team-a", {"status": "playing"})
            for n in range(3):
                self.assertEqual(store.event(job["id"], "state", {"n": n})["seq"], n + 1)
            self.assertEqual([e["seq"] for e in store.events(job["id"], 1)], [2, 3])
            store.db.close()
            reopened = Store(tmp)
            self.assertEqual(reopened.get(job["id"])["status"], "aborted")
            self.assertEqual(reopened.get(a["id"])["source"], a["source"])
            reopened.db.close()

    def test_worker_unavailable_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            service = Service(store, start_worker=False)
            s = service.submit("a", "v1.0.0", "cpp17", archive({"bot.cpp": "x"}), "source-key-1")
            with patch("arena.sandbox.image_id", side_effect=Fault("worker_unavailable", "offline")), self.assertRaises(Fault):
                service.enqueue("a", "benchmark", {"submission_id": s["id"]}, "job-key-1")
            self.assertEqual(store.list("job"), [])
            store.db.close()


if __name__ == "__main__":
    unittest.main()
