"""Windows supervisor: a trusted Python process next to one bot in an AppContainer + Job Object.

Same control protocol and adjudication rules as supervisor.py on Linux. Differences forced by
Windows: bot pipes are read and written by threads (select() does not work on pipes), freezing
uses NtSuspendProcess, limits come from the Job Object, and memory is counted as commit
(see config.memory_limit_mib). The host chooses every path; the bot cannot.

    python -m arena.supervisor windows run   '<json: artifact, work, toolchain, cpu>'
    python -m arena.supervisor windows build '<json: source, out, sdk, work, toolchain, runtime, stage, cpu>'
"""
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from arena import winbox
from arena.config import memory_limit_mib
from arena.protocol import Fault, response
from arena.supervisor import (CLOCK_ID, MAX_LINE, MAX_LOG, QUOTA_CHECK_NS, check_work_quota, emit,
                              monotonic_ns, pch_compatible, run)

BUILD_TIMEOUT_NS = 30_000_000_000
BUILD_PROCESSES = 64
BUILD_MEMORY_MIB = 1024


def base_env(work, extra_path=None):
    root = os.environ.get("SystemRoot", r"C:\Windows")
    # CreateProcess refuses an AppContainer environment without LOCALAPPDATA; Windows then
    # replaces LOCALAPPDATA, TEMP and TMP with the container's own folder.
    return {"SystemRoot": root, "TEMP": str(work), "TMP": str(work), "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", str(work)),
            "PATH": os.pathsep.join(filter(None, [str(extra_path) if extra_path else None, root + r"\System32"]))}


class WindowsBot:
    def __init__(self, profile, box):
        self.profile = profile
        self.buffer = bytearray()
        self.logs = bytearray()
        self.log_bytes = 0
        self.frozen = False
        self.remaining_ns = None
        self.work = Path(box["work"])
        artifact, toolchain = Path(box["artifact"]), Path(box["toolchain"])
        runtime_file = artifact / "runtime.json"
        runtime = json.loads(runtime_file.read_text())["runtime"] if runtime_file.exists() else "cpp17"
        if runtime == "python3":
            exe = toolchain / "pypy" / "pypy3.exe"
            argv, path = [exe, "-I", "-S", artifact / "bot"], exe.parent
        else:
            argv, path = [artifact / "bot.exe"], None
        self.container = winbox.Container()
        self.job = None
        try:
            self.container.grant(artifact, "RX")
            self.container.grant(self.work, "M")
            self.job = winbox.Job(process_limit=1, process_memory_mib=memory_limit_mib("windows", runtime, profile["memory_mib"]),
                                  cpu=box.get("cpu"), cpu_seconds=math.ceil(profile["job_wall_ms"] / 1000))
            self.p = winbox.Process(argv, self.work, base_env(self.work, path), self.container, self.job)
        except BaseException:
            if self.job:
                self.job.close()
            self.container.close()
            raise
        self.out = queue.Queue()
        self.writer_error = None
        self.reader = threading.Thread(target=self.read_stdout, daemon=True)
        self.reader.start()
        self.log_thread = threading.Thread(target=self.drain_stderr, daemon=True)
        self.log_thread.start()

    def read_stdout(self):
        try:
            while True:
                chunk = self.p.stdout.read(MAX_LINE + 1)
                received = monotonic_ns()
                self.out.put((received, chunk or b""))
                if not chunk:
                    return
        except (OSError, ValueError):
            self.out.put((monotonic_ns(), b""))

    def drain_stderr(self):
        try:
            while True:
                chunk = self.p.stderr.read(4096)
                if not chunk:
                    return
                self.log_bytes += len(chunk)
                self.logs.extend(chunk[:max(0, MAX_LOG - len(self.logs))])
        except (OSError, ValueError):
            return

    def metrics(self):
        usage = self.job.accounting() if self.job else {}
        return {"cpu_usage_usec": usage.get("cpu_usage_usec", 0), "memory_peak_bytes": usage.get("process_peak_bytes", 0),
                "memory_events": {}}

    def freeze(self):
        if not self.frozen and self.p.poll() is None:
            self.p.suspend()
            self.frozen = True

    def resume(self):
        if self.frozen:
            self.p.resume()
            self.frozen = False

    def write_frame(self, frame, done):
        try:
            self.p.stdin.write(frame)
            self.p.stdin.flush()
        except (OSError, ValueError) as exc:
            self.writer_error = exc
        finally:
            done.set()

    def exchange(self, message, budget_ms, kind):
        if self.buffer or not self.out.empty():
            raise Fault("protocol_error", "Unsolicited stdout or duplicate response")
        before = self.metrics()
        started = monotonic_ns()
        bank = kind == "action" and self.profile.get("match_ms") is not None
        if bank and self.remaining_ns is None:
            self.remaining_ns = self.profile["match_ms"] * 1_000_000
        allowance = self.remaining_ns if bank else budget_ms * 1_000_000
        deadline = started + allowance
        if bank:
            budget_ms = allowance // 1_000_000
        message["timing"] = {"clock_id": CLOCK_ID, "started_mono_ns": str(started),
                             "deadline_mono_ns": str(deadline), "budget_ms": budget_ms}
        if bank:
            message["timing"].update(time_control="match", remaining_ms=allowance / 1e6,
                                     initial_ms=self.profile["match_ms"], increment_ms=0)
        frame = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        if len(frame) > MAX_LINE:
            raise Fault("worker_error", "Observation exceeds frame limit")
        emit({"type": "turn_started", "clock_id": CLOCK_ID, "started_mono_ns": str(started),
              "deadline_mono_ns": str(deadline), "budget_ms": budget_ms,
              "worker_unix_ms": time.time_ns() // 1_000_000})
        self.resume()
        written = threading.Event()
        self.writer_error = None
        threading.Thread(target=self.write_frame, args=(frame, written), daemon=True).start()
        quota_at = started + QUOTA_CHECK_NS
        try:
            while True:
                now = monotonic_ns()
                if now >= deadline:
                    raise Fault("timeout", "Hard worker deadline reached")
                if self.log_bytes > MAX_LOG:
                    raise Fault("output_limit", "Cumulative stderr limit exceeded")
                if self.writer_error is not None:
                    raise Fault("crash", "Bot closed stdin")
                if now >= quota_at:
                    check_work_quota(self.work, self.container.folder)
                    quota_at = now + QUOTA_CHECK_NS
                try:
                    received, chunk = self.out.get(timeout=min((deadline - now) / 1e9, 0.01))
                except queue.Empty:
                    continue
                if not chunk:
                    raise Fault("crash", "Bot exited or closed stdout")
                self.buffer.extend(chunk)
                if len(self.buffer) > MAX_LINE:
                    raise Fault("output_limit", "stdout frame exceeds 64 KiB")
                if b"\n" in self.buffer:
                    self.freeze()
                    if received >= deadline:
                        raise Fault("timeout", "Complete response arrived at/after deadline")
                    line, trailing = bytes(self.buffer).split(b"\n", 1)
                    self.buffer.clear()
                    if trailing or not written.is_set():
                        raise Fault("protocol_error", "Extra or premature response")
                    check_work_quota(self.work, self.container.folder)
                    msg = response(line.rstrip(b"\r"), message, kind)
                    after = self.metrics()
                    if bank:
                        self.remaining_ns = max(0, allowance - (received - started))
                    return {"message": msg, "wall_ms": (received - started) / 1e6,
                            **({"remaining_ms": self.remaining_ns / 1e6} if bank else {}),
                            "started_mono_ns": str(started), "received_mono_ns": str(received),
                            "deadline_mono_ns": str(deadline), "clock_id": CLOCK_ID,
                            "cgroup_cpu_ms": (after["cpu_usage_usec"] - before["cpu_usage_usec"]) / 1000,
                            "memory_peak_bytes": after["memory_peak_bytes"]}
        finally:
            if bank and self.remaining_ns == allowance:
                self.remaining_ns = max(0, allowance - (monotonic_ns() - started))
            self.freeze()

    def close(self):
        self.p.kill()
        self.p.wait(2)
        code = self.p.poll()
        self.log_thread.join(timeout=1)
        metrics = self.metrics()
        self.p.close()
        self.job.close()
        self.container.close()
        return {"exit_code": code, "stderr": self.logs.decode("utf-8", errors="replace"),
                "stderr_bytes": self.log_bytes, "cgroup": metrics}


def build(box):
    """Compile inside an AppContainer; emit one JSON report like supervisor.build() on Linux."""
    source, out, sdk, work = Path(box["source"]), Path(box["out"]), Path(box["sdk"]), Path(box["work"])
    toolchain, runtime, stage = Path(box["toolchain"]), box["runtime"], box.get("stage")
    gcc_bin = toolchain / "gcc" / "bin"
    pch = toolchain / "pch"
    if runtime == "python3":
        script = (f"import ast,pathlib,zipfile; p=pathlib.Path({str(source)!r}); files=sorted(p.glob('*.py')); "
                  "[ast.parse(f.read_bytes(),filename=f.name) for f in files]; "
                  f"z=zipfile.ZipFile({str(out / 'bot')!r},'w'); "
                  "[z.writestr('__main__.py' if f.name=='bot.py' else f.name,f.read_bytes()) for f in files]; z.close()")
        compiler = toolchain / "pypy" / "pypy3.exe"
        command, artifact, extra_path = [compiler, "-I", "-S", "-c", script], out / "bot", compiler.parent
    elif runtime == "c11":
        compiler = gcc_bin / "gcc.exe"
        command = [compiler, "-std=c11", "-O2", "-pipe", "-Wall", "-Wextra", f"-I{sdk}", f"-I{source}",
                   *sorted(map(str, source.glob("*.c"))), "-o", out / "bot.exe", "-lm", "-static"]
        artifact, extra_path = out / "bot.exe", gcc_bin
    elif runtime == "cpp17":
        compiler = gcc_bin / "g++.exe"
        command = [compiler, "-std=c++17", "-O2", "-pipe", "-Wall", "-Wextra", f"-I{sdk}", f"-I{source}",
                   *sorted(map(str, source.glob("*.cpp"))), "-o", out / "bot.exe", "-static"]
        local_json = source / "json.hpp"
        if (pch / "json.hpp.gch").is_file() and pch_compatible(source) and (
                not local_json.exists() or local_json.read_bytes() == (sdk / "json.hpp").read_bytes()):
            command[1:1] = ["-include", str(pch / "json.hpp")]
            command.insert(command.index("-o"), str(pch / "json.o"))
        artifact, extra_path = out / "bot.exe", gcc_bin
    else:
        raise Fault("unsupported_runtime", "Unknown runtime")
    if stage:
        raise Fault("worker_error", "Staged builds are a Docker cache optimisation only")
    container = winbox.Container()
    job = None
    start = monotonic_ns()
    log, error, code = bytearray(), None, None
    try:
        container.grant(source, "RX")
        container.grant(sdk, "RX")
        container.grant(out, "M")
        container.grant(work, "M")
        job = winbox.Job(process_limit=BUILD_PROCESSES, job_memory_mib=BUILD_MEMORY_MIB, cpu=box.get("cpu"), cpu_seconds=31)
        process = winbox.Process(command, work, base_env(work, extra_path), container, job, merge_stderr=True)
        process.stdin.close()
        chunks = queue.Queue()

        def read():
            try:
                while chunk := process.stdout.read(4096):
                    chunks.put(chunk)
            except (OSError, ValueError):
                pass
            chunks.put(b"")
        threading.Thread(target=read, daemon=True).start()
        while True:
            if monotonic_ns() - start >= BUILD_TIMEOUT_NS:
                error = "build_timeout"
                break
            try:
                chunk = chunks.get(timeout=0.05)
            except queue.Empty:
                continue
            if not chunk:
                break
            log.extend(chunk)
            if len(log) > MAX_LOG:
                error = "build_output_limit"
                break
        if error:
            job.terminate()
        process.wait(2)
        code = process.poll()
        metrics = job.accounting()
        process.close()
    finally:
        if job:
            job.close()
        container.close()
    ok = (not error and code == 0 and artifact.is_file() and not artifact.is_symlink()
          and artifact.stat().st_size <= 16 * 1024 * 1024)
    version = subprocess.run([str(compiler), "--version"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout.splitlines()
    emit({"ok": ok, "code": error or (None if ok else "build_error"), "exit_code": code,
          "wall_ms": (monotonic_ns() - start) / 1e6, "log": bytes(log[:MAX_LOG]).decode("utf-8", errors="replace"),
          "compiler": version[0] if version else str(compiler.name), "runtime": runtime,
          "cgroup": {"cpu_usage_usec": metrics.get("cpu_usage_usec", 0), "memory_peak_bytes": metrics.get("memory_peak_bytes", 0)}})


def main(argv):
    winbox.quiet_errors()
    if len(argv) != 2 or argv[0] not in ("run", "build"):
        raise SystemExit("Expected: windows run|build '<json>'")
    box = json.loads(argv[1])
    if argv[0] == "build":
        build(box)
        return
    holder = {}

    def make_bot(profile):
        holder["bot"] = WindowsBot(profile, box)
        return holder["bot"]

    def metrics():
        bot = holder.get("bot")
        return bot.metrics() if bot and bot.job else {"cpu_usage_usec": 0, "memory_peak_bytes": 0, "memory_events": {}}
    run(make_bot, metrics)


if __name__ == "__main__":
    main(sys.argv[1:])
