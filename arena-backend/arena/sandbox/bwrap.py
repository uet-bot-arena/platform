"""bubblewrap + systemd-run (cgroup v2) backend for Linux and WSL2, without root.

The supervisor runs inside bubblewrap on a read-only Ubuntu toolchain rootfs built by
scripts/build-rootfs.sh from a pinned package snapshot and a verified PyPy archive.
Each sandbox is a transient systemd user scope: MemoryMax with no swap, CPUQuota=100%,
TasksMax; optionally pinned to one CPU with taskset. bubblewrap gives it no network
(--unshare-all), its own PID namespace, uid 10001 and a small tmpfs /tmp. seccomp, rlimits and
SIGSTOP freezing stay in supervisor.py.

    python -m arena.sandbox.bwrap install <rootfs-<sha>.tar.zst>   # verify SHA-256, then unpack
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from arena.protocol import Fault, strict_json
from arena.sandbox.transport import SupervisorWorker, Transport

ROOT = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024
SUPERVISOR_FILES = ("__init__.py", "protocol.py", "config.py", "supervisor.py")
# Bind the scope's own cgroup as /sys/fs/cgroup so supervisor.cgroup_metrics() works unchanged.
WRAPPER = ('cg=$(cut -d: -f3 /proc/self/cgroup)\n'
           'for a; do shift; if [ "$a" = @CGROUP@ ]; then set -- "$@" --ro-bind "/sys/fs/cgroup$cg" /sys/fs/cgroup; '
           'else set -- "$@" "$a"; fi; done\n'
           'exec "$@"')


def data_dir():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "uet-arena"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def install_rootfs(tarball, root=None):
    """Check the tarball against its name and .sha256 file, then unpack it read-only."""
    tarball = Path(tarball)
    name = tarball.name
    if not (name.startswith("rootfs-") and name.endswith(".tar.zst")):
        raise SystemExit("Tên file phải là rootfs-<sha256>.tar.zst")
    expected = name[len("rootfs-"):-len(".tar.zst")]
    sidecar = tarball.with_name(name + ".sha256")
    if sidecar.is_file() and sidecar.read_text().split()[0] != expected:
        raise SystemExit("File .sha256 không khớp tên rootfs")
    actual = sha256(tarball)
    if actual != expected:
        raise SystemExit(f"SHA-256 sai: {actual} khác {expected}; không dùng rootfs này")
    base = Path(root or data_dir() / "rootfs")
    final = base / expected
    if (final / ".installed").is_file():
        return final
    base.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=base))
    try:
        subprocess.run(["tar", "--zstd", "-xf", str(tarball), "-C", str(staging), "--no-same-owner"], check=True)
        (staging / ".installed").write_text(expected)
        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def locate_rootfs(root=None):
    base = Path(root or data_dir() / "rootfs")
    found = sorted((p for p in base.glob("*") if (p / ".installed").is_file()), key=lambda p: p.stat().st_mtime) if base.is_dir() else []
    return found[-1] if found else None


class BwrapBackend:
    name, platform = "bwrap", "linux"

    def __init__(self, rootfs=None, state_dir=None, cpu=None):
        if not sys.platform.startswith("linux"):
            raise Fault("worker_unavailable", "Backend bwrap chỉ chạy trên Linux hoặc WSL2")
        self.rootfs = Path(rootfs) if rootfs else locate_rootfs()
        if not self.rootfs or not (self.rootfs / ".installed").is_file():
            raise Fault("worker_unavailable", "Chưa cài rootfs: python -m arena.sandbox.bwrap install rootfs-<sha>.tar.zst")
        self.sha = (self.rootfs / ".installed").read_text().strip()
        for tool in ("bwrap", "systemd-run"):
            if not shutil.which(tool):
                raise Fault("worker_unavailable", f"Thiếu {tool}")
        self.cpu = cpu
        self.state = Path(state_dir or data_dir() / "bwrap")
        self.code = self.snapshot_supervisor()

    def snapshot_supervisor(self):
        """Copy the trusted supervisor into a private directory that is bind-mounted read-only."""
        digest = hashlib.sha256()
        sources = [ROOT / "arena" / name for name in SUPERVISOR_FILES]
        for path in sources:
            digest.update(path.name.encode() + b"\0" + path.read_bytes())
        target = self.state / ("supervisor-" + digest.hexdigest()[:16])
        if not (target / "arena" / "supervisor.py").is_file():
            staging = Path(tempfile.mkdtemp(prefix="supervisor-", dir=self.state.mkdir(parents=True, exist_ok=True) or self.state))
            (staging / "arena").mkdir()
            for path in sources:
                shutil.copyfile(path, staging / "arena" / path.name)
            try:
                staging.rename(target)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)
        return target

    def identity(self):
        return {"backend": self.name, "platform": self.platform, "runtime_id": "sha256:" + self.sha}

    def command(self, memory_mib, tmp_mib, binds, args):
        scope = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                 "-p", f"MemoryMax={memory_mib}M", "-p", "MemorySwapMax=0", "-p", "CPUQuota=100%",
                 "-p", "TasksMax=64", "-p", "OOMPolicy=continue"]
        pin = ["taskset", "-c", str(self.cpu)] if self.cpu is not None else []
        box = ["bwrap", "--ro-bind", str(self.rootfs), "/", "--proc", "/proc", "--dev", "/dev",
               "--size", str(tmp_mib * MIB), "--tmpfs", "/tmp",
               "--ro-bind", str(self.code), "/opt/arena", *binds,
               "--unshare-all", "--unshare-user", "--uid", "10001", "--gid", "10001",
               "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--hostname", "arena",
               "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8",
               "--setenv", "PYTHONPATH", "/opt/arena", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
               "--chdir", "/tmp", "@CGROUP@", "python3", "-u", "-m", "arena.supervisor", *args]
        return scope + ["sh", "-c", WRAPPER, "sh", *pin, *box]

    def build(self, source, output, profile, runtime="cpp17", stage=None):
        from arena.sandbox import seal
        output.mkdir(mode=0o777)
        output.chmod(0o777)
        binds = ["--ro-bind", str(Path(source).resolve()), "/source", "--bind", str(output.resolve()), "/out",
                 "--ro-bind", str((ROOT / "sdk").resolve()), "/sdk"]
        argv = self.command(profile.build_memory_mib, 512, binds, ["build", runtime, *([stage] if stage else [])])
        try:
            done = subprocess.run(argv, capture_output=True, timeout=profile.build_ms / 1000 + 15, start_new_session=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Fault("worker_unavailable", str(exc)) from exc
        if done.returncode or len(done.stdout) > 2 * 1024 * 1024:
            raise Fault("worker_unavailable", (done.stderr or done.stdout).decode(errors="replace")[:2000])
        return seal(strict_json(done.stdout), output, runtime, stage)

    def worker(self, artifact, profile):
        return BwrapWorker(self, artifact, profile)


class BwrapWorker(SupervisorWorker):
    def __init__(self, backend, artifact, profile):
        self.name = "arena-run-" + uuid.uuid4().hex
        argv = backend.command(profile.memory_mib, 32, ["--ro-bind", str(Path(artifact).resolve()), "/artifact"], ["run"])
        super().__init__(Transport(argv, start_new_session=True), profile)


def main(argv):
    if len(argv) == 3 and argv[1] == "install":
        print(install_rootfs(argv[2]))
    elif len(argv) == 2 and argv[1] == "path":
        print(locate_rootfs() or "")
    else:
        raise SystemExit("Dùng: python -m arena.sandbox.bwrap install <rootfs-<sha>.tar.zst> | path")


if __name__ == "__main__":
    main(sys.argv)
