"""Gói 2: sandbox interface, shared transport and platform limits (no native sandboxes needed).

The real backends are exercised by tests/sandbox_suite/suite.py (see docs/thiet-ke.md).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from arena import supervisor
from arena.config import memory_limit_mib, profile
from arena.protocol import Fault
from arena import sandbox
from arena.sandbox import seal, toolchain
from arena.sandbox.transport import SupervisorWorker, Transport

ROOT = Path(__file__).resolve().parents[1]

FAKE_SUPERVISOR = textwrap.dedent('''
    import json, sys, time
    mode = sys.argv[1]
    def emit(x): print(json.dumps(x), flush=True)
    if mode == "silent":
        time.sleep(30)
    if mode == "exit":
        sys.stderr.write("boom from fake supervisor"); sys.exit(3)
    if mode == "huge":
        sys.stdout.write("x" * (3 * 1024 * 1024)); sys.stdout.flush(); time.sleep(5)
    emit({"type": "boot", "clock_id": "fake", "time_controls": ["turn", "match"]})
    for line in sys.stdin:
        cmd = json.loads(line)
        if cmd["op"] == "probe":
            emit({"ok": True, "result": {"mono_ns": str(time.monotonic_ns()), "clock_id": "fake"}})
        elif cmd["op"] == "start":
            emit({"ok": True, "result": {"wall_ms": 1.0}})
        elif cmd["op"] == "turn":
            emit({"type": "turn_started", "started_mono_ns": str(time.monotonic_ns())})
            if mode == "fault":
                emit({"ok": False, "code": "timeout", "detail": "Hard worker deadline reached", "remaining_ms": 0})
            else:
                emit({"ok": True, "result": {"message": {"payload": {"from": "a2", "to": "a3"}}, "wall_ms": 2.0}})
        elif cmd["op"] == "close":
            emit({"ok": True, "result": {"exit_code": 0}}); break
''')


class SharedTransport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.script = Path(self.tmp.name) / "fake_supervisor.py"
        self.script.write_text(FAKE_SUPERVISOR)

    def tearDown(self):
        self.tmp.cleanup()

    def worker(self, mode):
        return SupervisorWorker(Transport([sys.executable, str(self.script), mode]), profile())

    def test_protocol_round_trip_and_turn_started_callback(self):
        worker = self.worker("ok")
        started = []
        self.assertEqual(worker.start("m", "red")["wall_ms"], 1.0)
        result = worker.turn({"type": "observation"}, started.append)
        self.assertEqual(result["message"]["payload"], {"from": "a2", "to": "a3"})
        self.assertEqual(len(started), 1)
        self.assertIn("estimated_started_unix_ms", started[0])
        self.assertEqual(worker.close(), {"exit_code": 0})
        self.assertFalse(worker.transport.alive())

    def test_supervisor_fault_keeps_its_code_and_data(self):
        worker = self.worker("fault")
        with self.assertRaises(Fault) as caught:
            worker.turn({"type": "observation"})
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(caught.exception.worker_data["remaining_ms"], 0)
        worker.close()

    def test_transport_failures_are_never_bot_faults(self):
        with patch.object(SupervisorWorker, "boot_seconds", 1):
            with self.assertRaises(Fault) as caught:
                self.worker("silent")
        self.assertEqual(caught.exception.code, "worker_error")
        with self.assertRaises(Fault) as caught:
            self.worker("exit")
        self.assertEqual(caught.exception.code, "worker_error")
        self.assertIn("boom from fake supervisor", caught.exception.detail)
        with self.assertRaises(Fault) as caught:
            self.worker("huge")
        self.assertEqual(caught.exception.code, "worker_error")
        self.assertIn("Oversized", caught.exception.detail)

    def test_cleanup_runs_after_the_supervisor_is_stopped(self):
        order = []
        transport = Transport([sys.executable, str(self.script), "ok"])
        worker = SupervisorWorker(transport, profile(), cleanup=lambda: order.append(transport.alive()))
        worker.close()
        self.assertEqual(order, [False])


class Limits(unittest.TestCase):
    def test_work_directory_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "a.bin").write_bytes(b"x" * (8 * 1024 * 1024))
            supervisor.check_work_quota(folder)
            temp = folder / "temp"
            temp.mkdir()
            (temp / "b.bin").write_bytes(b"x" * (9 * 1024 * 1024))
            with self.assertRaises(Fault) as caught:
                supervisor.check_work_quota(folder)
            self.assertEqual(caught.exception.code, "output_limit")
            # Two folders (Windows: cwd + AppContainer TEMP) share one quota.
            other = Path(tempfile.mkdtemp())
            try:
                (temp / "b.bin").unlink()
                (other / "c.bin").write_bytes(b"x" * (9 * 1024 * 1024))
                supervisor.check_work_quota(folder)
                with self.assertRaises(Fault):
                    supervisor.check_work_quota(folder, other)
                supervisor.check_work_quota(folder, None)
            finally:
                shutil.rmtree(other)
            for index in range(supervisor.WORK_QUOTA_FILES + 1):
                (temp / f"f{index}").write_bytes(b"")
            with self.assertRaises(Fault):
                supervisor.check_work_quota(folder)

    def test_memory_limits_follow_the_measured_platform_rules(self):
        self.assertEqual(memory_limit_mib("linux", "python3", 256), 256)
        self.assertEqual(memory_limit_mib("linux", "cpp17", 256), 256)
        self.assertEqual(memory_limit_mib("windows", "python3", 256), 210)
        self.assertEqual(memory_limit_mib("windows", "cpp17", 256), 250)
        self.assertEqual(memory_limit_mib("windows", "c11", 256), 250)

    def test_seal_rejects_unexpected_compiler_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            out.mkdir()
            (out / "bot").write_bytes(b"#!/bin/sh\n")
            with self.assertRaises(Fault):
                seal({"ok": True}, out, "cpp17")
            (out / "bot").write_bytes(b"\x7fELF" + b"\0" * 32)
            report = seal({"ok": True}, out, "cpp17")
            self.assertEqual(len(report["artifact_sha256"]), 64)
            self.assertEqual(json.loads((out / "runtime.json").read_text()), {"runtime": "cpp17"})
            windows = Path(directory) / "win"
            windows.mkdir()
            built = Path(directory) / "bot.exe"
            built.write_bytes(b"MZ" + b"\0" * 32)
            self.assertTrue(seal({"ok": True}, windows, "c11", built=built, signature=b"MZ")["artifact_sha256"])
            self.assertEqual((windows / "bot").read_bytes()[:2], b"MZ")
            failed = {"ok": False, "code": "build_error"}
            self.assertEqual(seal(dict(failed), Path(directory) / "missing", "cpp17"), failed)


class Backends(unittest.TestCase):
    def test_registry_and_platform_guards(self):
        with self.assertRaises(ValueError):
            sandbox.get_backend("wasm")
        self.assertEqual(sandbox.BACKENDS, ("bwrap", "windows"))
        if os.name != "nt":
            with self.assertRaises(Fault) as caught:
                sandbox.get_backend("windows")
            self.assertEqual(caught.exception.code, "worker_unavailable")

    @unittest.skipUnless(sys.platform.startswith("linux"), "bubblewrap is Linux only")
    def test_bwrap_command_isolates_network_files_uid_and_resources(self):
        from arena.sandbox.bwrap import BwrapBackend
        with tempfile.TemporaryDirectory() as directory:
            rootfs = Path(directory) / "rootfs"
            rootfs.mkdir()
            (rootfs / ".installed").write_text("f" * 64)
            with patch("shutil.which", return_value="/usr/bin/x"):
                backend = BwrapBackend(rootfs=rootfs, state_dir=Path(directory) / "state", cpu=3)
            argv = backend.command(256, 32, ["--ro-bind", "/x", "/artifact"], ["run"])
            self.assertEqual(argv[:3], ["systemd-run", "--user", "--scope"])
            for prop in ("MemoryMax=256M", "MemorySwapMax=0", "CPUQuota=100%", "TasksMax=64"):
                self.assertIn(prop, argv)
            box = argv[argv.index("bwrap"):]
            self.assertEqual(argv[argv.index("bwrap") - 3:argv.index("bwrap")], ["taskset", "-c", "3"])
            for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "@CGROUP@"):
                self.assertIn(flag, box)
            self.assertEqual(box[box.index("--uid") + 1], "10001")
            self.assertEqual(box[box.index("--ro-bind") + 1:box.index("--ro-bind") + 3], [str(rootfs), "/"])
            self.assertEqual(box[box.index("--size") + 1], str(32 * 1024 * 1024))
            self.assertEqual(box[-5:], ["python3", "-u", "-m", "arena.supervisor", "run"])
            self.assertTrue((backend.code / "arena" / "supervisor.py").is_file())
            self.assertEqual(backend.identity()["runtime_id"], "sha256:" + "f" * 64)
            # The cgroup wrapper splices the scope's cgroup in place of @CGROUP@ and execs.
            wrapper = argv[argv.index("-c") + 1]
            done = subprocess.run(["sh", "-c", wrapper, "sh", "echo", "a", "@CGROUP@", "b"], capture_output=True, text=True)
            self.assertRegex(done.stdout.strip(), r"^a --ro-bind /sys/fs/cgroup/\S* /sys/fs/cgroup b$")

    @unittest.skipUnless(shutil.which("zstd") and sys.platform.startswith("linux"), "needs tar --zstd")
    def test_rootfs_install_checks_sha256(self):
        from arena.sandbox.bwrap import install_rootfs, sha256
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "tree").mkdir()
            (folder / "tree" / "hello").write_text("hi")
            raw = folder / "raw.tar.zst"
            subprocess.run(["tar", "--zstd", "-cf", str(raw), "-C", str(folder / "tree"), "."], check=True)
            digest = sha256(raw)
            good = folder / f"rootfs-{digest}.tar.zst"
            raw.rename(good)
            installed = install_rootfs(good, folder / "installed")
            self.assertEqual((installed / "hello").read_text(), "hi")
            self.assertEqual((installed / ".installed").read_text(), digest)
            bad = folder / f"rootfs-{'0' * 64}.tar.zst"
            shutil.copyfile(good, bad)
            with self.assertRaises(SystemExit):
                install_rootfs(bad, folder / "installed")

    def test_windows_toolchain_manifest_pins_every_download(self):
        data = toolchain.manifest()
        self.assertEqual(data["platform"], "windows")
        for archive in data["archives"]:
            self.assertTrue(archive["url"].startswith("https://"))
            self.assertRegex(archive["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(archive["size"], 1_000_000)
        pypy = next(a for a in data["archives"] if a["name"] == "pypy")
        self.assertEqual(pypy["sha256"], "948b8ea58dea5b9917210fe4afd242c788fbfaba1c3f1a25e696a404f703389a")
        self.assertIn("13.3.0", next(a for a in data["archives"] if a["name"] == "gcc")["version"])
        self.assertEqual(data["pch"]["header_sha256"], toolchain.sha256(ROOT / "sdk" / "json.hpp"))
        self.assertRegex(toolchain.runtime_id(), r"^sha256:[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(toolchain.locate(directory))


if __name__ == "__main__":
    unittest.main()
