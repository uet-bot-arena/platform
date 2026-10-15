"""Trusted in-container judge. Bot timestamps are never used for adjudication.

This module runs only inside the worker image; the HTTP API cannot choose paths
or commands. One untrusted, single-thread bot per container, frozen off-turn.
"""
import ctypes
import errno
import json
import math
import os
from pathlib import Path
import resource
import select
import signal
import subprocess
import sys
import threading
import time
import uuid

from arena.protocol import Fault, response

CLOCK_ID = str(uuid.uuid4())
MAX_LINE = 65_536
MAX_LOG = 262_144


def emit(data):
    print(json.dumps(data, separators=(",", ":"), ensure_ascii=True), flush=True)


def cgroup_metrics():
    root = Path("/sys/fs/cgroup")
    cpu = dict(line.split() for line in (root / "cpu.stat").read_text().splitlines())
    return {"cpu_usage_usec": int(cpu["usage_usec"]),
            "memory_peak_bytes": int((root / "memory.peak").read_text()),
            "memory_events": dict((k, int(v)) for k, v in (line.split() for line in (root / "memory.events").read_text().splitlines()))}


def restrict_bot():
    # Dynamic loader/malloc/file reads remain available. Deny creation of extra
    # processes/threads/sessions and access to other processes or the network.
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise OSError("seccomp_init failed")
    try:
        denied = ("fork vfork clone clone3 setsid setpgid unshare setns ptrace "
                  "process_vm_readv process_vm_writev pidfd_open pidfd_getfd pidfd_send_signal "
                  "kill tkill tgkill socket socketpair connect bind listen accept accept4 "
                  "io_uring_setup io_uring_enter io_uring_register bpf perf_event_open").split()
        for name in denied:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise OSError("seccomp rule failed: " + name)
        if lib.seccomp_load(ctx) != 0:
            raise OSError("seccomp_load failed")
    finally:
        lib.seccomp_release(ctx)


def child_setup(memory_mib, cpu_seconds, single_process=True):
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (memory_mib * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    if single_process:
        restrict_bot()


class Bot:
    def __init__(self, profile):
        self.profile = profile
        self.buffer = bytearray()
        self.logs = bytearray()
        self.log_bytes = 0
        self.frozen = False
        self.remaining_ns = None
        work = Path("/tmp/bot-work")
        # Single bot has its own container; allow its uid to write here without
        # granting the supervisor CAP_CHOWN. Nothing secret is stored in /tmp.
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        self.p = subprocess.Popen(["/artifact/bot"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=work, bufsize=0,
                                  env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                                  preexec_fn=lambda: child_setup(profile["memory_mib"], math.ceil(profile["job_wall_ms"] / 1000)))
        os.set_blocking(self.p.stdout.fileno(), False)
        os.set_blocking(self.p.stdin.fileno(), False)
        self.log_thread = threading.Thread(target=self.drain_stderr, daemon=True)
        self.log_thread.start()

    def drain_stderr(self):
        while True:
            chunk = self.p.stderr.read(4096)
            if not chunk:
                break
            self.log_bytes += len(chunk)
            self.logs.extend(chunk[:max(0, MAX_LOG - len(self.logs))])

    def freeze(self):
        if self.p.poll() is None and not self.frozen:
            os.kill(self.p.pid, signal.SIGSTOP)
            _, status = os.waitpid(self.p.pid, os.WUNTRACED)
            if not os.WIFSTOPPED(status):
                self.p.returncode = os.waitstatus_to_exitcode(status)
            self.frozen = True

    def resume(self):
        if self.frozen:
            os.kill(self.p.pid, signal.SIGCONT)
            self.frozen = False

    def exchange(self, message, budget_ms, kind):
        if self.buffer or select.select([self.p.stdout], [], [], 0)[0]:
            raise Fault("protocol_error", "Unsolicited stdout or duplicate response")
        before = cgroup_metrics()
        # The same absolute deadline governs serialization, writes and reads.
        started = time.monotonic_ns()
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
        offset = 0
        try:
            while True:
                now = time.monotonic_ns()
                if now >= deadline:
                    raise Fault("timeout", "Hard worker deadline reached")
                if self.log_bytes > MAX_LOG:
                    raise Fault("output_limit", "Cumulative stderr limit exceeded")
                readers, writers, _ = select.select([self.p.stdout], [self.p.stdin] if offset < len(frame) else [], [],
                                                     min((deadline - now) / 1e9, 0.01))
                if writers:
                    try:
                        offset += os.write(self.p.stdin.fileno(), frame[offset:])
                    except BlockingIOError:
                        pass
                    except BrokenPipeError as exc:
                        raise Fault("crash", "Bot closed stdin") from exc
                if readers:
                    try:
                        chunk = os.read(self.p.stdout.fileno(), MAX_LINE + 1)
                    except BlockingIOError:
                        continue
                    received = time.monotonic_ns()
                    if not chunk:
                        raise Fault("crash", "Bot exited or closed stdout")
                    self.buffer.extend(chunk)
                    if len(self.buffer) > MAX_LINE:
                        raise Fault("output_limit", "stdout frame exceeds 64 KiB")
                    if b"\n" in self.buffer:
                        # Receipt means the worker observed the complete frame,
                        # including LF, strictly before the absolute deadline.
                        self.freeze()
                        if received >= deadline:
                            raise Fault("timeout", "Complete response arrived at/after deadline")
                        line, trailing = bytes(self.buffer).split(b"\n", 1)
                        self.buffer.clear()
                        if trailing or offset != len(frame):
                            raise Fault("protocol_error", "Extra or premature response")
                        msg = response(line, message, kind)
                        after = cgroup_metrics()
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
                self.remaining_ns = max(0, allowance - (time.monotonic_ns() - started))
            self.freeze()

    def close(self):
        if self.p.poll() is None:
            os.kill(self.p.pid, signal.SIGKILL)
        self.p.wait(timeout=2)
        self.log_thread.join(timeout=1)
        return {"exit_code": self.p.returncode, "stderr": self.logs.decode("utf-8", errors="replace"),
                "stderr_bytes": self.log_bytes, "cgroup": cgroup_metrics()}


def build():
    out = Path("/out")
    command = ["g++", "-std=c++17", "-O2", "-pipe", "-Wall", "-Wextra", "-I/sdk", "-I/source", "/source/bot.cpp", "-o", "/out/bot"]
    start = time.monotonic_ns()
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
                         cwd="/tmp", preexec_fn=lambda: child_setup(900, 31, False))
    os.set_blocking(p.stdout.fileno(), False)
    log = bytearray()
    error = None
    while p.poll() is None:
        if time.monotonic_ns() - start >= 30_000_000_000:
            error = "build_timeout"
            break
        if select.select([p.stdout], [], [], 0.05)[0]:
            chunk = os.read(p.stdout.fileno(), 4096)
            log.extend(chunk)
            if len(log) > MAX_LOG:
                error = "build_output_limit"
                break
    if error:
        os.killpg(p.pid, signal.SIGKILL)
    p.wait(timeout=2)
    while len(log) <= MAX_LOG:
        try:
            chunk = os.read(p.stdout.fileno(), 4096)
        except BlockingIOError:
            break
        if not chunk:
            break
        log.extend(chunk)
    artifact = out / "bot"
    if len(log) > MAX_LOG:
        error = "build_output_limit"
    ok = not error and p.returncode == 0 and artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size <= 16 * 1024 * 1024
    emit({"ok": ok, "code": error or (None if ok else "build_error"), "exit_code": p.returncode,
          "wall_ms": (time.monotonic_ns() - start) / 1e6, "log": bytes(log[:MAX_LOG]).decode("utf-8", errors="replace"),
          "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0], "cgroup": cgroup_metrics()})


def run():
    bot = None
    emit({"type": "boot", "clock_id": CLOCK_ID, "cgroup": cgroup_metrics(), "time_controls": ["turn", "match"]})
    try:
        while line := sys.stdin.buffer.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise Fault("worker_error", "Oversized control request")
            cmd = json.loads(line)
            try:
                if cmd["op"] == "start":
                    if bot:
                        raise Fault("worker_error", "Bot already started")
                    bot = Bot(cmd["profile"])
                    emit({"ok": True, "result": bot.exchange(cmd["message"], cmd["profile"]["startup_ms"], "ready")})
                elif cmd["op"] == "turn":
                    emit({"ok": True, "result": bot.exchange(cmd["message"], cmd["budget_ms"], "action")})
                elif cmd["op"] == "close":
                    emit({"ok": True, "result": bot.close() if bot else None})
                    bot = None
                    break
                elif cmd["op"] == "probe":
                    emit({"ok": True, "result": {"mono_ns": str(time.monotonic_ns()), "unix_ns": str(time.time_ns()), "clock_id": CLOCK_ID}})
                else:
                    raise Fault("worker_error", "Unknown control operation")
            except Fault as exc:
                emit({"ok": False, "code": exc.code, "detail": exc.detail,
                      **({"remaining_ms": bot.remaining_ns / 1e6} if bot and bot.remaining_ns is not None else {}),
                      "worker_mono_ns": str(time.monotonic_ns()), "cgroup": cgroup_metrics()})
    finally:
        if bot:
            bot.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        build()
    elif sys.argv[1:] == ["run"]:
        run()
    else:
        raise SystemExit("Expected build or run")
