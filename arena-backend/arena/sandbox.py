"""Docker-only execution. No uploaded code ever falls back to host execution."""
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import shutil
import tempfile
import threading
import time
import uuid

from arena.protocol import Fault, strict_json

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("ARENA_WORKER_IMAGE", "uet-arena-worker:v1")
_images = {}
_image_lock = threading.Lock()
_image_fetch_lock = threading.Lock()
_refreshing = set()
_context = threading.local()


def sdk_hash():
    return hashlib.sha256(b"".join((ROOT/"sdk"/name).read_bytes() for name in ("json.hpp","jsmn.h"))).hexdigest()


def controller_scope(root, cpu=None):
    _context.owner = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    _context.cpu = cpu


def cleanup_orphans(root):
    controller_scope(root)
    names = command(["docker", "ps", "-aq", "--filter", "label=uet-arena.owner=" + _context.owner]).decode().split()
    for name in names:
        cleanup(name)


def invalidate_images():
    with _image_lock:
        _images.clear()


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
    image = image or IMAGE
    with _image_lock:
        cached = _images.get(image)
        if cached:
            if time.monotonic()-cached[0] >= 60 and image not in _refreshing:
                _refreshing.add(image)
                threading.Thread(target=_refresh_image,args=(image,),daemon=True).start()
            return cached[1]
    with _image_fetch_lock:
        with _image_lock:
            cached = _images.get(image)
            if cached:
                return cached[1]
        return _fetch_image(image)


def _fetch_image(image):
    command(["docker", "info", "--format", "{{.OSType}}"])
    value = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=5).decode().strip()
    if not value.startswith("sha256:") or len(value) != 71:
        raise Fault("worker_unavailable", "Worker image must resolve to immutable image ID")
    # Keep a durable tag: rebuilding a mutable worker tag must not make an
    # already locked competition's image disappear from the local image store.
    command(["docker","image","tag",value,"uet-arena-pinned:"+value[7:]])
    with _image_lock:
        _images[image] = time.monotonic(), value
    return value


def _refresh_image(image):
    try:
        _fetch_image(image)
    except Fault:
        with _image_lock:
            _images.pop(image,None)
    finally:
        with _image_lock:
            _refreshing.discard(image)


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
    if getattr(_context, "owner", None):
        args += ["--label", "uet-arena.owner=" + _context.owner]
    if getattr(_context, "cpu", None) is not None:
        args += ["--cpuset-cpus", str(_context.cpu)]
    return args + [image, mode]


def cleanup(name):
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def build(source, output, profile, image, runtime="cpp17", stage=None):
    name = "arena-build-" + uuid.uuid4().hex
    output.mkdir(mode=0o777)
    output.chmod(0o777)
    argv = options(name, profile.build_memory_mib, image,
                   [(source.resolve(), "/source", True), (output.resolve(), "/out", False),
                    ((ROOT / "sdk").resolve(), "/sdk", True)], "build")
    argv += [runtime]
    if stage:
        argv += [stage]
    try:
        raw = command(argv, timeout=profile.build_ms / 1000 + 15)
        if len(raw) > 2 * 1024 * 1024:
            raise Fault("worker_error", "Oversized build supervisor response")
        report = strict_json(raw)
        if report["ok"]:
            path = output / ("translation.ii" if stage == "preprocess" else "bot")
            signature = b"PK\x03\x04" if runtime in ("python3", "pypy3", "cpython3") else b"\x7fELF"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024 or stage != "preprocess" and path.read_bytes()[:4] != signature:
                raise Fault("worker_error", "Invalid built artifact")
            # Compiler output has uid 10001 on native Linux. Make a verified
            # controller-owned copy before sealing, without CAP_CHOWN.
            binary = path.read_bytes()
            verified = output / ("verified-" + uuid.uuid4().hex)
            with verified.open("xb") as stream:
                stream.write(binary)
            verified.chmod(0o444 if stage == "preprocess" else 0o555)
            verified.replace(path)
            report["artifact_sha256"] = hashlib.sha256(binary).hexdigest()
            if stage != "preprocess":
                (output / "runtime.json").write_text(json.dumps({"runtime": runtime}))
                (output / "runtime.json").chmod(0o444)
        return report
    finally:
        cleanup(name)


def cached_cpp_build(source, output, profile, image, cache_root, lock_for, compile_slots):
    """Cache exact GCC preprocessed bytes, including line/time macros and PCH.

    Only this submission enters each container. No shared competitor cache is
    mounted. Uploaded files never run on the controller or choose compiler args.
    """
    started = time.monotonic()
    cache_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="preprocess-",dir=cache_root) as tmp:
        translated = Path(tmp)/"translated"
        prepared = build(source,translated,profile,image,stage="preprocess")
        if not prepared["ok"]:
            return prepared
        key = hashlib.sha256((image+prepared["artifact_sha256"]).encode()).hexdigest()
        cached = cache_root/key
        with lock_for("translation:"+key):
            hit = (cached/"manifest.json").is_file()
            if hit:
                result = json.loads((cached/"manifest.json").read_text())
                if not (cached/"bot").is_file() or hashlib.sha256((cached/"bot").read_bytes()).hexdigest() != result["artifact_sha256"]:
                    raise Fault("artifact_corrupt","Shared compiled artifact hash mismatch")
            else:
                if cached.exists():
                    cached.rename(cached.with_name(key+"-interrupted-"+str(time.time_ns())))
                with compile_slots:
                    result = build(translated,cached,profile,image,stage="preprocessed")
                if not result["ok"]:
                    return result
                (cached/"manifest.json").write_text(json.dumps(result))
            output.mkdir(mode=0o755)
            for name in ("bot","runtime.json"):
                shutil.copyfile(cached/name,output/name)
                (output/name).chmod(0o555 if name=="bot" else 0o444)
            return {**result,"cache_hit":hit,"compiler_wall_ms":result["wall_ms"],"wall_ms":(time.monotonic()-started)*1000,
                    "translation_sha256":prepared["artifact_sha256"]}


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
            if context.get("basic_rules"):
                msg["payload"]["basic_rules"] = context["basic_rules"]
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
