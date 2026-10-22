"""Shared destructive-bot suite: every sandbox backend must give the same code for every bot.

    python tests/sandbox_suite/suite.py --backend bwrap      # Linux / WSL2
    python tests/sandbox_suite/suite.py --backend windows    # Python for Windows
    python tests/sandbox_suite/suite.py --backend docker     # needs Docker running

Each attack is injected into the SDK's valid starter bot and runs on the first turn. If the
sandbox lets it through, the bot prints "BREACH" (a protocol_error); if it is blocked, the bot
plays a legal move ("ok"). Resource abuse must end with crash, timeout or output_limit.
Valid bots in C++, C and Python must win or lose exactly as expected. Results are printed as
a table and saved to test-results/sandbox/<backend>.json; exit status 1 on any mismatch.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import platform as host_platform
import shutil
import socket
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from arena import engine  # noqa: E402
from arena.config import profile as legacy_profile  # noqa: E402
from arena.protocol import Fault  # noqa: E402
from arena.sandbox import get_backend  # noqa: E402

BOTS = Path(__file__).resolve().parent / "bots"
SDK = ROOT / "sdk"
PROFILE = replace(legacy_profile(15_000), id="sandbox-suite-v1", turn_ms=2_000, startup_ms=5_000, job_wall_ms=120_000)
# name, bot file, runtime, expected code, turns to play
CASES = [
    ("internet", "net_internet.py", "python3", "ok", 1),
    ("loopback_hub", "net_loopback.py", "python3", "ok", 1),
    ("read_user_secret", "read_secret.py", "python3", "ok", 1),
    ("write_outside_work", "write_outside.py", "python3", "ok", 1),
    ("spawn_child", "spawn.py", "python3", "ok", 1),
    ("fork_bomb", "fork_bomb.c", "c11", "ok", 1),
    ("memory_512mib_cpp", "mem_over.cpp", "cpp17", "crash", 1),
    ("memory_600mib_py", "mem_over.py", "python3", "crash", 1),
    ("memory_200mib_cpp", "mem_ok.cpp", "cpp17", "ok", 1),
    ("memory_60mib_py", "mem_ok.py", "python3", "ok", 1),
    ("memory_100mib_py", "mem_100.py", "python3", "crash", 1),
    ("infinite_loop", "infinite_loop.py", "python3", "timeout", 1),
    ("stdout_flood", "stdout_flood.py", "python3", "output_limit", 1),
    ("stderr_flood", "stderr_flood.py", "python3", "output_limit", 1),
    ("big_file_work", "big_file.py", "python3", "output_limit", 1),
    ("big_file_temp", "big_temp.py", "python3", "output_limit", 1),
    ("compute_off_turn", "offturn.py", "python3", "ok", 2),
    ("threads_more_cpu", "threads.cpp", "cpp17", "ok", 1),
    ("crash_null", "crash.c", "c11", "crash", 1),
]
# red, blue, expected winner (None = any finished game, compared across backends)
MATCHES = [
    ("valid.cpp", "resign.py", "red"),
    ("valid.c", "resign.cpp", "red"),
    ("resign.py", "valid.py", "blue"),
    ("valid.cpp", "valid.c", None),
]
C_ANCHOR = "    if (version < 0 || line[tok[version].start] != '2' || type < 0 || match < 0) return 2;\n"
C_HOOK = '    if (!eq(type, "init")) { static int arena_turn = 0; if (attack(++arena_turn)) { puts("BREACH"); fflush(stdout); continue; } }\n'
CPP_ANCHOR = '            } else if (m.at("type") == "observation" && m.at("match_id") == match) {\n'
CPP_HOOK = '                { static int arena_turn = 0; if (attack(++arena_turn)) { std::cout << "BREACH\\n" << std::flush; continue; } }\n'
RUNTIMES = {".py": "python3", ".c": "c11", ".cpp": "cpp17"}


def source(name, target, values):
    """Write the bot's source tree into target and return its runtime."""
    target.mkdir(parents=True)
    path = BOTS / name
    runtime = RUNTIMES[path.suffix]
    attack = path.read_text(encoding="utf-8")
    for key, value in values.items():
        attack = attack.replace(key, repr(value))
    if runtime == "python3":
        (target / "bot.py").write_text(attack + "\n" + (BOTS / "_base.py").read_text(encoding="utf-8"), encoding="utf-8")
    elif runtime == "c11":
        base = (SDK / "bot.c").read_text(encoding="utf-8")
        assert C_ANCHOR in base and '#include "jsmn.h"\n' in base
        base = base.replace('#include "jsmn.h"\n', '#include "jsmn.h"\n' + attack + "\n", 1).replace(C_ANCHOR, C_ANCHOR + C_HOOK, 1)
        (target / "bot.c").write_text(base, encoding="utf-8")
        shutil.copyfile(SDK / "jsmn.h", target / "jsmn.h")
    else:
        base = (SDK / "bot.cpp").read_text(encoding="utf-8")
        using = "using json = nlohmann::json;\n"
        resign = 'legal.empty() ? json({{"resign",true}})'
        assert CPP_ANCHOR in base and using in base and resign in base
        flag = "#ifndef ARENA_RESIGN_FLAG\n#define ARENA_RESIGN_FLAG 0\n#endif\n"
        base = (base.replace(using, using + attack + "\n" + flag, 1).replace(CPP_ANCHOR, CPP_ANCHOR + CPP_HOOK, 1)
                .replace(resign, '(ARENA_RESIGN_FLAG || legal.empty()) ? json({{"resign",true}})', 1))
        (target / "bot.cpp").write_text(base, encoding="utf-8")
    return runtime


def observation(match_id, turn, state):
    return {"protocol_version": 2, "type": "observation", "match_id": match_id, "turn_id": str(turn),
            "state_version": state["ply"], "payload": engine.observation(state)}


def play_turns(backend, artifact, turns, pause=0.8):
    worker, started = None, time.monotonic()
    try:
        worker = backend.worker(artifact, PROFILE)
        worker.start("suite", "red", {"kind": "match"})
        state = engine.initial_state()
        for turn in range(1, turns + 1):
            result = worker.turn(observation("suite", turn, state))
            state = engine.apply(state, result["message"]["payload"])
            # Hand the turn back to red so the next observation is red's again.
            if not state["result"] and state["to_move"] != "red" and engine.legal_moves(state):
                state = engine.apply(state, engine.legal_moves(state)[0])
            if turn < turns:
                time.sleep(pause)  # off-turn time: a frozen bot must not use it
        return "ok", None, time.monotonic() - started
    except Fault as exc:
        return exc.code, exc.detail[:160], time.monotonic() - started
    except ValueError as exc:
        return "illegal_action", str(exc)[:160], time.monotonic() - started
    finally:
        if worker:
            worker.close()


def play_match(backend, red, blue, max_plies=200):
    workers = {}
    try:
        workers = {"red": backend.worker(red, PROFILE), "blue": backend.worker(blue, PROFILE)}
        for side, worker in workers.items():
            worker.start("match", side, {"kind": "match"})
        state = engine.initial_state()
        for turn in range(1, max_plies + 1):
            side = state["to_move"]
            try:
                result = workers[side].turn(observation("match", turn, state))
                state = engine.apply(state, result["message"]["payload"])
            except (Fault, ValueError) as exc:
                state = engine.finish(state, engine.other(side), getattr(exc, "code", "illegal_action"))
            if state["result"]:
                break
        else:
            state = engine.finish(state, None, "ply_limit")
        return state["result"], state["ply"]
    finally:
        for worker in workers.values():
            worker.close()


def startup(backend, artifact, samples):
    values = []
    for _ in range(samples):
        started = time.monotonic()
        worker = backend.worker(artifact, PROFILE)
        try:
            worker.start("boot", "red", {"kind": "match"})
            values.append((time.monotonic() - started) * 1000)
        finally:
            worker.close()
    values.sort()
    return {"samples": len(values), "p50_ms": round(statistics.median(values), 1),
            "p95_ms": round(values[max(0, round(0.95 * len(values)) - 1)], 1), "max_ms": round(values[-1], 1)}


PY_ALLOC = "keep = []\ndef attack(turn):\n    if turn == 1:\n        keep.append(bytearray(SIZE * 1048576))\n    return False\n"
CPP_ALLOC = ("#include <cstring>\n#include <vector>\nstatic std::vector<char*> keep;\n"
             "static int attack(int turn) { if (turn != 1) return 0; std::size_t n = (std::size_t)SIZE * 1048576; "
             "char* b = new char[n]; std::memset(b, 1, n); keep.push_back(b); return 0; }\n")


def calibrate(backend, work):
    """Largest allocation (MiB, 10 MiB steps) each runtime survives; used to pick memory limits."""
    sizes = list(range(10, 601, 10))
    result = {}
    for runtime, suffix, template in (("python3", ".py", PY_ALLOC), ("cpp17", ".cpp", CPP_ALLOC)):
        low, high, best = 0, len(sizes) - 1, 0
        while low <= high:
            middle = (low + high) // 2
            size = sizes[middle]
            name = f"_cal{suffix}"
            (BOTS / name).write_text(template.replace("SIZE", str(size)), encoding="utf-8")
            try:
                src = work / f"cal-{runtime}-{size}"
                source(name, src, {})
            finally:
                (BOTS / name).unlink()
            out = work / f"cal-out-{runtime}-{size}"
            report = backend.build(src, out, PROFILE, runtime)
            code = play_turns(backend, out, 1)[0] if report["ok"] else "build_failed"
            print(f"{runtime:<8} {size:4d} MiB -> {code}", flush=True)
            if code == "ok":
                best, low = size, middle + 1
            else:
                high = middle - 1
        result[runtime] = best
    print("largest allocation that passes (MiB):", result, flush=True)
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("docker", "bwrap", "windows"))
    parser.add_argument("--startup", type=int, default=20, help="number of boots measured for p50/p95")
    parser.add_argument("--only", help="run one case or match file name")
    parser.add_argument("--calibrate", action="store_true", help="find the largest allocation per runtime")
    args = parser.parse_args()
    backend = get_backend(args.backend)
    identity = backend.identity()
    print(f"backend {args.backend} {identity['platform']} {identity['runtime_id'][:23]}…", flush=True)
    work = Path(tempfile.mkdtemp(prefix="sandbox-suite-"))
    outside = Path(tempfile.mkdtemp(prefix="sandbox-outside-", dir=Path.home()))
    secret = outside / "secrets.toml"
    secret.write_text('token = "suite-secret-value"\n')
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    values = {"__PORT__": listener.getsockname()[1], "__SECRET__": str(secret),
              # Host-only locations: the sandbox's own rootfs (/root, /home) is not a host secret.
              "__HOMES__": [str(Path.home()), str(outside), "/mnt/c/Users/" + os.environ.get("USER", "x"),
                            os.environ.get("USERPROFILE", r"C:\Users\Default")],
              "__OUTSIDE__": [str(outside), str(Path.home()), r"C:\Users\Public", "/mnt/c/Users/Public"]}
    built, rows, failures = {}, [], 0

    def artifact(name):
        if name not in built:
            src = work / ("src-" + name.replace(".", "-"))
            runtime = source(name, src, values)
            out = work / ("out-" + name.replace(".", "-"))
            started = time.monotonic()
            report = backend.build(src, out, PROFILE, runtime)
            if not report["ok"]:
                raise SystemExit(f"build {name} failed: {report['code']}\n{report['log'][:2000]}")
            built[name] = (out, round((time.monotonic() - started) * 1000))
        return built[name][0]

    if args.calibrate:
        calibrate(backend, work)
        return
    try:
        for name, bot, runtime, expected, turns in CASES:
            if args.only and args.only not in (name, bot):
                continue
            code, detail, seconds = play_turns(backend, artifact(bot), turns)
            passed = code == expected
            failures += not passed
            rows.append({"case": name, "bot": bot, "runtime": runtime, "expected": expected, "got": code,
                         "passed": passed, "seconds": round(seconds, 2), "detail": detail})
            print(f"{'PASS' if passed else 'FAIL'}  {name:<22} {runtime:<8} expected {expected:<13} got {code:<13} {seconds:5.2f}s"
                  + ("" if passed else f"  {detail}"), flush=True)
        for red, blue, expected in MATCHES:
            if args.only and args.only not in (red, blue):
                continue
            result, plies = play_match(backend, artifact(red), artifact(blue))
            passed = result.get("winner") == expected if expected else result.get("reason") not in ("worker_error", "worker_unavailable")
            failures += not passed
            rows.append({"case": f"match {red} vs {blue}", "expected": expected or "finished", "got": result,
                         "plies": plies, "passed": passed})
            print(f"{'PASS' if passed else 'FAIL'}  match {red} (red) vs {blue} (blue): winner {result.get('winner')} "
                  f"by {result.get('reason')} after {plies} plies", flush=True)
        boot = startup(backend, artifact("valid.cpp"), args.startup) if args.startup and not args.only else None
        if boot:
            print(f"startup (boot + init, C++ bot, {boot['samples']} runs): p50 {boot['p50_ms']} ms, p95 {boot['p95_ms']} ms", flush=True)
    finally:
        listener.close()
        shutil.rmtree(work, ignore_errors=True)
        breached = [p.name for p in outside.iterdir() if p.name != "secrets.toml"]
        shutil.rmtree(outside, ignore_errors=True)
    if breached:
        failures += 1
        print("FAIL  files appeared outside the sandbox:", breached)
    report = {"backend": args.backend, "identity": identity, "host": host_platform.platform(), "python": sys.version.split()[0],
              "cpus": os.cpu_count(), "profile": PROFILE.public(), "cases": rows, "startup": boot,
              "builds_ms": {k: v[1] for k, v in built.items()}, "failures": failures,
              "finished_unix": int(time.time())}
    out = ROOT / "test-results" / "sandbox"
    out.mkdir(parents=True, exist_ok=True)
    if not args.only:
        (out / f"{args.backend}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows) - failures}/{len(rows)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
