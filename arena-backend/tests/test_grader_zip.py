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


if __name__ == "__main__":
    unittest.main()
