"""Real Docker + HTTP integration, fails rather than skips an unavailable worker."""
from dataclasses import replace
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import engine, sandbox
from arena.config import profile
from arena.protocol import Fault
from arena.server import Application, handler

TOKEN = "integration-only-team-a-token-0001"
OTHER = "integration-only-team-b-token-0002"


def zipped(source):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bot.cpp", source)
    return out.getvalue()


def main():
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    results = {"started_unix_ms": time.time_ns() // 1_000_000, "checks": []}
    def checked(name, **data):
        results["checks"].append({"name": name, "passed": True, **data})
        print("PASS", name, flush=True)
    image = sandbox.image_id()
    results["image_id"] = image
    source = (ROOT / "sdk" / "bot.cpp").read_text()
    with tempfile.TemporaryDirectory(prefix="integration-", dir=output) as tmp:
        app = Application(tmp, {TOKEN: "a", OTHER: "b"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        def request(path, body=None, token=TOKEN, headers=None, status=200):
            hdr = {"Authorization": "Bearer " + token} if token else {}
            if isinstance(body, dict):
                if path in ("/benchmarks", "/matches") and "turn_ms" not in body and "match_ms" not in body:
                    body = {**body, "turn_ms": 15000}  # Explicit legacy deadline regression.
                body = json.dumps(body).encode()
                hdr["Content-Type"] = "application/json"
            if body is not None:
                hdr["Idempotency-Key"] = uuid.uuid4().hex
            hdr.update(headers or {})
            req = urllib.request.Request(base + "/api/v1" + path, data=body, headers=hdr)
            try:
                response = urllib.request.urlopen(req, timeout=20)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                data = response.read()
                assert response.status == status, (path, response.status, data[:2000])
                return json.loads(data)
        def upload(version, code):
            return request("/submissions", zipped(code), headers={"Content-Type": "application/zip", "X-Submission-Version": version}, status=201)
        def wait(job, timeout=120):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                item = request("/jobs/" + job["id"])
                if item["status"] in ("completed", "failed", "aborted"):
                    return item
                time.sleep(0.1)
            raise AssertionError("Job did not finish: " + job["id"])
        try:
            assert request("/health", token=None)["ready"]
            clock = request("/time", token=None)
            assert clock["server_sent_unix_ms"] >= clock["server_received_unix_ms"]
            request("/submissions", token=None, status=401)
            request("/config", headers={"Origin": "https://evil.invalid"}, status=403)
            checked("auth_origin_and_clock_api")
            good = upload("v1.0.0", source)
            request("/jobs", token=OTHER)
            bad_access = request("/benchmarks", {"submission_id": good["id"]}, token=OTHER, status=404)
            assert bad_access["error"]["code"] == "not_found"
            checked("submission_ownership")
            bench = wait(request("/benchmarks", {"submission_id": good["id"]}, status=202))
            assert bench["status"] == "completed", bench
            report = request("/jobs/" + bench["id"] + "/report")
            assert len(report["samples"]) == 72 and report["passed"]
            assert report["turn_wall_ms"]["samples"] == 72
            assert report["image_id"] == image and report["engine_sha256"] == engine.ENGINE_SHA256
            for sample in report["samples"]:
                assert int(sample["received_mono_ns"]) < int(sample["deadline_mono_ns"])
                assert sample["cgroup_cpu_ms"] >= 0
            results["benchmark"] = report
            checked("real_cpp_build_and_72_sample_benchmark", summary=report["turn_wall_ms"])
            match = wait(request("/matches", {"red_submission_id": good["id"], "blue_submission_id": good["id"], "delegate_timeout_claims": True}, status=202))
            assert match["status"] == "completed" and match["report"]["result"], match
            events = request("/jobs/" + match["id"] + "/events")["items"]
            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
            states = [e["data"]["state"] for e in events if e["type"] == "state"]
            assert states[0] == engine.initial_state() and states[-1] == match["report"]["final_state"]
            cursor = events[len(events) // 2]["seq"]
            resumed = request(f"/jobs/{match['id']}/events?after={cursor}")["items"]
            assert resumed == [e for e in events if e["seq"] > cursor]
            # SSE must honor Last-Event-ID and scope, not just JSON polling.
            req = urllib.request.Request(base + f"/api/v1/jobs/{match['id']}/events", headers={"Authorization": "Bearer " + TOKEN, "Accept": "text/event-stream", "Last-Event-ID": str(cursor)})
            with urllib.request.urlopen(req, timeout=5) as stream:
                wire = stream.read().decode()
            assert f"id: {cursor + 1}\n" in wire and "event: arena" in wire
            results["match"] = match["report"]
            checked("real_ottv2_match_event_replay_and_sse_reconnect", result=match["report"]["result"], plies=states[-1]["ply"])
            broken = upload("v1.0.1", "int main( { broken")
            failed = wait(request("/benchmarks", {"submission_id": broken["id"]}, status=202))
            assert failed["status"] == "failed" and failed["error"]["code"] == "build_error", failed
            checked("compiler_error_reported_without_running")
            # Direct worker tests use a short INTERNAL test profile; the public
            # API still only allows the four room time choices from the rules.
            short = replace(profile(), turn_ms=60, startup_ms=1000)
            fault_sources = {
                "timeout": source.replace('#include <string>', '#include <string>\n#include <unistd.h>').replace('const auto legal =', 'usleep(200000); const auto legal ='),
                "stale_action": source.replace('{"turn_id",m.at("turn_id")}', '{"turn_id","old-turn"}'),
                "output_limit": source.replace('const auto legal =', 'std::cout << std::string(70000,\'x\') << std::flush; const auto legal ='),
            }
            for n, (expected, code) in enumerate(fault_sources.items()):
                sub = upload(f"v2.0.{n}", code)
                job = {"id": "fault-" + str(n), "image_id": image}
                # build event requires a job record for consistent audit.
                job = app.store.create("job", "a", {"status": "validating", "image_id": image})
                folder, _ = app.service.ensure_artifact(job, app.store.get(sub["id"]), short)
                worker = sandbox.Worker(folder, short, image)
                try:
                    worker.start(job["id"], "red")
                    top = sandbox.command(["docker", "top", worker.name, "-eo", "pid,user,stat,comm"]).decode()
                    assert any("bot" in line and "T" in line.split()[2] for line in top.splitlines()[1:]), top
                    starts = []
                    try:
                        worker.turn(app.service.message(job, 1, engine.initial_state()), starts.append)
                        raise AssertionError("Expected fault " + expected)
                    except Fault as exc:
                        assert exc.code == expected, (expected, exc.code, exc.detail)
                        if expected == "timeout":
                            assert int(exc.worker_data["worker_mono_ns"]) >= int(starts[0]["deadline_mono_ns"])
                finally:
                    worker.close()
                app.store.update(job["id"], status="completed")
                checked("worker_" + expected + "_and_offturn_freeze")
            secure = source.replace('#include <string>', '#include <string>\n#include <unistd.h>\n#include <sys/socket.h>\n#include <signal.h>').replace('const auto legal =', 'if (fork()!=-1 || socket(AF_INET,SOCK_STREAM,0)!=-1 || kill(getppid(),0)!=-1) return 90; const auto legal =')
            sub = upload("v3.0.0", secure)
            checked_job = wait(request("/benchmarks", {"submission_id": sub["id"]}, status=202))
            assert checked_job["status"] == "completed", checked_job
            checked("bot_cannot_fork_open_socket_or_signal_supervisor")
            delayed = source.replace('#include <string>', '#include <string>\n#include <unistd.h>').replace('const auto legal =', 'usleep(16000000); const auto legal =')
            late = upload("v4.0.0", delayed)
            lost = wait(request("/matches", {"red_submission_id": late["id"], "blue_submission_id": good["id"], "delegate_timeout_claims": True}, status=202))
            assert lost["status"] == "completed" and lost["report"]["result"] == {"winner": "blue", "reason": "timeout"}, lost
            log = request("/jobs/" + lost["id"] + "/events")["items"]
            pending = next(e for e in log if e["type"] == "timeout_pending")
            claimed = next(e for e in log if e["type"] == "timeout_claimed")
            # UTC timestamps are display-only and can slew/jump in WSL/NTP.
            # Verify the actual monotonic wait, not a subtraction of wall clocks.
            assert claimed["data"]["grace_clock"] == "controller-monotonic"
            assert claimed["data"]["grace_waited_ms"] >= 3000, claimed
            assert lost["report"]["samples"] == []
            checked("15s_hard_deadline_plus_3s_delegated_claim_no_late_action",
                    grace_monotonic_ms=claimed["data"]["grace_waited_ms"],
                    grace_display_clock_ms=claimed["server_unix_ms"] - pending["server_unix_ms"])
        finally:
            app.service.stop.set()
            server.shutdown()
            server.server_close()
            app.service.worker_thread.join(timeout=15)
            app.store.db.close()
    results["passed"] = True
    results["completed_unix_ms"] = time.time_ns() // 1_000_000
    (output / "latest.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("ALL INTEGRATION CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
