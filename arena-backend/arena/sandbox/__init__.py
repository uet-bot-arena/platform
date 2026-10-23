"""Sandboxed execution: one interface, three backends. No uploaded code ever runs on the host.

Every backend offers ``build(source, out, profile, runtime, stage)``, ``worker(artifact, profile)``
(start/turn/close over the supervisor's JSON-lines protocol) and ``identity()`` (platform and
runtime_id). Backends:

  * ``docker``  — this module (Linux containers; the original behaviour);
  * ``bwrap``   — arena.sandbox.bwrap (bubblewrap + systemd-run cgroup v2, Linux/WSL2);
  * ``windows`` — arena.sandbox.windows (AppContainer + Job Object, Windows 10/11, no admin).

The Docker helpers stay at package level because tests and the hub patch them by name.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading
import time
import uuid

from arena.protocol import Fault, strict_json
from arena.sandbox.transport import SupervisorWorker, Transport

ROOT = Path(__file__).resolve().parents[2]
BACKENDS = ("docker", "bwrap", "windows")
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
        return seal(strict_json(raw), output, runtime, stage)
    finally:
        cleanup(name)


def seal(report, output, runtime, stage=None, built=None, signature=None):
    """Verify the compiler output and replace it with a controller-owned, read-only copy."""
    if report["ok"]:
        path = output / ("translation.ii" if stage == "preprocess" else "bot")
        built = built or path
        signature = signature or (b"PK\x03\x04" if runtime in ("python3", "pypy3", "cpython3") else b"\x7fELF")
        if built.is_symlink() or not built.is_file() or built.stat().st_size > 16 * 1024 * 1024 or stage != "preprocess" and built.read_bytes()[:len(signature)] != signature:
            raise Fault("worker_error", "Invalid built artifact")
        # Compiler output has uid 10001 on native Linux. Make a verified
        # controller-owned copy before sealing, without CAP_CHOWN.
        binary = built.read_bytes()
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


class Worker(SupervisorWorker):
    """One bot in its own Docker container (network=none, read-only, 256 MiB, 1 CPU)."""

    def __init__(self, artifact, profile, image):
        self.name = "arena-run-" + uuid.uuid4().hex
        argv = options(self.name, profile.memory_mib, image,
                       [(artifact.resolve(), "/artifact", True)], "run")
        super().__init__(Transport(argv), profile, cleanup=lambda: cleanup(self.name))


class DockerBackend:
    name, platform = "docker", "linux"

    def __init__(self, image=None, cpu=None):
        self.image, self.cpu = image, cpu

    def identity(self):
        return {"backend": self.name, "platform": self.platform, "runtime_id": image_id(self.image)}

    def build(self, source, output, profile, runtime="cpp17", stage=None):
        return build(source, output, profile, image_id(self.image), runtime, stage)

    def worker(self, artifact, profile):
        return Worker(artifact, profile, image_id(self.image))


def remove_tree(path):
    """rmtree that also removes read-only files (sources are 0444; Windows refuses those)."""
    import stat

    def retry(function, target, _):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            function(target)
        except OSError:
            pass
    shutil.rmtree(path, onexc=retry) if Path(path).exists() else None


def get_backend(name, **options):
    """docker | bwrap | windows. Options are backend specific (see each module)."""
    if name == "docker":
        return DockerBackend(**options)
    if name == "bwrap":
        from arena.sandbox.bwrap import BwrapBackend
        return BwrapBackend(**options)
    if name == "windows":
        from arena.sandbox.windows import WindowsBackend
        return WindowsBackend(**options)
    raise ValueError("Unknown sandbox backend: " + str(name))
