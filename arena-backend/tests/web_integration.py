"""Real Uvicorn HTTP + persistent auth + Docker worker, no execution mocks."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import uuid

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import engine, sandbox
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings


def main():
    image = sandbox.image_id('uet-arena-worker:clock-v2')  # Intentionally fails if Docker/image is absent.
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    report = {"image_id": image, "started_ms": time.time_ns() // 1_000_000, "checks": []}

    def checked(name, **data):
        report["checks"].append({"name": name, "passed": True, **data})
        print("PASS", name, flush=True)

    with tempfile.TemporaryDirectory(prefix="web-integration-", dir=output) as directory:
        store = Store(directory, recover_jobs=False)
        Accounts(store).bootstrap_admin("organizer", "integration-only-admin-password")
        store.db.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        settings = Settings(data=Path(directory), origin=f"http://127.0.0.1:{sock.getsockname()[1]}")
        server = uvicorn.Server(uvicorn.Config(create_app(settings), access_log=False, log_level="warning", proxy_headers=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        admin = httpx.Client(base_url=settings.origin, timeout=30)
        team = httpx.Client(base_url=settings.origin, timeout=30)
        try:
            until = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < until:
                time.sleep(0.05)
            assert server.started, "Uvicorn did not start"

            def request(client, method, path, expected=200, **kwargs):
                response = client.request(method, "/api/v1" + path, **kwargs)
                assert response.status_code == expected, (path, response.status_code, response.text[:1000])
                return response

            def login(client, username, password):
                data = request(client, "POST", "/auth/login", json={"username": username, "password": password}).json()
                client.headers["X-CSRF-Token"] = data["csrf_token"]
                return data

            health = request(admin, "GET", "/health/ready")
            assert health.json()["ready"] and health.headers["X-Request-ID"]
            schema = admin.get("/openapi.json").json()
            assert schema["paths"]["/api/v1/matches"]["post"]["security"]
            checked("asgi_readiness_openapi_request_ids")
            login(admin, "organizer", "integration-only-admin-password")
            data = {"team": "Integration Team", "email": "integration@example.test", "members": [
                {"name": f"Student {i}", "student_id": f"2602000{i}"} for i in range(3)], "confirmed": True}
            registration = request(team, "POST", "/registrations", expected=201, json=data).json()
            issued = request(admin, "POST", f"/admin/registrations/{registration['id']}/approve", expected=201, json={"username": "integration-team"}).json()
            login(team, "integration-team", issued["temporary_password"])
            request(team, "GET", "/teams/me", expected=403)
            changed = request(team, "POST", "/auth/password", json={"current_password": issued["temporary_password"], "new_password": "integration-only-team-new-password"}).json()
            team.headers["X-CSRF-Token"] = changed["csrf_token"]
            assert request(team, "GET", "/teams/me").json()["name"] == "Integration Team"
            request(team, "GET", "/admin/accounts", expected=403)
            checked("registration_approval_temporary_password_rotation_rbac")

            starter = request(team, "GET", "/starter.zip").content
            upload_headers = {"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0", "Idempotency-Key": uuid.uuid4().hex}
            source = request(team, "POST", "/submissions", expected=201, content=starter, headers=upload_headers).json()
            assert "source_path" not in source
            duplicate = request(team, "POST", "/submissions", expected=201, content=starter, headers=upload_headers).json()
            assert duplicate["id"] == source["id"]
            checked("authenticated_source_upload_and_idempotency")

            def wait(job):
                end = time.monotonic() + 120
                while time.monotonic() < end:
                    result = request(team, "GET", "/jobs/" + job["id"]).json()
                    if result["status"] in ("completed", "failed", "aborted"):
                        assert result["status"] == "completed", result.get("error")
                        return result
                    time.sleep(0.1)
                raise AssertionError("Job did not complete")

            benchmark = request(team, "POST", "/benchmarks", expected=202, json={"submission_id": source["id"]}, headers={"Idempotency-Key": uuid.uuid4().hex}).json()
            benchmark = wait(benchmark)["report"]
            assert benchmark["passed"] and benchmark["turn_wall_ms"]["samples"] == 72
            assert benchmark["image_id"] == image
            report["benchmark"] = benchmark["turn_wall_ms"]
            checked("authenticated_native_cpp_benchmark_72_samples", summary=report["benchmark"])

            match = request(team, "POST", "/matches", expected=202, json={"red_submission_id": source["id"], "blue_submission_id": source["id"], "delegate_timeout_claims": True}, headers={"Idempotency-Key": uuid.uuid4().hex}).json()
            match = wait(match)
            ident = match["id"]
            events = request(team, "GET", f"/jobs/{ident}/events").json()["items"]
            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
            states = [e["data"]["state"] for e in events if e["type"] == "state"]
            assert states[0] == engine.initial_state()
            assert states[-1] == match["report"]["final_state"]
            cursor = events[len(events) // 2]["seq"]
            wire = request(team, "GET", f"/jobs/{ident}/events", headers={"Accept": "text/event-stream", "Last-Event-ID": str(cursor)}).text
            assert f"id: {cursor + 1}\n" in wire and f"id: {cursor}\n" not in wire
            actual_report = request(team, "GET", f"/jobs/{ident}/report").json()
            assert actual_report["result"] == match["report"]["result"]
            checked("authenticated_match_replay_and_sse_resume", result=actual_report["result"], plies=states[-1]["ply"])
            metrics = request(admin, 'GET', '/admin/metrics').json()
            assert metrics['worker']['ready'] and metrics['jobs']['by_status']['completed'] == 2
            assert metrics['last_24h']['benchmark_pass_rate'] == 100
            assert metrics['last_24h']['benchmark_p95_max_ms'] == benchmark['turn_wall_ms']['p95_ms']
            all_jobs = request(admin, 'GET', '/admin/jobs?status=completed&kind=benchmark').json()
            assert all_jobs['total'] == 1 and 'report' not in all_jobs['items'][0]
            assert request(admin, 'GET', '/admin/jobs/'+match['id']).json()['report']['result'] == actual_report['result']
            sources = request(admin, 'GET', '/admin/submissions').json()['items']
            assert len(sources) == 1 and 'source_path' not in sources[0]
            request(team, 'GET', '/admin/metrics', expected=403)
            checked('organizer_metrics_and_reports_match_real_worker_results')
            request(admin, "POST", f"/admin/accounts/{issued['account']['id']}/password", json={})
            request(team, "GET", "/auth/me", expected=401)
            audit = request(admin, "GET", "/admin/audit").text
            assert "integration-only" not in audit and "temporary_password" not in audit
            checked("admin_password_reset_revokes_session_without_secret_audit")
            report["passed"] = True
        finally:
            admin.close()
            team.close()
            server.should_exit = True
            thread.join(timeout=360)
            sock.close()
            assert not thread.is_alive(), "API failed to release its controller lease"
    report["completed_ms"] = time.time_ns() // 1_000_000
    (output / "web-api-latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ALL WEB API INTEGRATION CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
