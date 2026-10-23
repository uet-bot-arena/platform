"""SIGKILL a real hub with 50 unfinished official games; Windows and bwrap must finish them.

Run in WSL with both native toolchains installed. Two slots per platform, four CPUs total;
50 durable fixtures, rather than 50 oversubscribed bot CPUs. No real accounts/data are used.
"""
import io
import json
import sqlite3
import time
import zipfile

import httpx

from grader_cluster import Cluster, slow_bot


def source():
    with zipfile.ZipFile(io.BytesIO(slow_bot())) as archive:
        text = archive.read("bot.cpp").decode().replace("milliseconds(400)", "milliseconds(100)")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("bot.cpp", text)
    return data.getvalue()


class Recovery(Cluster):
    def sql(self, statement, values=()):
        with sqlite3.connect(f"file:{self.data / 'arena.sqlite'}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(statement, values)]

    def run(self):
        self.start_hub()
        self.grader("restart-windows", "windows", "11,10", slots=2)
        self.grader("restart-linux", "bwrap", "9,8", slots=2)
        self.wait_online(list(self.graders))
        clients = [self.team("Restart A", "restart-a"), self.team("Restart B", "restart-b")]
        submissions = [self.api(client, "POST", "/submissions", expected=201, content=source(),
                                headers={"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0"})
                       for client in clients]
        cups, checks = [], {}
        try:
            # Prepare all fixtures before starting any clock. Reuse only real passed benchmarks.
            for number in range(25):
                platform = "windows" if number % 2 == 0 else "linux"
                cup = self.api(self.admin, "POST", "/admin/competitions", expected=201,
                               json={"title": f"Restart {platform} {number + 1}", "max_teams": 2,
                                     "grader_platform": platform, "match_ms": 60000})
                cid = cup["id"]
                self.api(self.admin, "POST", f"/admin/competitions/{cid}/open")
                if platform not in checks:
                    checks[platform] = []
                    for client, submission in zip(clients, submissions):
                        job = self.api(client, "POST", "/benchmarks", expected=202,
                                       json={"submission_id": submission["id"], "competition_id": cid})
                        done = self.wait_job(client, job["id"])
                        assert done["status"] == "completed" and done["report"]["passed"], done.get("error")
                        checks[platform].append(job["id"])
                for client, submission, benchmark in zip(clients, submissions, checks[platform]):
                    self.api(client, "POST", f"/competitions/{cid}/entry",
                             json={"submission_id": submission["id"], "benchmark_id": benchmark,
                                   "delegate_timeout_claims": True})
                self.api(self.admin, "POST", f"/admin/competitions/{cid}/lock")
                cups.append((cid, platform))
            self.check("fifty_real_qualified_fixtures", len(self.sql("SELECT id FROM competition_fixtures")) == 50)
            for cid, _ in cups:
                self.api(self.admin, "POST", f"/admin/competitions/{cid}/start")
            until = time.monotonic() + 60
            while time.monotonic() < until:
                playing = self.sql("SELECT json_extract(data,'$.runtime.platform') AS platform FROM documents "
                                   "WHERE kind='job' AND json_extract(data,'$.status')='playing'")
                if {row["platform"] for row in playing} == {"windows", "linux"}:
                    break
                time.sleep(.1)
            self.check("both_native_platforms_playing_before_kill", {r["platform"] for r in playing} == {"windows", "linux"})
            old = self.sql("SELECT id,job_id,grader_id,fencing FROM grader_leases WHERE status='active'")
            unfinished = self.sql("SELECT id FROM competition_fixtures WHERE status<>'completed'")
            self.check("all_fifty_unfinished_before_kill", len(unfinished) == 50, unfinished=len(unfinished), active_leases=len(old))
            self.hub.kill()
            self.hub.wait(15)
            killed_at = time.monotonic()
            self.start_hub(bootstrap=False)
            self.check("hub_restarted_without_admin_recovery", True, seconds=round(time.monotonic() - killed_at, 2))
            invalidated = self.sql("SELECT status,reason FROM grader_leases WHERE id IN (" + ",".join("?" for _ in old) + ")",
                                   [r["id"] for r in old])
            self.check("all_old_leases_invalidated_on_startup", len(invalidated) == len(old)
                       and all(r == {"status": "expired", "reason": "server_restart"} for r in invalidated))
            # Exercise the actual HTTP fence using a grader's disposable token, without logging it.
            by_id = {item["id"]: name for name, item in self.graders.items()}
            for lease in old:
                token = (self.shared / (by_id[lease["grader_id"]] + ".token")).read_text()
                response = httpx.post(self.origin + f"/api/v1/grader/leases/{lease['id']}/heartbeat",
                                      headers={"Authorization": "Bearer " + token}, json={"fencing": lease["fencing"]})
                assert response.status_code == 409 and response.json()["error"]["code"] == "stale_lease", response.status_code
            self.check("stale_graders_rejected_over_http", True, leases=len(old))
            until, previous = time.monotonic() + 600, None
            while time.monotonic() < until:
                counts = self.sql("SELECT status,COUNT(*) AS n FROM competition_fixtures GROUP BY status")
                if counts != previous:
                    print("FIXTURES", json.dumps(counts), flush=True)
                    previous = counts
                assert not any(r["status"] == "attention" for r in counts), counts
                if counts == [{"status": "completed", "n": 50}]:
                    break
                time.sleep(.5)
            self.check("fifty_fixtures_complete_without_manual_retry", counts == [{"status": "completed", "n": 50}], counts=counts)
            totals = self.sql("SELECT fixture_id,COUNT(*) AS n FROM competition_attempts WHERE status='completed' GROUP BY fixture_id")
            interrupted = self.sql("SELECT error_code FROM competition_attempts WHERE error_code='server_restart'")
            self.check("one_scored_attempt_per_fixture", len(totals) == 50 and all(r["n"] == 1 for r in totals)
                       and len(interrupted) == len(old), interrupted_attempts=len(interrupted))
            for cid, platform in cups:
                standings = self.api(self.admin, "GET", f"/competitions/{cid}/standings")
                assert not standings["provisional"] and sum(r["played"] for r in standings["items"]) == 4, cid
                self.api(self.admin, "GET", f"/competitions/{cid}/standings")
            scored = self.sql("SELECT COUNT(*) AS n FROM competition_attempts WHERE status='completed'")[0]["n"]
            self.check("standings_reads_never_score_twice", scored == 50)
            self.results["cups"] = [{"id": cid, "platform": platform} for cid, platform in cups]
            self.results["seconds_after_restart"] = round(time.monotonic() - killed_at, 2)
        finally:
            for client in clients:
                client.close()


def main():
    cluster = Recovery()
    cluster.results["mode"] = "hub-restart"
    try:
        cluster.run()
        print("ALL HUB RESTART CHECKS PASSED", flush=True)
    finally:
        cluster.stop()


if __name__ == "__main__":
    main()
