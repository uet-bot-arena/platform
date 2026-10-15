"""Verify the published guide example against the existing Docker worker.

--prepare regenerates the downloadable source/input from the guide and engine.
The normal check uses an isolated temporary store, never the local platform DB.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import engine, sandbox
from arena.adjudication import CURRENT
from arena.config import match_profile as profile
from arena.protocol import response
from arena.service import Service
from arena.store import Store

LIVE = ROOT.parent / "uet-bot-arena" / "dist" / "live"


def examples():
    guide = (LIVE / "bot-guide.md").read_text(encoding="utf-8")
    source = guide.split("```cpp\n", 1)[1].split("\n```", 1)[0] + "\n"
    context = {"kind": "benchmark", "adjudication": dict(CURRENT)}
    init = {"protocol_version": 2, "type": "init", "match_id": "example-match",
            "payload": {"side": "red", "engine": engine.ENGINE_ID,
                        "profile": profile().public(), "match": context},
            "timing": {"clock_id": "example-worker", "started_mono_ns": "1000000000",
                       "deadline_mono_ns": "3000000000", "budget_ms": 2000}}
    observation = {"protocol_version": 2, "type": "observation", "match_id": "example-match",
                   "turn_id": "turn-A", "state_version": 0,
                   "payload": {**engine.observation(engine.initial_state()), "adjudication": dict(CURRENT),
                               "clocks": {"red":300000,"blue":300000}},
                   "timing": {"clock_id": "example-worker", "started_mono_ns": "4000000000",
                              "deadline_mono_ns": "304000000000", "budget_ms": 300000,
                              "time_control":"match", "remaining_ms":300000,"initial_ms":300000,"increment_ms":0}}
    return source, [init, observation]


def main():
    source, messages = examples()
    input_text = "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in messages)
    if "--prepare" in sys.argv:
        (LIVE / "bot-example.cpp").write_text(source, encoding="utf-8", newline="\n")
        (LIVE / "ottv2-input.jsonl").write_text(input_text, encoding="utf-8", newline="\n")
        print("Prepared downloadable C++ source and two-frame input")
        return
    assert (LIVE / "bot-example.cpp").read_text(encoding="utf-8") == source, "Guide/source mismatch"
    assert (LIVE / "ottv2-input.jsonl").read_text(encoding="utf-8") == input_text, "Engine/input mismatch"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("bot.cpp", source)
    results = ROOT / "test-results"
    results.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bot-guide-", dir=results) as directory:
        store = Store(directory)
        try:
            service = Service(store, start_worker=False)
            submission = service.submit("guide-check", "v1.0.0", "cpp17", archive.getvalue(), "guide-source")
            job = service.enqueue("guide-check", "benchmark",
                                  {"submission_id": submission["id"], "match_ms": 300000}, "guide-benchmark")
            service.execute(job)
            job = store.get(job["id"])
            report = job["report"]
            assert job["status"] == "completed" and report["passed"] is True, job
            assert len(report["samples"]) == 72, report
            print("PASS guide C++ builds and passes all 72 benchmark samples", flush=True)
            artifact = store.root / "artifacts" / report["artifacts"][0]["build_key"]
            worker = sandbox.Worker(artifact, profile(), job["image_id"])
            try:
                ready = worker.start("example-match", "red", messages[0]["payload"]["match"])
                response(json.dumps(ready["message"]), messages[0], kind="ready")
                action = worker.turn(messages[1])["message"]
                response(json.dumps(action), messages[1])
                assert action["payload"] in messages[1]["payload"]["legal_moves"]
                engine.apply(messages[1]["payload"]["state"], action["payload"])
            finally:
                worker.close()
            proof = {"passed": True, "scope": "guide sample only; isolated Docker worker/store",
                     "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                     "image_id": job["image_id"], "samples": len(report["samples"]),
                     "turn_wall_ms": report["turn_wall_ms"], "sample_ready": ready["message"],
                     "sample_action": action, "profile": job["profile"]}
            (results / "bot-guide-latest.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
            print("PASS downloadable input: handshake, opaque turn ID, exact schema, legal action", flush=True)
        finally:
            store.db.close()


if __name__ == "__main__":
    main()
