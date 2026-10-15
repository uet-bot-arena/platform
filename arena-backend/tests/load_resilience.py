"""Bounded local load and real fault injection; never targets the user's server.

Starts the normal single-process CLI on an ephemeral localhost port and a fresh
database. Kills only its own controller or containers mounted from that database.
Requires the already-built Docker worker image; no execution mocks/fallbacks.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import io
import json
import math
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import sandbox
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.schemas import Registration

PASSWORD = "isolated-load-test-only-password"
OUTPUT = ROOT / "test-results"


def zipped(source):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bot.cpp", source)
    return result.getvalue()


def percentile(values, fraction):
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values) * fraction) - 1)], 3) if values else None


def registration(index):
    return Registration(team=f"Load Team {index}", email=f"load-{index}@example.test",
        members=[{"name": f"Fixture Student {n}", "student_id": f"LT{index:03}{n:03}"}
                 for n in range(3)], confirmed=True).model_dump()


class Harness:
    def __init__(self, root, image, report_prefix="load-resilience"):
        self.root, self.image = Path(root).resolve(), image
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.process = None
        self.log = (OUTPUT / (report_prefix + "-controller.log")).open("w", encoding="utf-8")
        self.auth, self.team_ids, self.account_ids = [], [], []
        self.report = {"started_local": datetime.now(ZoneInfo("Asia/Bangkok")).isoformat(),
            "image_id": image, "environment": {"os": platform.platform(), "logical_cpus": os.cpu_count(),
            "python": platform.python_version(), "server": "normal CLI; one process/worker; concurrency limit 32",
            "transport": "loopback HTTP, closed-loop clients, no browser rendering/network latency"},
            "checks": [], "load": [], "resources": []}

    def checked(self, name, **data):
        self.report["checks"].append({"name": name, "passed": True, **data})
        print("PASS", name, json.dumps(data) if data else "", flush=True)

    def seed(self):
        store = Store(self.root, recover_jobs=False)
        try:
            accounts = Accounts(store)
            admin = accounts.bootstrap_admin("load-admin", PASSWORD)
            for index in range(6):
                record = accounts.register(registration(index), f"offline-fixture-{index}")
                issued = accounts.approve(record["id"], f"load-team-{index}", PASSWORD, admin["id"])
                self.team_ids.append(issued["account"]["team_id"])
                self.account_ids.append(issued["account"]["id"])
            # Fixture setup only. HTTP password-rotation flow is covered by web_integration.py.
            with store.db:
                store.db.execute("UPDATE accounts SET must_change=0")
        finally:
            store.db.close()

    async def start(self, client, missing_image=False):
        env = {**os.environ, "ARENA_API_DATA": str(self.root), "ARENA_API_ORIGIN": self.origin,
               "ARENA_API_COOKIE_SECURE": "0"}
        if missing_image:
            env["ARENA_WORKER_IMAGE"] = "uet-arena-worker:missing-load-test-" + uuid.uuid4().hex
        self.process = subprocess.Popen([sys.executable, "-m", "arena_api", "serve", "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=self.log, stderr=self.log, start_new_session=True)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            assert self.process.poll() is None, "Test controller exited; inspect controller log"
            try:
                result = await client.get(self.origin + "/api/v1/health/live")
                if result.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(.05)
        else:
            raise AssertionError("Test controller failed to start")
        ready = await self.request(client, "GET", "/health/ready", expected=503 if missing_image else 200)
        assert ready.json()["ready"] is not missing_image, ready.text

    async def stop_idle(self):
        self.process.send_signal(signal.SIGTERM)
        await asyncio.to_thread(self.process.wait, 30)
        # Uvicorn re-raises the original signal after graceful lifespan cleanup.
        assert self.process.returncode in (0, -signal.SIGTERM), self.process.returncode

    async def request(self, client, method, path, auth=None, expected=200, **kwargs):
        headers = dict(auth or {})
        headers.update(kwargs.pop("headers", {}))
        if method == "POST":
            headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
            if "json" not in kwargs and "content" not in kwargs:
                kwargs["json"] = {}
        result = await client.request(method, self.origin + "/api/v1" + path, headers=headers, **kwargs)
        assert result.status_code == expected, (path, result.status_code, result.text[:1200])
        assert result.headers.get("X-Request-ID"), path
        return result

    async def login(self, client, username):
        result = await self.request(client, "POST", "/auth/login", json={"username": username, "password": PASSWORD})
        return {"Cookie": "arena_web_session=" + result.cookies["arena_web_session"],
                "X-CSRF-Token": result.json()["csrf_token"]}

    async def upload(self, client, auth, version, source):
        return (await self.request(client, "POST", "/submissions", auth, expected=201, content=zipped(source),
            headers={"Content-Type": "application/zip", "X-Submission-Version": version})).json()

    async def enqueue(self, client, auth, source):
        return (await self.request(client, "POST", "/benchmarks", auth, expected=202,
            json={"submission_id": source["id"]})).json()

    async def wait_job(self, client, auth, ident, statuses=("completed", "failed", "aborted"), timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            item = (await self.request(client, "GET", "/jobs/" + ident, auth)).json()
            if item["status"] in statuses:
                return item
            await asyncio.sleep(.1)
        raise AssertionError("Timed out waiting for test job " + ident)

    def containers(self, running=False):
        # Docker inspection is read-only. Select by exact mount path under our
        # random temporary root, never by image/name prefix shared by real jobs.
        ids = sandbox.command(["docker", "ps", "-q" if running else "-aq"]).decode().split()
        if not ids:
            return []
        result = json.loads(sandbox.command(["docker", "inspect", *ids]))
        own = []
        for item in result:
            for mount in item.get("Mounts", []):
                source = Path(mount["Source"]).resolve()
                if source.is_relative_to(self.root):
                    own.append({"id": item["Id"], "name": item["Name"].lstrip("/"),
                                "source": str(source), "state": item["State"]["Status"]})
                    break
        return own

    def resources(self, phase):
        if self.process is None or self.process.poll() is not None:
            return
        data = Path(f"/proc/{self.process.pid}/status").read_text()
        fields = {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in data.splitlines() if ':' in line}
        self.report["resources"].append({"phase": phase, "pid": self.process.pid,
            "rss_kib": int(fields["VmRSS"].split()[0]), "peak_rss_kib": int(fields["VmHWM"].split()[0]),
            "threads": int(fields["Threads"]), "open_fds": len(list(Path(f"/proc/{self.process.pid}/fd").iterdir()))})

    async def load(self, client, name, concurrency, count=None, seconds=None, paths=None, overload=False):
        paths = paths or ["/teams/me", "/dashboard", "/submissions", "/jobs", "/competitions", "/config"]
        values, statuses, errors = [], Counter(), Counter()
        sent = 0
        start = time.perf_counter()
        async def worker(index):
            nonlocal sent
            while (sent < count if count is not None else time.perf_counter() - start < seconds):
                ordinal = sent
                sent += 1
                path = paths[ordinal % len(paths)]
                then = time.perf_counter()
                try:
                    result = await client.get(self.origin + "/api/v1" + path, headers=self.auth[index % 6])
                    statuses[str(result.status_code)] += 1
                    if result.status_code not in ((200, 503) if overload else (200,)):
                        errors[f"HTTP {result.status_code}"] += 1
                    if result.status_code == 200 and not result.headers.get("X-Request-ID"):
                        errors["missing request ID"] += 1
                except httpx.HTTPError as exc:
                    errors[type(exc).__name__] += 1
                values.append((time.perf_counter() - then) * 1000)
        await asyncio.gather(*(worker(i) for i in range(concurrency)))
        elapsed = time.perf_counter() - start
        item = {"name": name, "clients": concurrency, "requests": sent, "seconds": round(elapsed, 3),
                "requests_per_second": round(sent / elapsed, 2), "p50_ms": percentile(values, .5),
                "p95_ms": percentile(values, .95), "p99_ms": percentile(values, .99),
                "max_ms": round(max(values), 3), "statuses": dict(statuses), "unexpected_errors": dict(errors),
                "expected_overload_503": overload}
        self.report["load"].append(item)
        print("LOAD", json.dumps(item), flush=True)
        assert not errors, item
        self.resources(name)
        return item

    async def sse_viewers(self, client, job, viewers=12):
        connected, release = 0, asyncio.Event()
        event_counts = []
        async def viewer():
            nonlocal connected
            async with client.stream("GET", self.origin + f"/api/v1/jobs/{job['id']}/events",
                headers={**self.auth[0], "Accept": "text/event-stream"}) as stream:
                assert stream.status_code == 200
                async for line in stream.aiter_lines():
                    if line.startswith("id: "):
                        connected += 1
                        event_counts.append(1)
                        await release.wait()
                        break
        tasks = [asyncio.create_task(viewer()) for _ in range(viewers)]
        try:
            async with asyncio.timeout(10):
                while connected < viewers:
                    for task in tasks:
                        if task.done():
                            task.result()
                    await asyncio.sleep(.02)
            self.resources("12 connected SSE viewers")
            await self.load(client, "API with 12 live SSE viewers", 8, count=160)
        finally:
            release.set()
            await asyncio.gather(*tasks)
        current = (await self.request(client, "GET", "/jobs/" + job["id"], self.auth[0])).json()
        assert current["status"] == "benchmarking", current
        self.checked("12_real_sse_connections_and_disconnect_does_not_cancel_job", viewers=viewers)

    async def execute(self, soak_seconds):
        self.seed()
        limits = httpx.Limits(max_connections=80, max_keepalive_connections=0)
        async with httpx.AsyncClient(timeout=30, limits=limits, trust_env=False) as client:
            await self.start(client)
            admin = await self.login(client, "load-admin")
            for i in range(6):
                self.auth.append(await self.login(client, f"load-team-{i}"))
            # Avoid a shared cookie jar mixing accounts in concurrent requests.
            client.cookies.clear()
            source = (ROOT / "sdk/bot.cpp").read_text(encoding="utf-8")
            sources = [await self.upload(client, auth, "v1.0.0", source) for auth in self.auth]
            first = await self.enqueue(client, self.auth[0], sources[0])
            first = await self.wait_job(client, self.auth[0], first["id"])
            assert first["status"] == "completed" and len(first["report"]["samples"]) == 72
            self.checked("real_http_auth_upload_and_native_72_sample_baseline")

            for concurrency in (1, 8, 24):
                await self.load(client, "mixed reads", concurrency, count=240)
            await self.load(client, "bounded sustained reads", 12, seconds=soak_seconds)
            await self.load(client, "admission overload", 48, count=192, overload=True)
            await self.load(client, "recovery after overload", 4, count=80)
            self.checked("bounded_read_load_no_unexpected_http_or_transport_errors")

            # All requests use exactly the same source, version, and idempotency key.
            key = uuid.uuid4().hex
            headers = {"Content-Type": "application/zip", "X-Submission-Version": "v1.0.1", "Idempotency-Key": key}
            retry_zip = zipped(source)
            duplicates = await asyncio.gather(*(self.request(client, "POST", "/submissions", self.auth[0],
                expected=201, content=retry_zip, headers=headers) for _ in range(24)))
            assert len({r.json()["id"] for r in duplicates}) == 1
            conflict = await self.request(client, "POST", "/submissions", self.auth[0], expected=409,
                content=retry_zip, headers={**headers, "X-Submission-Version": "v1.0.2"})
            assert conflict.json()["error"]["code"] == "idempotency_conflict"
            self.checked("24_concurrent_upload_retries_create_exactly_one_version_and_conflict_is_409")

            slow = source.replace("#include <string>", "#include <string>\n#include <unistd.h>").replace(
                "const auto legal =", "usleep(1000000); const auto legal =")
            slow_source = await self.upload(client, self.auth[0], "v1.1.0", slow)
            active = await self.enqueue(client, self.auth[0], slow_source)
            await self.wait_job(client, self.auth[0], active["id"], statuses=("benchmarking",))
            submitted = []
            async def queue_one(i):
                result = await client.post(self.origin + "/api/v1/benchmarks", headers={**self.auth[i % 6],
                    "Idempotency-Key": uuid.uuid4().hex}, json={"submission_id": sources[i % 6]["id"]})
                assert result.status_code in (202, 429), (result.status_code, result.text)
                if result.status_code == 202:
                    submitted.append((i % 6, result.json()))
                else:
                    assert result.json()["error"]["code"] == "quota_exceeded"
                return result.status_code
            queue_statuses = await asyncio.gather(*(queue_one(i) for i in range(30)))
            assert queue_statuses.count(202) == 15 and queue_statuses.count(429) == 15, Counter(queue_statuses)
            active_by_team = Counter(index for index, _ in submitted)
            active_by_team[0] += 1
            assert max(active_by_team.values()) <= 4, active_by_team
            self.checked("30_concurrent_job_requests_enforce_15_waiting_and_4_per_team",
                         statuses=dict(Counter(queue_statuses)), max_active_per_team=max(active_by_team.values()))
            await self.sse_viewers(client, active)

            # Revoke the owner of a queued job while its stream is connected.
            index, queued = next((i, j) for i, j in submitted if i != 0)
            async with client.stream("GET", self.origin + f"/api/v1/jobs/{queued['id']}/events",
                headers={**self.auth[index], "Accept": "text/event-stream"}) as stream:
                assert stream.status_code == 200
                lines = stream.aiter_lines()
                async for line in lines:
                    if line.startswith("id: "):
                        break
                await self.request(client, "POST", f"/admin/accounts/{self.account_ids[index]}/lock", admin,
                                   json={"locked": True})
                async with asyncio.timeout(5):
                    async for line in lines:
                        if line == "event: auth_expired":
                            break
                    else:
                        raise AssertionError("Revoked SSE did not send auth_expired")
            await self.request(client, "GET", "/auth/me", self.auth[index], expected=401)
            await self.request(client, "POST", f"/admin/accounts/{self.account_ids[index]}/lock", admin,
                               json={"locked": False})
            self.auth[index] = await self.login(client, f"load-team-{index}")
            client.cookies.clear()
            self.checked("session_revocation_closes_open_sse_and_rejects_old_rest_session")

            own = await asyncio.to_thread(self.containers, True)
            assert len(own) == 1 and "/artifacts/" in own[0]["source"], own
            await asyncio.to_thread(sandbox.command, ["docker", "kill", own[0]["id"]])
            failed = await self.wait_job(client, self.auth[0], active["id"])
            assert failed["status"] == "failed" and failed["error"]["code"] == "worker_error", failed
            assert not failed["report"].get("result"), failed
            for index, queued in submitted:
                done = await self.wait_job(client, self.auth[index], queued["id"])
                assert done["status"] == "completed" and len(done["report"]["samples"]) == 72, done
            self.checked("killed_worker_is_infrastructure_error_and_all_15_queued_benchmarks_recover")

            bad_sources = {
                "build_error": "int main( { bad source",
                "protocol_error": source.replace("const auto legal =", 'std::cout << "debug\\n" << std::flush; const auto legal ='),
                "stale_action": source.replace('{"turn_id",m.at("turn_id")}', '{"turn_id","wrong-turn"}'),
                "illegal_action": source.replace('const auto action = legal.empty() ? json({{"resign",true}}) : choose_move(m.at("payload").at("state"), legal);',
                                                   'const auto action = json({{"from","a1"},{"to","i9"}});'),
                "crash": source.replace("const auto legal =", "return 23; const auto legal ="),
                "output_limit": source.replace("const auto legal =", "std::cout << std::string(70000,'x') << std::flush; const auto legal ="),
            }
            for number, (code, bad_source) in enumerate(bad_sources.items()):
                bad = await self.upload(client, self.auth[4], f"v3.0.{number}", bad_source)
                job = await self.enqueue(client, self.auth[4], bad)
                done = await self.wait_job(client, self.auth[4], job["id"])
                assert done["status"] == "failed" and done["error"]["code"] == code, done
                self.checked("real_bot_fault_" + code)
            recovered = await self.enqueue(client, self.auth[4], sources[4])
            recovered = await self.wait_job(client, self.auth[4], recovered["id"])
            assert recovered["status"] == "completed" and recovered["report"]["passed"]
            self.checked("healthy_bot_still_passes_after_all_faulty_bots")

            for path, headers, expected in [("/admin/accounts", self.auth[0], 403),
                ("/jobs/" + first["id"], self.auth[1], 404), ("/auth/me", {"Cookie": ""}, 401)]:
                await self.request(client, "GET", path, headers, expected=expected)
            await self.request(client, "POST", "/benchmarks", {"Cookie": self.auth[0]["Cookie"]}, expected=403,
                json={"submission_id": sources[0]["id"]})
            invalid = await self.request(client, "POST", "/submissions", self.auth[0], expected=400, content=b"not a zip",
                headers={"Content-Type": "application/zip", "X-Submission-Version": "v8.0.0"})
            assert invalid.json()["error"]["code"] == "invalid_archive"
            await self.request(client, "POST", "/benchmarks", self.auth[0], expected=400,
                content='{"submission_id":"x","submission_id":"y"}', headers={"Content-Type": "application/json"})
            self.checked("malformed_archive_duplicate_json_csrf_rbac_and_team_isolation_remain_enforced")

            # Qualify a normal bot whose official turns take 250ms. Only the
            # test bot sleeps; real engine, profiles and adjudication stay intact.
            official_source = source.replace("#include <string>", "#include <string>\n#include <unistd.h>").replace(
                "std::string line, match;", "std::string line, match; bool slow_match = false;").replace(
                'match = m.at("match_id");', 'match = m.at("match_id"); slow_match = m.at("payload").at("match").at("kind") == "match";').replace(
                "const auto legal =", "if (slow_match) usleep(250000); const auto legal =")
            qualifications = []
            for i in (0, 1):
                sub = await self.upload(client, self.auth[i], "v4.0.0", official_source)
                job = await self.enqueue(client, self.auth[i], sub)
                job = await self.wait_job(client, self.auth[i], job["id"])
                assert job["status"] == "completed" and job["report"]["passed"], job
                qualifications.append((sub, job))
            comp = (await self.request(client, "POST", "/admin/competitions", admin, expected=201,
                json={"title": "Isolated restart fault cup", "max_teams": 2})).json()
            cid = comp["id"]
            await self.request(client, "POST", f"/admin/competitions/{cid}/open", admin)
            for i, (sub, job) in enumerate(qualifications):
                await self.request(client, "POST", f"/competitions/{cid}/entry", self.auth[i],
                    json={"submission_id": sub["id"], "benchmark_id": job["id"], "delegate_timeout_claims": True})
            await self.request(client, "POST", f"/admin/competitions/{cid}/lock", admin)
            fixtures = (await self.request(client, "GET", f"/competitions/{cid}/fixtures", self.auth[0])).json()["items"]
            fixture = fixtures[0]["id"]
            await self.request(client, "POST", f"/admin/competitions/{cid}/start", admin)
            async with asyncio.timeout(25):
                while True:
                    attempts = (await self.request(client, "GET", f"/admin/competitions/{cid}/fixtures/{fixture}/attempts", admin)).json()["items"]
                    if attempts:
                        attempt = (await self.request(client, "GET", f"/admin/competitions/{cid}/fixtures/{fixture}/attempts/1", admin)).json()
                        if attempt["status"] == "playing":
                            break
                    await asyncio.sleep(.05)
            waiting = await self.enqueue(client, self.auth[2], sources[2])
            assert waiting["status"] == "queued"
            self.resources("before controller SIGKILL")
            old_pid = self.process.pid
            self.process.kill()
            await asyncio.to_thread(self.process.wait, 10)
            # Inspect/reclaim only this test's orphaned containers; never stop Docker.
            orphaned = await asyncio.to_thread(self.containers)
            for item in orphaned:
                await asyncio.to_thread(sandbox.cleanup, item["id"])
            await self.start(client)
            assert self.process.pid != old_pid
            comp = (await self.request(client, "GET", f"/competitions/{cid}", self.auth[0])).json()
            assert comp["status"] == "paused", comp
            standings = (await self.request(client, "GET", f"/competitions/{cid}/standings", self.auth[0])).json()
            assert all(x["played"] == 0 and x["points"] == 0 for x in standings["items"]), standings
            old = (await self.request(client, "GET", f"/admin/competitions/{cid}/fixtures/{fixture}/attempts/1", admin)).json()
            assert old["status"] == "aborted" and old["error"]["code"] == "server_restart", old
            waiting = await self.wait_job(client, self.auth[2], waiting["id"])
            assert waiting["status"] == "aborted" and waiting["error"]["code"] == "server_restart", waiting
            fixtures = (await self.request(client, "GET", f"/competitions/{cid}/fixtures", self.auth[0])).json()["items"]
            assert fixtures[0]["status"] == "attention" and fixtures[0]["result"] is None, fixtures
            self.checked("controller_sigkill_restart_preserves_sessions_aborts_jobs_pauses_cup_and_awards_no_points",
                         orphan_containers_explicitly_reclaimed=len(orphaned),
                         reclaimed_container_states=dict(Counter(item["state"] for item in orphaned)))

            await self.request(client, "POST", f"/admin/competitions/{cid}/fixtures/{fixture}/retry", admin)
            await self.request(client, "POST", f"/admin/competitions/{cid}/start", admin)
            async with asyncio.timeout(120):
                while True:
                    comp = (await self.request(client, "GET", f"/competitions/{cid}", self.auth[0])).json()
                    if comp["status"] == "completed":
                        break
                    await asyncio.sleep(.3)
            attempts = (await self.request(client, "GET", f"/admin/competitions/{cid}/fixtures/{fixture}/attempts", admin)).json()["items"]
            assert len(attempts) == 2 and attempts[0]["status"] == "attention" and attempts[1]["status"] == "completed", attempts
            standings = (await self.request(client, "GET", f"/competitions/{cid}/standings", self.auth[0])).json()
            assert all(x["played"] == 2 and x["points"] == 3 and x["wins"] == 1 for x in standings["items"]), standings
            self.checked("explicit_admin_retry_and_resume_finishes_both_legs_without_double_scoring",
                         points=[x["points"] for x in standings["items"]])
            await self.load(client, "standings and replay after restart", 12, count=240,
                paths=[f"/competitions/{cid}/standings", f"/competitions/{cid}/fixtures/{fixture}/events", "/jobs"])

            # Real unavailable-image test, scoped to this controller's env.
            # Never stop the shared Docker daemon or remove the real image.
            before = (await self.request(client, "GET", "/jobs", self.auth[2])).json()["total"]
            await self.stop_idle()
            await self.start(client, missing_image=True)
            unavailable = await self.request(client, "POST", "/benchmarks", self.auth[2], expected=503,
                json={"submission_id": sources[2]["id"]})
            assert unavailable.json()["error"]["code"] == "worker_unavailable", unavailable.text
            after = (await self.request(client, "GET", "/jobs", self.auth[2])).json()["total"]
            assert before == after, (before, after)
            await self.stop_idle()
            await self.start(client)
            healthy = await self.enqueue(client, self.auth[2], sources[2])
            healthy = await self.wait_job(client, self.auth[2], healthy["id"])
            assert healthy["status"] == "completed" and healthy["report"]["passed"]
            self.checked("missing_worker_image_returns_readiness_and_enqueue_503_without_creating_job_then_recovers")

            login_codes = []
            for _ in range(11):
                response = await client.post(self.origin + "/api/v1/auth/login",
                    json={"username": "missing-load-account", "password": PASSWORD})
                login_codes.append(response.status_code)
            assert login_codes == [401] * 10 + [429], login_codes
            assert int(response.headers["Retry-After"]) > 0
            self.checked("login_abuse_is_rate_limited_with_retry_after", attempts=11)
            self.report["passed"] = True

    def cleanup(self):
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for item in self.containers():
            sandbox.cleanup(item["id"])
        remaining = self.containers()
        self.report["cleanup"] = {"controller_stopped": self.process is None or self.process.poll() is not None,
                                  "remaining_test_containers": len(remaining)}
        assert not remaining, remaining
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soak-seconds", type=int, default=15, choices=range(5, 61))
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    image = sandbox.image_id('uet-arena-worker:clock-v2')
    with tempfile.TemporaryDirectory(prefix="load-resilience-", dir=OUTPUT) as directory:
        harness = Harness(directory, image)
        try:
            asyncio.run(harness.execute(args.soak_seconds))
        except Exception as exc:
            harness.report["passed"] = False
            harness.report["failure"] = {"type": type(exc).__name__, "detail": str(exc)[:4000]}
            traceback.print_exc()
        finally:
            try:
                harness.cleanup()
            except Exception as exc:
                harness.report["passed"] = False
                harness.report["cleanup_failure"] = {"type": type(exc).__name__, "detail": str(exc)[:2000]}
                traceback.print_exc()
            finally:
                harness.report["completed_local"] = datetime.now(ZoneInfo("Asia/Bangkok")).isoformat()
                (OUTPUT / "load-resilience-latest.json").write_text(json.dumps(harness.report, indent=2), encoding="utf-8")
                from load_report import write_report
                write_report(harness.report)
        assert harness.report["passed"], harness.report.get("failure")
    print("ALL LOAD AND RESILIENCE CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
