"""Docker-only execution. No uploaded code ever falls back to host execution."""
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import time
import uuid

from arena.protocol import Fault, strict_json

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("ARENA_WORKER_IMAGE", "uet-arena-worker:v1")


def command(argv, timeout=15):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Fault("worker_unavailable", str(exc)) from exc
    if p.returncode:
        raise Fault("worker_unavailable", p.stderr.decode(errors="replace")[:2000])
    return p.stdout


def image_id(image=None):
    if os.name != "posix":
        raise Fault("worker_unavailable", "Worker controller requires Linux (WSL2 is supported)")
    command(["docker", "info", "--format", "{{.OSType}}"])
    value = command(["docker", "image", "inspect", image or IMAGE, "--format", "{{.Id}}"], timeout=5).decode().strip()
    if not value.startswith("sha256:") or len(value) != 71:
        raise Fault("worker_unavailable", "Worker image must resolve to immutable image ID")
    return value


def options(name, memory_mib, image, mounts, mode):
    # Only fixed directories selected by the controller are mounted; no host
    # credentials, Docker socket, engine state or competitor artifact enters.
    args = ["docker", "run", "--name", name, "--pull=never", "--network=none", "--read-only",
            "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=KILL",
            "--security-opt=no-new-privileges", "--pids-limit=64", "--cpus=1",
            f"--memory={memory_mib}m", f"--memory-swap={memory_mib}m", "--ulimit", "nofile=128:128",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={512 if mode == 'build' else 32}m,mode=1777",
            "--log-driver=none", "-i"]
    for source, target, readonly in mounts:
        if "," in str(source):
            raise Fault("worker_error", "Comma in worker path is unsupported")
        args += ["--mount", f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")]
    return args + [image, mode]


def cleanup(name):
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def build(source, output, profile, image):
    name = "arena-build-" + uuid.uuid4().hex
    output.mkdir(mode=0o777)
    output.chmod(0o777)
    argv = options(name, profile.build_memory_mib, image,
                   [(source.resolve(), "/source", True), (output.resolve(), "/out", False),
                    ((ROOT / "sdk").resolve(), "/sdk", True)], "build")
    try:
        raw = command(argv, timeout=profile.build_ms / 1000 + 15)
        if len(raw) > 2 * 1024 * 1024:
            raise Fault("worker_error", "Oversized build supervisor response")
        report = strict_json(raw)
        if report["ok"]:
            path = output / "bot"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024 or path.read_bytes()[:4] != b"\x7fELF":
                raise Fault("worker_error", "Invalid built ELF artifact")
            # Compiler output has uid 10001 on native Linux. Make a verified
            # controller-owned copy before sealing, without CAP_CHOWN.
            binary = path.read_bytes()
            verified = output / ("verified-" + uuid.uuid4().hex)
            with verified.open("xb") as stream:
                stream.write(binary)
            verified.chmod(0o555)
            verified.replace(path)
            report["artifact_sha256"] = hashlib.sha256(binary).hexdigest()
        return report
    finally:
        cleanup(name)


class Worker:
    def __init__(self, artifact, profile, image):
        self.name = "arena-run-" + uuid.uuid4().hex
        self.buffer = bytearray()
        self.profile = profile
        argv = options(self.name, profile.memory_mib, image,
                       [(artifact.resolve(), "/artifact", True)], "run")
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            boot = self.line(15)
            if boot.get("type") != "boot":
                raise Fault("worker_error", "Missing trusted supervisor handshake")
            if profile.match_ms is not None and "match" not in boot.get("time_controls", []):
                raise Fault("worker_error", "Worker image does not support cumulative clocks; rebuild clock-v2 image")
            self.clock_id = boot["clock_id"]
            # Bound the worker/host monotonic mapping by the probe's send and
            # receive instants. Used for countdown display, never adjudication.
            before = time.monotonic_ns()
            probe = self.rpc({"op": "probe"}, 5)
            after = time.monotonic_ns()
            self.clock_offset_ns = (before + after) // 2 - int(probe["mono_ns"])
            self.clock_uncertainty_ms = (after - before) / 2e6
        except BaseException:
            self.close()
            raise

    def line(self, seconds):
        deadline = time.monotonic() + seconds
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.p.stdout], [], [], remaining)[0]:
                raise Fault("worker_error", "Control transport timeout (not a bot time loss)")
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                raise Fault("worker_error", "Supervisor exited: " + self.p.stderr.read(2048).decode(errors="replace"))
            self.buffer.extend(chunk)
            if len(self.buffer) > 2 * 1024 * 1024:
                raise Fault("worker_error", "Oversized supervisor response")
        line, tail = self.buffer.split(b"\n", 1)
        self.buffer = bytearray(tail)
        return strict_json(line)

    def rpc(self, payload, timeout, on_started=None):
        self.p.stdin.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
        self.p.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            item = self.line(max(0, deadline - time.monotonic()))
            if item.get("type") == "turn_started":
                if on_started:
                    worker_start = int(item["started_mono_ns"])
                    estimated_host_start = worker_start + self.clock_offset_ns
                    item["estimated_started_unix_ms"] = (time.time_ns() + estimated_host_start - time.monotonic_ns()) // 1_000_000
                    item["clock_uncertainty_ms"] = self.clock_uncertainty_ms
                    on_started(item)
                continue
            if not item.get("ok"):
                fault = Fault(item.get("code", "worker_error"), item.get("detail", "Unknown supervisor failure"))
                fault.worker_data = item
                raise fault
            return item.get("result")

    def start(self, match_id, side, context=None):
        msg = {"protocol_version": 2, "type": "init", "match_id": match_id,
               "payload": {"side": side, "engine": "ottv2-v1", "profile": self.profile.public()}}
        if context is not None:
            msg["payload"]["match"] = {k:v for k,v in context.items() if k!='ruleset'}
            if context.get('ruleset'):
                msg['protocol_version']=3
                msg['payload']['engine']='ottv2-experimental-v1'
                msg['payload']['ruleset']=context['ruleset']
        return self.rpc({"op": "start", "profile": self.profile.public(), "message": msg}, self.profile.startup_ms / 1000 + 10)

    def turn(self, message, on_started=None):
        return self.rpc({"op": "turn", "message": message, "budget_ms": self.profile.turn_ms},
                        self.profile.turn_ms / 1000 + 10, on_started)

    def close(self):
        final = None
        try:
            if self.p.poll() is None:
                final = self.rpc({"op": "close"}, 4)
        except (Fault, OSError, ValueError):
            pass
        finally:
            cleanup(self.name)
            if self.p.poll() is None:
                self.p.kill()
            self.p.communicate(timeout=5)
        return final
