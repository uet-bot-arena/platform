"""E2E qua API thật: nhiều đội cùng nộp bot -> benchmark -> giải vòng tròn.

Đo: giới hạn đăng nhập theo IP, 429 (hàng đợi đầy), 503 (Uvicorn admission), thời gian
chờ/biên dịch/benchmark, độ trễ API khi tải, phát hiện bot lỗi, thời gian chạy giải.

    .venv/bin/python tests/load_lab/e2e_submissions.py --teams 100 --tournament 8
"""
import argparse
import concurrent.futures as cf
import random
import statistics
import threading
import time

from lab_common import ALGORITHMS, FAULTS, HOME_LAB, Api, ApiError, bot_source, save, start_server, stop_server, team_ip, zipped, ADMIN_PW

TERMINAL = ("completed", "failed", "aborted")
FAULT_LIST = ["compile_error"] + list(FAULTS)


def roster(n):
    """Ưu tiên đủ 6 thuật toán, rồi đủ các bot lỗi, phần dư xoay vòng thuật toán; xếp xen kẽ."""
    n_fault = min(len(FAULT_LIST), max(0, n - len(ALGORITHMS)))
    algos = [ALGORITHMS[i % len(ALGORITHMS)] for i in range(n - n_fault)]
    faults = FAULT_LIST[:n_fault]
    out = []
    while algos or faults:
        if algos:
            out.append(algos.pop(0))
        if faults:
            out.append(faults.pop(0))
    return out


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))] if values else None


def probe_loop(origin, stop, samples):
    """Khách xem (không đăng nhập) gọi API công khai mỗi 0,5 s trong suốt pha tải."""
    guest = Api(origin, "127.8.8.8", timeout=60)
    while not stop.is_set():
        for path in ("/competitions", "/health/live"):
            a = time.monotonic()
            try:
                status = guest.call("GET", path, expected=(200, 503), retry_503=False).status_code
            except Exception:
                status = -1
            samples.append({"path": path, "ms": (time.monotonic() - a) * 1000, "status": status})
        stop.wait(0.5)


def team_flow(t, origin, match_ms, t0, apis, retry_s=3.0, poll_s=2.0):
    api = Api(origin, t["ip"])
    apis[t["username"]] = api
    tl = t["timeline"] = {}
    mark = lambda k: tl.__setitem__(k, round(time.monotonic() - t0, 2))
    try:
        api.login(t["username"], t["temp"])
        data = api.json("POST", "/auth/password", json={"current_password": t["temp"], "new_password": "lab-pw-" + t["username"]})
        api.http.headers["X-CSRF-Token"] = data["csrf_token"]
        mark("logged_in")
        raw = zipped(bot_source(t["bot"]), salt=t["username"])
        a = time.monotonic()
        sub = api.json("POST", "/submissions", expected=(201,), content=raw,
                       headers={"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0"})
        t["upload_ms"] = round((time.monotonic() - a) * 1000)
        t["submission_id"] = sub["id"]
        mark("uploaded")
        retries = 0
        while True:
            try:
                a = time.monotonic()
                job = api.json("POST", "/benchmarks", expected=(202,), json={"submission_id": sub["id"], "match_ms": match_ms})
                t["enqueue_ms"] = round((time.monotonic() - a) * 1000)
                break
            except ApiError as e:
                if e.status != 429:
                    raise
                retries += 1
                time.sleep(retry_s * random.uniform(0.7, 1.3))
        t["queue_full_retries"] = retries
        mark("enqueued")
        while True:
            j = api.json("GET", "/jobs/" + job["id"])
            if j["status"] in TERMINAL:
                break
            time.sleep(poll_s)
        mark("finished")
        rep = j.get("report") or {}
        t.update(benchmark_id=job["id"], status=j["status"], passed=rep.get("passed") is True,
                 error=(j.get("error") or {}).get("code"), error_stage=(j.get("error") or {}).get("stage"),
                 error_detail=((j.get("error") or {}).get("detail") or "")[:160],
                 turn_wall_ms=rep.get("turn_wall_ms"))
        stderr = " | ".join((m or {}).get("stderr", "") for m in rep.get("sandbox_metrics", []))
        if t["bot"] == "sneaky":
            t["sneaky_stderr"] = stderr.strip()[:900]
        ev = api.json("GET", f"/jobs/{job['id']}/events")["items"]
        at = {}
        for e in ev:
            key = e["data"]["phase"] if e["type"] == "phase" else e["type"]
            at.setdefault(key, e["server_unix_ms"])
        end = at.get("completed") or at.get("failed") or at.get("aborted")
        seg = lambda a, b: round((at[b] - at[a]) / 1000, 2) if a in at and b in at else None
        at["end"] = end
        t["phase_s"] = {"queue_wait": seg("queued", "building"), "build": seg("building", "validating"),
                        "startup": seg("validating", "benchmarking"), "samples": seg("benchmarking", "end")}
    except Exception as exc:
        t["exception"] = str(exc)[:400]
    t["api_stats"] = dict(api.stats)


def tournament(origin, admin, teams, apis, k, match_ms, fmt="double_round_robin"):
    passed = [t for t in teams if t.get("passed")]
    picked = []
    for alg in ALGORITHMS + ["sneaky"]:
        for t in passed:
            if t["bot"] == alg and t not in picked:
                picked.append(t)
                break
    for t in passed:
        if len(picked) >= k:
            break
        if t not in picked:
            picked.append(t)
    picked = picked[:k]
    comp = admin.json("POST", "/admin/competitions", expected=(201,), json={"title": "Lab cup", "max_teams": len(picked), "match_ms": match_ms, "format": fmt})
    cid = comp["id"]
    admin.json("POST", f"/admin/competitions/{cid}/open")
    for t in picked:
        apis[t["username"]].json("POST", f"/competitions/{cid}/entry", json={
            "submission_id": t["submission_id"], "benchmark_id": t["benchmark_id"], "delegate_timeout_claims": True})
    locked = admin.json("POST", f"/admin/competitions/{cid}/lock")
    admin.json("POST", f"/admin/competitions/{cid}/start")
    start, last_print, last_change, last_counts = time.monotonic(), 0, time.monotonic(), None
    print(f"[giải] {fmt}, {len(picked)} đội, vòng/lịch đầu {locked['fixture_count']} trận", flush=True)
    while True:
        view = admin.json("GET", "/competitions/" + cid)
        c = view["fixture_counts"]
        if c != last_counts:
            last_counts, last_change = dict(c), time.monotonic()
        if time.monotonic() - last_print > 60:
            print(f"[giải] {round(time.monotonic() - start)}s: vòng {view['config'].get('current_round', '-')}, {c}", flush=True)
            last_print = time.monotonic()
        if view["status"] == "completed":
            break
        # Swiss has short gaps between rounds; stop only when nothing moved for 3 minutes.
        if time.monotonic() - last_change > 180:
            print("[giải] không tiến triển trong 3 phút, dừng theo dõi:", view["status"], c, flush=True)
            break
        time.sleep(3)
    total = time.monotonic() - start
    fixtures, offset = [], 0
    while True:
        page = admin.json("GET", f"/competitions/{cid}/fixtures?limit=100&offset={offset}")
        fixtures += page["items"]
        offset += 100
        if offset >= page["total"]:
            break
    kind_of = {t["team_id"]: t["bot"] for t in teams}
    rows = []
    for f in fixtures:
        items, after = [], 0
        while True:
            page = admin.json("GET", f"/competitions/{cid}/fixtures/{f['id']}/events?after={after}")
            if not page["items"]:
                break
            items += page["items"]
            if page["next_after"] == after:
                break
            after = page["next_after"]
        states = [e for e in items if e["type"] == "state"]
        rows.append({"red": kind_of[f["red_team_id"]], "blue": kind_of[f["blue_team_id"]], "status": f["status"],
                     "result": f["result"], "plies": states[-1]["data"]["state"]["ply"] if states else None,
                     "seconds": round((items[-1]["server_unix_ms"] - items[0]["server_unix_ms"]) / 1000, 2) if items else None})
    standings = admin.json("GET", f"/competitions/{cid}/standings")["items"]
    table = [{"rank": s["rank"], "bot": kind_of[s["team_id"]], "points": s["points"], "w": s["wins"], "d": s["draws"], "l": s["losses"],
              "bye": s.get("bye_points", 0), "buchholz": s.get("buchholz")} for s in standings]
    by_bot = {}
    for row in table:
        by_bot.setdefault(row["bot"], []).append(row["rank"])
    secs = [r["seconds"] for r in rows if r["seconds"] is not None]
    return {"format": fmt, "status": view["status"], "rounds": view["config"].get("rounds"), "teams": len(picked), "fixtures": len(rows), "wall_s": round(total, 1),
            "mean_rank_by_bot": {bot: round(statistics.mean(ranks), 1) for bot, ranks in sorted(by_bot.items(), key=lambda x: statistics.mean(x[1]))},
            "fixture_s": {"median": statistics.median(secs) if secs else None, "p95": pct(secs, 95), "max": max(secs) if secs else None},
            "status_counts": {s: sum(r["status"] == s for r in rows) for s in {r["status"] for r in rows}},
            "reasons": {r: sum((x["result"] or {}).get("reason") == r for x in rows) for r in {(x["result"] or {}).get("reason") for x in rows}},
            "standings": table, "fixtures_detail": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams", type=int, default=36)
    ap.add_argument("--tournament", type=int, default=8, help="số đội vào giải (vòng tròn <=32, Swiss <=4096); 0 = bỏ qua")
    ap.add_argument("--format", default="double_round_robin", choices=("double_round_robin", "swiss"))
    ap.add_argument("--match-ms", type=int, default=60000, choices=(60000, 300000))
    ap.add_argument("--retry-s", type=float, default=20.0, help="chờ sau mỗi 429 (hàng đợi đầy)")
    ap.add_argument("--poll-s", type=float, default=5.0, help="chu kỳ hỏi trạng thái job")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    origin, server, thread = start_server(HOME_LAB / f"data-e2e-{stamp}")
    report = {"args": vars(args), "origin": origin}
    try:
        admin = Api(origin, "127.0.0.1")
        admin.login("organizer", ADMIN_PW)
        bots = roster(args.teams)
        teams = []
        for i, bot in enumerate(bots):
            cred = admin.json("POST", "/admin/teams", expected=(201,), json={"username": f"team{i:04d}", "profile": {
                "team": f"Lab team {i:04d}", "email": f"team{i}@lab.test", "confirmed": True,
                "members": [{"name": f"Student {i}-{m}", "student_id": f"LAB{i:04d}{m}"} for m in range(3)]}})
            teams.append({"username": f"team{i:04d}", "temp": cred["temporary_password"], "team_id": cred["account"]["team_id"],
                          "bot": bot, "ip": team_ip(i)})
        print(f"[0] BTC tạo {len(teams)} đội: " + ", ".join(f"{b}x{bots.count(b)}" for b in dict.fromkeys(bots)), flush=True)

        # 1) Giới hạn đăng nhập theo IP: nhiều sinh viên chung 1 IP (NAT/phòng máy, hoặc sau reverse proxy).
        nat = Api(origin, "127.9.9.9")
        demo = []
        for t in teams[:min(len(teams), 34)]:
            try:
                nat.call("POST", "/auth/login", json={"username": t["username"], "password": t["temp"]})
                demo.append(200)
            except ApiError as e:
                demo.append(e.status)
        first_429 = demo.index(429) + 1 if 429 in demo else None
        report["shared_ip_login"] = {"attempts": len(demo), "ok": demo.count(200), "rate_limited": demo.count(429), "first_429_at": first_429}
        print(f"[1] {len(demo)} lần đăng nhập từ cùng 1 IP: {demo.count(200)} OK, {demo.count(429)} bị 429 (bắt đầu ở lần {first_429})", flush=True)

        # 2) Tất cả đội cùng nộp + benchmark.
        samples, stop = [], threading.Event()
        prober = threading.Thread(target=probe_loop, args=(origin, stop, samples), daemon=True)
        prober.start()
        apis, t0 = {}, time.monotonic()
        with cf.ThreadPoolExecutor(max_workers=len(teams)) as pool:
            futures = [pool.submit(team_flow, t, origin, args.match_ms, t0, apis, args.retry_s, args.poll_s) for t in teams]
            done_count = 0
            for _ in cf.as_completed(futures):
                done_count += 1
                if done_count % 6 == 0 or done_count == len(teams):
                    print(f"[2] {done_count}/{len(teams)} đội đã có kết quả benchmark sau {round(time.monotonic() - t0)}s", flush=True)
        phase_wall = time.monotonic() - t0
        stop.set()
        prober.join()
        ok = [t for t in teams if t.get("passed")]
        builds = [t["phase_s"]["build"] for t in teams if t.get("phase_s", {}).get("build") is not None]
        samples_s = [t["phase_s"]["samples"] for t in ok if t["phase_s"]["samples"] is not None]
        lat = lambda p: [s["ms"] for s in samples if s["path"] == p and s["status"] == 200]
        report["submission_phase"] = {
            "wall_s": round(phase_wall, 1), "teams": len(teams), "passed": len(ok),
            "throughput_benchmarks_per_hour": round(len(teams) / phase_wall * 3600, 1),
            "build_s": {"median": statistics.median(builds) if builds else None, "max": max(builds) if builds else None},
            "benchmark_samples_s": {"median": statistics.median(samples_s) if samples_s else None, "max": max(samples_s) if samples_s else None},
            "max_queue_wait_s": max((t["phase_s"]["queue_wait"] or 0) for t in teams if t.get("phase_s")),
            "queue_full_429_retries": sum(t.get("queue_full_retries", 0) for t in teams),
            "admission_503": sum(t["api_stats"]["503"] for t in teams),
            "upload_ms": {"median": statistics.median([t["upload_ms"] for t in teams if "upload_ms" in t]), "max": max(t.get("upload_ms", 0) for t in teams)},
            "enqueue_ms": {"median": statistics.median([t["enqueue_ms"] for t in teams if "enqueue_ms" in t]), "max": max(t.get("enqueue_ms", 0) for t in teams)},
            "guest_GET_competitions_ms": {"median": round(statistics.median(lat("/competitions")), 1), "p95": round(pct(lat("/competitions"), 95), 1), "max": round(max(lat("/competitions")), 1)},
            "guest_GET_health_live_ms": {"median": round(statistics.median(lat("/health/live")), 1), "max": round(max(lat("/health/live")), 1)},
            "guest_503": sum(s["status"] == 503 for s in samples),
            "exceptions": [{"team": t["username"], "bot": t["bot"], "exc": t["exception"]} for t in teams if "exception" in t],
        }
        report["fault_detection"] = [{"bot": t["bot"], "status": t.get("status"), "error": t.get("error"), "stage": t.get("error_stage"),
                                      "detail": t.get("error_detail")} for t in teams if t["bot"] not in ALGORITHMS]
        report["sneaky_stderr"] = next((t.get("sneaky_stderr") for t in teams if t["bot"] == "sneaky"), None)
        report["algorithm_bots_failed"] = [{"bot": t["bot"], "error": t.get("error"), "detail": t.get("error_detail")}
                                           for t in teams if t["bot"] in ALGORITHMS and not t.get("passed")]
        sp = report["submission_phase"]
        print(f"[2] xong {len(teams)} bài trong {sp['wall_s']}s ({sp['throughput_benchmarks_per_hour']} bài/giờ); đạt {len(ok)}; "
              f"build median {sp['build_s']['median']}s; 429 retry {sp['queue_full_429_retries']}; 503 {sp['admission_503']}", flush=True)
        for f in report["fault_detection"]:
            print(f"    lỗi cố ý {f['bot']:<13} -> {f['status']}/{f['error']} ({f['stage']})", flush=True)

        # 3) Giải đấu.
        if args.tournament:
            limit = 32 if args.format == "double_round_robin" else 4096
            report["tournament"] = tournament(origin, admin, teams, apis, min(args.tournament, limit), args.match_ms, args.format)
            tr = report["tournament"]
            print(f"[3] {tr['format']} {tr['status']}: {tr['teams']} đội / {tr['fixtures']} trận trong {tr['wall_s']}s; "
                  f"trận median {tr['fixture_s']['median']}s, max {tr['fixture_s']['max']}s; kết thúc {tr['reasons']}", flush=True)
            for s in tr["standings"][:10]:
                print(f"    #{s['rank']:<3} {s['bot']:<8} {s['points']:>3} điểm  ({s['w']}T {s['d']}H {s['l']}B"
                      f"{', nghỉ +' + str(s['bye']) if s['bye'] else ''}{', Buchholz ' + str(s['buchholz']) if s['buchholz'] is not None else ''})", flush=True)
            print("    hạng trung bình theo thuật toán:", tr["mean_rank_by_bot"], flush=True)
        report["teams"] = [{k: v for k, v in t.items() if k not in ("temp",)} for t in teams]
    finally:
        path = save(f"e2e-{stamp}.json", report)
        print("saved", path, flush=True)
        stop_server(server, thread)


if __name__ == "__main__":
    main()
