import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_grader_zip", REPO / "scripts/build-grader-zip.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class GraderZipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.source = self.tmp / "src"
        for tree in builder.TREES:
            shutil.copytree(REPO / tree, self.source / tree, ignore=shutil.ignore_patterns("__pycache__"))
        for name in builder.FILES:
            if (REPO / name).is_file():
                (self.source / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / name, self.source / name)

    def test_deterministic_package_has_only_grader_files(self):
        (self.source / "arena-backend/arena/__pycache__").mkdir()
        (self.source / "arena-backend/arena/__pycache__/x.cpython-312.pyc").write_bytes(b"cache")
        first = builder.build(self.source, self.tmp / "a")
        second = builder.build(self.source, self.tmp / "b")
        self.assertEqual(first, second)
        self.assertRegex(first["file"], builder.NAME)
        data = (self.tmp / "a" / first["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), first["sha256"])
        self.assertEqual(json.loads((self.tmp / "a/grader-latest.json").read_text()), first)
        infos = zipfile.ZipFile(self.tmp / "a" / first["file"]).infolist()
        self.assertEqual({(i.create_system, i.date_time, i.external_attr >> 16) for i in infos}, {(3, (1980, 1, 1, 0, 0, 0), 0o644)})
        names = [i.filename for i in infos]
        self.assertIn("scripts/install-grader.ps1", names)
        self.assertIn("arena-backend/arena/grader.py", names)
        self.assertIn("arena-backend/sdk/bot.cpp", names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
        self.assertFalse([n for n in names if not n.startswith(("arena-backend/arena/", "arena-backend/sdk/")) and n not in builder.FILES])
        self.assertEqual(first["grader_version"], builder.re.search(r'VERSION = "([^"]+)"', (REPO / "arena-backend/arena/grader.py").read_text())[1])

    def test_refuses_installer_that_does_not_match_its_hash(self):
        script = self.source / "scripts/install-grader.ps1"
        script.write_text(script.read_text() + "\n# tampered\n")
        with self.assertRaises(SystemExit):
            builder.build(self.source, self.tmp / "out")
        self.assertFalse((self.tmp / "out").exists())

    def test_keeps_only_newest_packages(self):
        out = self.tmp / "out"
        files = []
        for i in range(4):
            (self.source / "arena-backend/sdk/README.md").write_text(f"version {i}\n")
            files.append(builder.build(self.source, out, keep=2)["file"])
        kept = sorted(p.name for p in out.glob("uet-arena-grader-*.zip"))
        self.assertEqual(kept, sorted(files[-2:]))
        self.assertEqual(sorted(p.name for p in out.glob("*.sha256")), sorted(f + ".sha256" for f in files[-2:]))

    def rootfs(self, content):
        path = self.tmp / "build" / f"rootfs-{hashlib.sha256(content).hexdigest()}.tar.zst"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return path

    def test_publishes_checked_rootfs_for_linux_graders(self):
        rootfs, out = self.rootfs(b"toolchain v1"), self.tmp / "out"
        manifest = builder.build(self.source, out, rootfs=rootfs)
        digest = hashlib.sha256(b"toolchain v1").hexdigest()
        self.assertEqual(manifest["linux_rootfs"], {"file": rootfs.name, "sha256": digest, "bytes": 12})
        self.assertEqual((out / rootfs.name).read_bytes(), b"toolchain v1")
        self.assertEqual((out / (rootfs.name + ".sha256")).read_text(), f"{digest}  {rootfs.name}\n")
        self.assertEqual(json.loads((out / "grader-latest.json").read_text()), manifest)
        # A reinstall without --rootfs keeps publishing the rootfs already verified in out.
        self.assertEqual(builder.build(self.source, out)["linux_rootfs"], manifest["linux_rootfs"])

    def test_refuses_rootfs_whose_content_does_not_match_its_name(self):
        wrong = self.tmp / ("rootfs-" + "0" * 64 + ".tar.zst")
        wrong.write_bytes(b"not the named toolchain")
        for bad in (wrong, self.tmp / "toolchain.tar.zst", self.tmp / ("rootfs-" + "1" * 64 + ".tar.zst")):
            with self.assertRaises(SystemExit):
                builder.build(self.source, self.tmp / "out", rootfs=bad)
        self.assertFalse(list((self.tmp / "out").glob("rootfs-*")) if (self.tmp / "out").exists() else [])

    def test_corrupted_published_rootfs_is_not_offered(self):
        out = self.tmp / "out"
        rootfs = self.rootfs(b"toolchain v1")
        builder.build(self.source, out, rootfs=rootfs)
        (out / rootfs.name).write_bytes(b"truncated")
        self.assertNotIn("linux_rootfs", builder.build(self.source, out))
        self.assertNotIn("linux_rootfs", builder.build(self.source, self.tmp / "empty"))

    def test_new_rootfs_wins_and_only_the_previous_one_is_kept(self):
        out = self.tmp / "out"
        names = []
        for i in range(3):
            rootfs = self.rootfs(f"toolchain v{i}".encode())
            names.append(rootfs.name)
            self.assertEqual(builder.build(self.source, out, rootfs=rootfs)["linux_rootfs"]["file"], rootfs.name)
        self.assertEqual(sorted(p.name for p in out.glob("rootfs-*.tar.zst")), sorted(names[-2:]))
        self.assertFalse((out / (names[0] + ".sha256")).exists())


if __name__ == "__main__":
    unittest.main()
