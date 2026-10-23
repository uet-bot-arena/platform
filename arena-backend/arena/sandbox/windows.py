"""Windows backend: AppContainer + Job Object, run by Python for Windows. No admin, no WSL.

The host side copies inputs into a fresh run directory, starts the trusted supervisor
(``python -m arena.supervisor windows ...``) and talks to it over the same JSON-lines protocol
as the Linux backends. The supervisor creates a one-off AppContainer profile for every run, so
two bots on this machine never share a SID or a writable directory.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from arena.protocol import Fault, strict_json
from arena.sandbox import remove_tree, toolchain as toolchains
from arena.sandbox.transport import SupervisorWorker, Transport

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = [sys.executable, "-E", "-s", "-B", "-m", "arena.supervisor", "windows"]


class WindowsBackend:
    name, platform = "windows", "windows"

    def __init__(self, toolchain=None, state_dir=None, cpu=None):
        if os.name != "nt":
            raise Fault("worker_unavailable", "Backend windows chỉ chạy trên Windows")
        self.toolchain = Path(toolchain) if toolchain else toolchains.locate()
        if not self.toolchain:
            raise Fault("worker_unavailable", "Chưa cài toolchain: python -m arena.sandbox.toolchain install")
        # Always pinned to exactly one logical CPU; default is the last one.
        self.cpu = cpu if cpu is not None else max(0, (os.cpu_count() or 1) - 1)
        self.state = Path(state_dir or toolchains.base_dir() / "sandbox")
        self.runs = self.state / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)

    def identity(self):
        return {"backend": self.name, "platform": self.platform, "runtime_id": toolchains.runtime_id()}

    def new_run(self):
        run = self.runs / uuid.uuid4().hex
        run.mkdir()
        return run

    def build(self, source, output, profile, runtime="cpp17", stage=None):
        from arena.sandbox import seal
        if stage:
            raise Fault("worker_error", "Staged preprocessing is unavailable on Windows")
        run = self.new_run()
        try:
            shutil.copytree(source, run / "source")
            shutil.copytree(ROOT / "sdk", run / "sdk")
            for name in ("out", "tmp"):
                (run / name).mkdir()
            box = {"source": str(run / "source"), "out": str(run / "out"), "sdk": str(run / "sdk"), "work": str(run / "tmp"),
                   "toolchain": str(self.toolchain), "runtime": runtime, "cpu": self.cpu}
            try:
                done = subprocess.run(SUPERVISOR + ["build", json.dumps(box)], cwd=ROOT, capture_output=True,
                                      timeout=profile.build_ms / 1000 + 15, creationflags=0x08000000)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise Fault("worker_unavailable", str(exc)) from exc
            if done.returncode or len(done.stdout) > 2 * 1024 * 1024:
                raise Fault("worker_unavailable", (done.stderr or done.stdout).decode(errors="replace")[-2000:])
            report = strict_json(done.stdout)
            output.mkdir()
            python = runtime == "python3"
            built = run / "out" / ("bot" if python else "bot.exe")
            return seal(report, output, runtime, built=built, signature=b"PK\x03\x04" if python else b"MZ")
        finally:
            remove_tree(run)

    def worker(self, artifact, profile):
        return WindowsWorker(self, Path(artifact), profile)


class WindowsWorker(SupervisorWorker):
    def __init__(self, backend, artifact, profile):
        run = backend.new_run()
        try:
            runtime = json.loads((artifact / "runtime.json").read_text())["runtime"]
            (run / "artifact").mkdir()
            (run / "work").mkdir()
            shutil.copyfile(artifact / "bot", run / "artifact" / ("bot" if runtime == "python3" else "bot.exe"))
            shutil.copyfile(artifact / "runtime.json", run / "artifact" / "runtime.json")
            box = {"artifact": str(run / "artifact"), "work": str(run / "work"), "toolchain": str(backend.toolchain),
                   "cpu": backend.cpu}
            transport = Transport(SUPERVISOR + ["run", json.dumps(box)], cwd=str(ROOT))
        except BaseException:
            remove_tree(run)
            raise
        super().__init__(transport, profile, cleanup=lambda: remove_tree(run))
