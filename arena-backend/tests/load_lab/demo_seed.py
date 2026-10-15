"""Seed a running server with a ready-to-present demo, through the real API.

    .venv/bin/python tests/load_lab/demo_seed.py --origin http://127.0.0.1:41810 --admin organizer

Asks for the organiser password (or reads ARENA_DEMO_ADMIN_PASSWORD). Creates 7 teams
(C++, C and Python bots) with password demo-team-2026, an organiser sample bot, a practice
match against it, a finished Swiss event, a finished 10x10 event and a locked event that the
organiser starts live during the demo. Use it on a demo data directory, not on real data.
"""
import argparse
import getpass
import json
import os
import time

from lab_common import REPO, RESULTS, Api, ApiError, bot_source, zipped

TEAM_PW = "demo-team-2026"
TEAMS = [("alpha", "Đội Alpha", "cpp17", "ab50"), ("beta", "Đội Beta", "cpp17", "ab20"), ("gamma", "Đội Gamma", "python3", "starter"),
         ("delta", "Đội Delta", "c11", "starter"), ("epsilon", "Đội Epsilon", "cpp17", "onestep"), ("zeta", "Đội Zeta", "cpp17", "random"),
         ("eta", "Đội Eta", "python3", "starter")]
ENTRY = {"cpp17": "bot.cpp", "c11": "bot.c", "python3": "bot.py"}


def source(runtime, kind):
    return (REPO / "sdk" / ENTRY[runtime]).read_text() if kind == "starter" else bot_source(kind)


def upload(api, runtime, kind, version):
    return api.json("POST", "/submissions", expected=(201,), content=zipped(source(runtime, kind), salt=version, name=ENTRY[runtime]),
                    headers={"Content-Type": "application/zip", "X-Submission-Version": version, "X-Arena-Runtime": runtime})


def wait(api, job_id, label):
    while True:
        job = api.json("GET", "/jobs/" + job_id)
        if job["status"] in ("completed", "failed", "aborted"):
            print(f"  {label}: {job['status']} {(job.get('error') or {}).get('code') or ''}", flush=True)
            return job
        time.sleep(2)


def competition(admin, title, members, mode, **options):
    """mode: 'finish' runs it to the end, 'locked' stops after locking (start it live), 'open' only opens."""
    comp = admin.json("POST", "/admin/competitions", expected=(201,), json={"title": title, "max_teams": max(len(members), 2), "match_ms": 60000, **options})
    cid = comp["id"]
    admin.json("POST", f"/admin/competitions/{cid}/open")
    jobs = {t["slug"]: t["api"].json("POST", "/benchmarks", expected=(202,), json={"submission_id": t["sub"], "competition_id": cid})["id"] for t in members}
    for t in members:
        bench = wait(t["api"], jobs[t["slug"]], f"benchmark {t['slug']} · {title}")
        if bench["status"] == "completed":
            t["api"].json("POST", f"/competitions/{cid}/entry", json={"submission_id": t["sub"], "benchmark_id": bench["id"], "delegate_timeout_claims": True})
    if mode == "open":
        return cid
    admin.json("POST", f"/admin/competitions/{cid}/lock")
    if mode == "finish":
        admin.json("POST", f"/admin/competitions/{cid}/start")
        while admin.json("GET", "/competitions/" + cid)["status"] != "completed":
            time.sleep(3)
        rows = admin.json("GET", f"/competitions/{cid}/standings")["items"]
        print(f"  {title}: " + ", ".join(f"#{r['rank']} {r['name']} {r['points']}đ" for r in rows), flush=True)
    return cid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="http://127.0.0.1:41810")
    ap.add_argument("--admin", default="organizer")
    args = ap.parse_args()
    password = os.environ.get("ARENA_DEMO_ADMIN_PASSWORD") or getpass.getpass("Organiser password: ")
    admin = Api(args.origin)
    admin.login(args.admin, password)
    teams = []
    for slug, name, runtime, kind in TEAMS:
        try:
            cred = admin.json("POST", "/admin/teams", expected=(201,), json={"username": "doi-" + slug, "profile": {
                "team": name, "email": f"{slug}@demo.local", "confirmed": True,
                "members": [{"name": f"Sinh viên {slug} {m}", "student_id": f"SV{slug[:3].upper()}{m}001"} for m in range(1, 4)]}})
        except ApiError as exc:
            raise SystemExit(f"Demo data already exists on this server? {exc}")
        api = Api(args.origin)
        api.login("doi-" + slug, cred["temporary_password"])
        data = api.json("POST", "/auth/password", json={"current_password": cred["temporary_password"], "new_password": TEAM_PW})
        api.http.headers["X-CSRF-Token"] = data["csrf_token"]
        teams.append({"slug": slug, "api": api, "sub": upload(api, runtime, kind, "v1.0.0")["id"]})
        print(f"{name}: {runtime}/{kind}", flush=True)
    zeta = teams[5]
    broken = upload(zeta["api"], "cpp17", "compile_error", "v0.9.0")
    wait(zeta["api"], zeta["api"].json("POST", "/benchmarks", expected=(202,), json={"submission_id": broken["id"], "match_ms": 60000})["id"], "zeta compile error")
    sample = admin.json("POST", "/admin/sample-bots", expected=(201,), content=zipped(bot_source("ab20"), salt="sample"),
                        headers={"Content-Type": "application/zip", "X-Bot-Name": "Alpha-beta 20ms", "X-Bot-Level": "hard", "X-Arena-Runtime": "cpp17"})
    alpha = teams[0]
    practice = alpha["api"].json("POST", "/matches", expected=(202,), json={"red_submission_id": alpha["sub"], "sample_bot_id": sample["id"],
                                                                          "sample_side": "red", "match_ms": 60000, "delegate_timeout_claims": True})
    wait(alpha["api"], practice["id"], "practice alpha (blue) vs sample bot (red)")
    ids = {"practice_job": practice["id"],
           "swiss": competition(admin, "Giải Swiss demo (7 đội)", teams, "finish", format="swiss"),
           "custom": competition(admin, "Giải bàn 10×10 · diệt 2 loại", teams[:4], "finish", basic_rules={"rows": 10, "cols": 10, "eliminate_types": 2}),
           "live": competition(admin, "Giải demo trực tiếp", teams[:4], "locked")}
    (RESULTS / "demo-ids.json").write_text(json.dumps(ids, indent=2))
    print("Demo ready:", ids, flush=True)


if __name__ == "__main__":
    main()
