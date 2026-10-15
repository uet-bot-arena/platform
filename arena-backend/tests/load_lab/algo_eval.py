"""Rank bot algorithms by real matches and measure parallel match throughput.

    .venv/bin/python tests/load_lab/algo_eval.py league --openings 6 --workers 8
    .venv/bin/python tests/load_lab/algo_eval.py scaling --workers 1,2,4,8,10

Every match runs in the platform's own container/seccomp/clock (sandbox.Worker) with the
v2 adjudication used by service.run_match. Results go to test-results/load-lab/results/.
"""
import argparse
import concurrent.futures as cf
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import statistics
import time
import uuid

from lab_common import ALGORITHMS, HOME_LAB, bot_source, save
from arena import engine, sandbox
from arena.adjudication import position_key
from arena.config import match_profile
from arena.protocol import Fault

PROFILE = match_profile(60_000)            # Bullet: quỹ 60 s mỗi bot
IMAGE_TAG = "uet-arena-worker:clock-v2"
ART = HOME_LAB / "artifacts"
BOT_FAULTS = {"timeout", "crash", "protocol_error", "stale_action", "output_limit"}


# ---------- build ----------
def artifact(kind, image):
    """Biên dịch một lần bằng sandbox.build của repo, cache theo loại bot + image."""
    folder = ART / f"{kind}-{image[7:19]}"
    if (folder / "bot").is_file():
        return folder
    src = HOME_LAB / "src" / kind
    src.mkdir(parents=True, exist_ok=True)
    (src / "bot.cpp").write_text(bot_source(kind))
    ART.mkdir(exist_ok=True)
    stage = ART / f"tmp-{uuid.uuid4().hex}"
    report = sandbox.build(src, stage, PROFILE, image)
    if not report["ok"]:
        raise RuntimeError(f"build {kind}: {report['code']} {report['log'][:400]}")
    stage.rename(folder)
    return folder


# ---------- đấu thật ----------
def openings(n, seed=20261015, plies=6):
    rng, out = random.Random(seed), []
    while len(out) < n:
        s = engine.initial_state()
        for _ in range(plies):
            s = engine.apply(s, rng.choice(engine.legal_moves(s)))
        if not s["result"] and s["to_move"] == "red":
            out.append(s)
    return out


def play(task):
    """Một trận theo đúng quy tắc của service.run_match (thể lệ v2): lỗi bot = thua, lặp 3 lần / 512 ply = hòa."""
    red, blue, opening, image = task["red"], task["blue"], task["opening"], task["image"]
    t0 = time.monotonic()
    mid = "lab-" + uuid.uuid4().hex
    workers, state, result, fault = [], deepcopy(opening), None, None
    clocks = {s: PROFILE.match_ms for s in engine.SIDES}
    try:
        for side, kind in (("red", red), ("blue", blue)):
            w = sandbox.Worker(Path(task["arts"][kind]), PROFILE, image)
            workers.append(w)
            try:
                w.start(mid, side)
            except Fault as exc:
                if exc.code not in BOT_FAULTS:
                    raise
                result, fault = {"winner": engine.other(side), "reason": "startup_failure"}, f"{kind}:{exc.code}"
                break
        seen, turn = {position_key(state): 1}, 0
        while not result and state["ply"] < PROFILE.max_plies:
            turn += 1
            side = state["to_move"]
            w = workers[0 if side == "red" else 1]
            payload = engine.observation(state)
            payload["clocks"] = dict(clocks)
            msg = {"protocol_version": 2, "type": "observation", "match_id": mid, "turn_id": str(turn),
                   "state_version": state["ply"], "payload": payload}
            try:
                res = w.turn(msg)
                clocks[side] = res["remaining_ms"]
                state = engine.apply(state, res["message"]["payload"])
            except (Fault, ValueError) as exc:
                code = exc.code if isinstance(exc, Fault) else "illegal_action"
                if code not in BOT_FAULTS | {"illegal_action"}:
                    raise
                result, fault = {"winner": engine.other(side), "reason": code}, f"{task[side]}:{code}"
                break
            if state["result"]:
                result = state["result"]
                break
            key = position_key(state)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= 3:
                result = {"winner": None, "reason": "threefold_repetition"}
        result = result or {"winner": None, "reason": "ply_limit"}
    finally:
        for w in workers:
            w.close()
    return {"red": red, "blue": blue, "result": result, "plies": state["ply"], "fault": fault,
            "seconds": round(time.monotonic() - t0, 2), "clocks_left_ms": {k: round(v) for k, v in clocks.items()}}


def _pin_worker(counter):
    """Pin each pool process to its own CPU, like the platform's N workers (one match per CPU)."""
    cpus = sorted(os.sched_getaffinity(0))
    cpus = cpus[2:] or cpus
    with counter.get_lock():
        index = counter.value
        counter.value += 1
    sandbox.controller_scope(HOME_LAB, cpus[index % len(cpus)])


def run_batch(tasks, workers):
    import multiprocessing
    t0 = time.monotonic()
    cpu0 = cpu_times()
    counter = multiprocessing.Value("i", 0)
    with cf.ProcessPoolExecutor(max_workers=workers, initializer=_pin_worker, initargs=(counter,)) as pool:
        rows = list(pool.map(play, tasks))
    wall = time.monotonic() - t0
    return rows, wall, cpu_util(cpu0, cpu_times())


def cpu_times():
    v = list(map(int, Path("/proc/stat").read_text().split("\n")[0].split()[1:]))
    return sum(v), v[3] + v[4]


def cpu_util(a, b):
    total, idle = b[0] - a[0], b[1] - a[1]
    return round(100 * (1 - idle / total), 1) if total else None


def league_tasks(kinds, n_openings, arts, image):
    tasks = []
    for o in openings(n_openings):
        for i, a in enumerate(kinds):
            for b in kinds[i + 1:]:
                for red, blue in ((a, b), (b, a)):
                    tasks.append({"red": red, "blue": blue, "opening": o, "arts": arts, "image": image})
    return tasks


def standings(kinds, rows):
    table = {k: {"bot": k, "pts": 0, "w": 0, "d": 0, "l": 0, "faults": 0} for k in kinds}
    for r in rows:
        win = r["result"]["winner"]
        for side in engine.SIDES:
            t = table[r[side]]
            if win is None:
                t["d"] += 1; t["pts"] += 1
            elif win == side:
                t["w"] += 1; t["pts"] += 3
            else:
                t["l"] += 1
        if r["fault"]:
            table[r["fault"].split(":")[0]]["faults"] += 1
    return sorted(table.values(), key=lambda t: -t["pts"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("league", "scaling"))
    ap.add_argument("--openings", type=int, default=6)
    ap.add_argument("--workers", default="8")
    args = ap.parse_args()
    image = sandbox.image_id(IMAGE_TAG)
    kinds = list(ALGORITHMS)
    arts = {k: str(artifact(k, image)) for k in kinds}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = {"mode": args.mode, "image": image, "cpus": os.cpu_count()}

    if args.mode == "league":
        tasks = league_tasks(kinds, args.openings, arts, image)
        k = int(args.workers.split(",")[0])
        rows, wall, cpu = run_batch(tasks, k)
        out.update(matches=len(rows), workers=k, wall_s=round(wall, 1), cpu_util_pct=cpu,
                   matches_per_hour=round(len(rows) / wall * 3600), standings=standings(kinds, rows),
                   reasons={r: sum(x["result"]["reason"] == r for x in rows) for r in {x["result"]["reason"] for x in rows}},
                   match_s={"median": statistics.median(x["seconds"] for x in rows), "max": max(x["seconds"] for x in rows)},
                   plies_median=statistics.median(x["plies"] for x in rows), rows=rows)
        print(f"{len(rows)} trận, {k} worker, {out['wall_s']}s ({out['matches_per_hour']} trận/giờ, CPU {cpu}%)", flush=True)
        print("kết thúc:", out["reasons"], flush=True)
        for s in out["standings"]:
            print(f"  {s['bot']:<8} {s['pts']:>4} điểm  {s['w']}T {s['d']}H {s['l']}B  lỗi bot {s['faults']}", flush=True)

    else:  # scaling: cùng một lô trận, tăng số worker song song
        tasks = league_tasks(["ab20", "onestep", "greedy", "random"], 4, arts, image)
        out["batch"] = len(tasks)
        out["runs"] = []
        for k in [int(x) for x in args.workers.split(",")]:
            rows, wall, cpu = run_batch(tasks, k)
            secs = [r["seconds"] for r in rows]
            run = {"workers": k, "wall_s": round(wall, 1), "matches_per_hour": round(len(rows) / wall * 3600),
                   "match_s_median": statistics.median(secs), "match_s_max": max(secs), "cpu_util_pct": cpu,
                   "faults": sum(bool(r["fault"]) for r in rows)}
            out["runs"].append(run)
            print(f"  K={k:<2} {run['wall_s']:>6}s  {run['matches_per_hour']:>6} trận/giờ  trận median {run['match_s_median']}s  CPU {cpu}%  lỗi bot {run['faults']}", flush=True)
    path = save(f"algo-{args.mode}-{stamp}.json", out)
    print("saved", path)


if __name__ == "__main__":
    main()
