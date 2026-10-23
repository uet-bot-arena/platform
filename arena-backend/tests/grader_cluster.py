"""Gói 3 end to end on one PC: a hub in WSL and three real graders.

    cd arena-backend && .venv/bin/python tests/grader_cluster.py      # run from WSL

* two Windows graders (Python for Windows, AppContainer sandbox) serve a competition whose
  grader_platform is windows; one bwrap grader in WSL may only take practice and checks;
* one Windows grader is killed hard (taskkill /F) in the middle of an official match: the
  match must start again on the other Windows grader within 60 s, and no fixture may score twice;
* the remaining Windows grader's token is revoked: its process must exit within 10 s;
* the admin grader list must show exactly who is online.

Needs: the Windows toolchain (python -m arena.sandbox.toolchain install, on Windows) and the
bwrap rootfs (python -m arena.sandbox.bwrap install ..., in WSL). Results:
test-results/m1/grader-cluster.json
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "cluster-test-only-password"
SLOW_BOT = ROOT / "sdk" / "bot.cpp"


def windows_path(path):
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def windows_temp():
    raw = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], capture_output=True, text=True, cwd="/mnt/c").stdout.strip()
    return Path(subprocess.run(["wslpath", "-u", raw], capture_output=True, text=True, check=True).stdout.strip())


def slow_bot():
    """The SDK starter, but 0.4 s per move in matches (not in the 72-sample check)."""
    source = SLOW_BOT.read_text(encoding="utf-8")
    source = source.replace("#include <string>\n", "#include <string>\n#include <chrono>\n#include <thread>\n", 1)
    source = source.replace("    std::string line, match;\n", "    std::string line, match;\n    bool slow = false;\n", 1)
    source = source.replace("                match = m.at(\"match_id\");\n",
                            "                match = m.at(\"match_id\");\n"
                            "                slow = m.at(\"payload\").value(\"match\", json::object()).value(\"kind\", \"\") == \"match\";\n", 1)
    source = source.replace("                const auto legal = m.at(\"payload\").at(\"legal_moves\");\n",
                            "                const auto legal = m.at(\"payload\").at(\"legal_moves\");\n"
                            "                if (slow) std::this_thread::sleep_for(std::chrono::milliseconds(400));\n", 1)
    assert source.count("sleep_for") == 1 and "bool slow" in source
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr("bot.cpp", source)
    return out.getvalue()


class Cluster:
    def __init__(self):
        self.results = {"started_unix": int(time.time()), "checks": []}
        self.data = Path(tempfile.mkdtemp(prefix="cluster-hub-"))
        self.shared = Path(tempfile.mkdtemp(prefix="uet-cluster-", dir=windows_temp()))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "ARENA_API_DATA": str(self.data), "ARENA_PORT": str(self.port), "ARENA_CONFIG": "none",
                    "ARENA_SECRETS": "none", "ARENA_API_ORIGIN": self.origin}
        self.hub = None
        self.graders = {}
        self.admin = httpx.Client(base_url=self.origin, timeout=60)

    def check(self, name, condition, **data):
        print(("PASS " if condition else "FAIL ") + name, json.dumps(data, ensure_ascii=False) if data else "", flush=True)
        self.results["checks"].append({"name": name, "passed": bool(condition), **data})
        if not condition:
            raise AssertionError(name)

    def api(self, client, method, path, expected=200, **kwargs):
        if method == "POST":
            kwargs.setdefault("headers", {}).setdefault("Idempotency-Key", uuid.uuid4().hex)
            if "json" not in kwargs and "content" not in kwargs:
                kwargs["json"] = {}
        response = client.request(method, "/api/v1" + path, **kwargs)
        assert response.status_code == expected, (method, path, response.status_code, response.text[:800])
        return response.json()

    def start_hub(self):
        before = time.time_ns() // 1_000_000
        windows = int(subprocess.check_output(["python.exe", "-c", "import time; print(time.time_ns() // 1000000)"], text=True))
        after = time.time_ns() // 1_000_000
        self.results["clocks"] = {"wsl_before_ms": before, "windows_ms": windows, "wsl_after_ms": after,
                                  "wsl_minus_windows_ms": (before + after) // 2 - windows}
        python = str(ROOT / ".venv" / "bin" / "python")
        subprocess.run([python, "-m", "arena_api", "bootstrap-admin", "--username", "organizer", "--password-stdin"],
                       input=PASSWORD + "\n", text=True, env=self.env, cwd=ROOT, check=True, capture_output=True)
        self.hub = subprocess.Popen([python, "-m", "arena_api", "serve"], env=self.env, cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=open(self.data / "hub.log", "w"))
        until = time.monotonic() + 30
        while time.monotonic() < until:
            try:
                if httpx.get(self.origin + "/api/v1/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        login = self.api(self.admin, "POST", "/auth/login", json={"username": "organizer", "password": PASSWORD})
        self.admin.headers["X-CSRF-Token"] = login["csrf_token"]

    def grader(self, name, backend, cpu):
        token = self.api(self.admin, "POST", "/admin/graders", expected=201, json={"name": name})
        token_file = self.shared / f"{name}.token"
        token_file.write_text(token["token"])
        pid_file = self.shared / f"{name}.pid"
        log = self.shared / f"{name}.log"
        if backend == "windows":
            argv = ["python.exe", "-m", "arena.grader", "--hub", self.origin, "--slots", "1", "--backend", "windows",
                    "--cpus", str(cpu), "--profile", name, "--token-file", windows_path(token_file),
                    "--pid-file", windows_path(pid_file), "--log-file", windows_path(log),
                    "--state-dir", windows_path(self.shared / ("state-" + name))]
            process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            argv = [str(ROOT / ".venv" / "bin" / "python"), "-m", "arena.grader", "--hub", self.origin, "--slots", "1",
                    "--backend", backend, "--cpus", str(cpu), "--profile", name, "--token-file", str(token_file),
                    "--pid-file", str(pid_file), "--log-file", str(log), "--state-dir", str(self.shared / ("state-" + name))]
            process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.graders[name] = {"id": token["id"], "process": process, "pid_file": pid_file, "backend": backend, "log": log}

    def listing(self):
        return {g["name"]: g for g in self.api(self.admin, "GET", "/admin/graders")["items"]}

    def wait_online(self, names, timeout=60):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            items = self.listing()
            if all(items.get(n, {}).get("online") for n in names):
                return items
            time.sleep(0.5)
        raise AssertionError("graders did not come online: " + json.dumps(self.listing(), ensure_ascii=False)[:2000])

    def windows_pid_alive(self, pid):
        out = subprocess.run(["tasklist.exe", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, cwd="/mnt/c").stdout
        return str(pid) in out

    def team(self, name, username):
        issued = self.api(self.admin, "POST", "/admin/teams", expected=201, json={"profile": {
            "team": name, "email": username + "@example.test", "phone": "", "confirmed": True,
            "members": [{"name": f"SV {i}", "student_id": f"2602{username[-1]}00{i}"} for i in range(3)]}, "username": username})
        client = httpx.Client(base_url=self.origin, timeout=60)
        login = self.api(client, "POST", "/auth/login", json={"username": username, "password": issued["temporary_password"]})
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        changed = self.api(client, "POST", "/auth/password", json={"current_password": issued["temporary_password"], "new_password": PASSWORD})
        client.headers["X-CSRF-Token"] = changed["csrf_token"]
        return client

    def wait_job(self, client, ident, timeout=300):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            job = self.api(client, "GET", "/jobs/" + ident)
            if job["status"] in ("completed", "failed", "aborted"):
                return job
            time.sleep(0.5)
        raise AssertionError("job did not finish: " + ident)

    def official_jobs(self):
        rows = self.api(self.admin, "GET", "/admin/jobs?kind=match&limit=100")["items"]
        return [j for j in rows if j.get("official")]

    def lease_row(self, job_id):
        with sqlite3.connect(f"file:{self.data / 'arena.sqlite'}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT id,status,reason,created_ms,expires_ms,ended_ms FROM grader_leases "
                             "WHERE job_id=? ORDER BY fencing DESC LIMIT 1", (job_id,)).fetchone()
        assert row is not None, job_id
        return dict(row)

    def kill_grader(self, name):
        pid = int(self.graders[name]["pid_file"].read_text())
        subprocess.run(["taskkill.exe", "/F", "/PID", str(pid)], capture_output=True, cwd="/mnt/c", check=True)
        killed = {"mono": time.monotonic(), "unix_ms": time.time_ns() // 1_000_000}
        self.check("grader_killed_hard", not self.windows_pid_alive(pid), grader=name, pid=pid)
        return killed

    def sample_lease(self, job_id, killed):
        row = self.lease_row(job_id)
        sample = {"seconds_after_kill": round(time.monotonic() - killed["mono"], 3), **row}
        self.results.setdefault("lease_samples", []).append(sample)
        print("LEASE", json.dumps(sample), flush=True)
        return row

    def check_expired_lease(self, row, killed):
        seconds = next(s["seconds_after_kill"] for s in self.results["lease_samples"]
                       if s["id"] == row["id"] and s["status"] != "active")
        self.check("killed_lease_expires_after_25_to_35s", row["status"] == "expired"
                   and row["reason"] == "lease_expired" and 25 <= seconds <= 35
                   and row["ended_ms"] >= row["expires_ms"], seconds=seconds, lease=row,
                   wall_seconds=((row["ended_ms"] or 0) - killed["unix_ms"]) / 1000)

    def lease_probe(self):
        """One real Windows grader, hard-killed during a match; sample the lease every second."""
        self.start_hub()
        self.grader("win-1", "windows", 11)
        self.wait_online(["win-1"])
        team = self.team("Lease probe", "lease-1")
        submission = self.api(team, "POST", "/submissions", expected=201, content=slow_bot(),
                              headers={"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0"})
        job = self.api(team, "POST", "/matches", expected=202,
                       json={"red_submission_id": submission["id"], "blue_submission_id": submission["id"],
                             "delegate_timeout_claims": True, "match_ms": 60000})
        until = time.monotonic() + 120
        while time.monotonic() < until:
            job = self.api(team, "GET", "/jobs/" + job["id"])
            if job["status"] == "playing":
                break
            assert job["status"] not in ("failed", "aborted", "completed"), job
            time.sleep(0.2)
        self.check("probe_match_playing", job["status"] == "playing")
        time.sleep(2)
        killed = self.kill_grader("win-1")
        until = killed["mono"] + 40
        while True:
            row = self.sample_lease(job["id"], killed)
            if row["status"] != "active" or time.monotonic() >= until:
                break
            time.sleep(1)
        self.check_expired_lease(row, killed)
        self.check("practice_requeued_without_a_result", self.api(team, "GET", "/jobs/" + job["id"])["status"] == "queued")

    def run(self):
        self.start_hub()
        self.grader("win-1", "windows", 11)
        self.grader("win-2", "windows", 10)
        self.grader("wsl-1", "bwrap", 9)
        items = self.wait_online(["win-1", "win-2", "wsl-1"])
        self.check("three_graders_online_with_platforms", {items[n]["platform"] for n in ("win-1", "win-2")} == {"windows"}
                   and items["wsl-1"]["platform"] == "linux", platforms={n: items[n]["platform"] for n in items})
        windows_runtime = items["win-1"]["runtime_id"]
        a, b = self.team("Cluster A", "cluster-a"), self.team("Cluster B", "cluster-b")
        comp = self.api(self.admin, "POST", "/admin/competitions", expected=201,
                        json={"title": "Cluster Windows cup", "max_teams": 2, "grader_platform": "windows"})
        self.check("competition_pins_windows_runtime", comp["config"]["runtime_id"] == windows_runtime and comp["config"]["grader_platform"] == "windows")
        self.api(self.admin, "POST", f"/admin/competitions/{comp['id']}/open")
        entries = []
        for client, version in ((a, "v1.0.0"), (b, "v1.0.0")):
            submission = self.api(client, "POST", "/submissions", expected=201, content=slow_bot(),
                                  headers={"Content-Type": "application/zip", "X-Submission-Version": version})
            bench = self.api(client, "POST", "/benchmarks", expected=202, json={"submission_id": submission["id"], "competition_id": comp["id"]})
            done = self.wait_job(client, bench["id"])
            self.check("competition_check_ran_on_windows", done["status"] == "completed" and done["runtime"]["platform"] == "windows",
                       grader=done.get("worker"), status=done["status"], error=done.get("error"))
            entries.append((client, submission["id"], bench["id"]))
        for client, submission_id, bench_id in entries:
            self.api(client, "POST", f"/competitions/{comp['id']}/entry", json={"submission_id": submission_id, "benchmark_id": bench_id,
                                                                                "delegate_timeout_claims": True})
        self.api(self.admin, "POST", f"/admin/competitions/{comp['id']}/lock")
        self.api(self.admin, "POST", f"/admin/competitions/{comp['id']}/start")
        # Practice from team A while the Windows graders are busy: only the bwrap grader is free.
        practice = self.api(a, "POST", "/benchmarks", expected=202, json={"submission_id": entries[0][1], "match_ms": 60000})
        victim, killed_at, victim_job, fixture = None, None, None, None
        until = time.monotonic() + 120
        while time.monotonic() < until and not victim:
            for job in self.official_jobs():
                if job["status"] == "playing" and job.get("worker") in ("win-1", "win-2"):
                    time.sleep(2)  # a few moves into the game
                    victim, victim_job, fixture = job["worker"], job["id"], job["official"]["fixture_id"]
                    break
            time.sleep(0.3)
        self.check("official_match_playing_on_a_windows_grader", victim is not None, grader=victim)
        killed = self.kill_grader(victim)
        killed_at = killed["mono"]
        survivor = "win-2" if victim == "win-1" else "win-1"
        restarted, restarted_after = None, None
        until = time.monotonic() + 120
        next_sample = 0
        while time.monotonic() < until and not restarted:
            if time.monotonic() >= next_sample:
                self.sample_lease(victim_job, killed)
                next_sample = time.monotonic() + 1
            for job in self.official_jobs():
                if job["official"]["fixture_id"] == fixture and job["id"] != victim_job and job["status"] in ("playing", "completed"):
                    restarted, restarted_after = job, time.monotonic() - killed_at
                    break
            time.sleep(0.25)
        self.check_expired_lease(self.lease_row(victim_job), killed)
        self.check("match_restarted_on_other_windows_grader_within_60s", restarted is not None and restarted_after < 60
                   and restarted.get("worker") == survivor, seconds=round(restarted_after or -1, 1), grader=(restarted or {}).get("worker"))
        until = time.monotonic() + 240
        while time.monotonic() < until:
            view = self.api(self.admin, "GET", f"/competitions/{comp['id']}")
            if view["status"] == "completed":
                break
            time.sleep(1)
        standings = self.api(self.admin, "GET", f"/competitions/{comp['id']}/standings")["items"]
        attempts = self.api(self.admin, "GET", f"/admin/competitions/{comp['id']}/fixtures/{fixture}/attempts")["items"]
        self.check("competition_completed_each_game_scored_once", view["status"] == "completed"
                   and sum(r["played"] for r in standings) == 4 and all(r["played"] == 2 for r in standings),
                   standings=[{k: r[k] for k in ("name", "played", "wins", "draws", "losses", "points")} for r in standings])
        self.check("lost_attempt_recorded_unscored", [x["error_code"] for x in attempts][:1] == ["worker_lost"] and attempts[-1]["status"] == "completed",
                   attempts=[{k: x[k] for k in ("number", "status", "error_code")} for x in attempts])
        jobs = self.official_jobs()
        self.check("bwrap_grader_never_ran_official_work", all(j.get("worker") != "wsl-1" for j in jobs))
        done = self.wait_job(a, practice["id"])
        self.check("practice_check_may_run_on_linux_grader", done["status"] == "completed", grader=done.get("worker"),
                   platform=done["runtime"]["platform"])
        items = self.listing()
        self.check("grader_list_shows_killed_grader_offline", not items[victim]["online"],
                   online={n: items[n]["online"] for n in items})
        survivor_pid = int(self.graders[survivor]["pid_file"].read_text())
        self.api(self.admin, "POST", f"/admin/graders/{self.graders[survivor]['id']}/revoke")
        revoked_at = time.monotonic()
        while self.windows_pid_alive(survivor_pid) and time.monotonic() - revoked_at < 30:
            time.sleep(0.25)
        gone = time.monotonic() - revoked_at
        self.check("revoked_grader_disconnects_within_10s", gone < 10, seconds=round(gone, 1))
        until = time.monotonic() + 30
        while time.monotonic() < until:
            items = self.listing()
            if not items[victim]["online"]:
                break
            time.sleep(1)
        self.check("final_grader_list", items["wsl-1"]["online"] and not items[victim]["online"] and items[survivor]["revoked_ms"],
                   online={n: items[n]["online"] for n in items}, jobs={n: items[n]["jobs_done"] for n in items})

    def stop(self):
        for name, item in self.graders.items():
            if item["backend"] == "windows" and item["pid_file"].is_file():
                subprocess.run(["taskkill.exe", "/F", "/PID", item["pid_file"].read_text().strip()], capture_output=True, cwd="/mnt/c")
            if item["process"].poll() is None:
                item["process"].kill()
        if self.hub and self.hub.poll() is None:
            self.hub.terminate()
            try:
                self.hub.wait(30)
            except subprocess.TimeoutExpired:
                self.hub.kill()
        for name, item in self.graders.items():
            if item["log"].is_file():
                self.results.setdefault("logs", {})[name] = item["log"].read_text(errors="replace")[-4000:]
        out = ROOT / "test-results" / "m1"
        out.mkdir(parents=True, exist_ok=True)
        filename = {"lease-probe": "grader-lease-probe.json", "installers": "grader-installers.json"}.get(
            self.results.get("mode"), "grader-cluster.json")
        (out / filename).write_text(json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.rmtree(self.shared, ignore_errors=True)
        shutil.rmtree(self.data, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lease-probe", action="store_true", help="Only probe expiry with one Windows grader")
    args = parser.parse_args()
    cluster = Cluster()
    cluster.results["mode"] = "lease-probe" if args.lease_probe else "cluster"
    try:
        cluster.lease_probe() if args.lease_probe else cluster.run()
        print("ALL GRADER CLUSTER CHECKS PASSED")
    finally:
        cluster.stop()


if __name__ == "__main__":
    sys.exit(main())
