import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]
STARTER = BACKEND.parent / "starter"


class Starter(unittest.TestCase):
    def run_local(self, *args):
        return subprocess.run([sys.executable, str(STARTER/"run_local.py"), *map(str,args)], cwd=tempfile.gettempdir(),
                              env={**os.environ,"PYTHONIOENCODING":"utf-8"}, capture_output=True, text=True, encoding="utf-8", timeout=15)

    def test_bundled_engine_protocol_and_examples_match_hub_sources(self):
        for name in ("engine.py", "basic_rules.py", "protocol.py"):
            self.assertEqual((STARTER/"tools/arena"/name).read_bytes(),(BACKEND/"arena"/name).read_bytes())
        for name in ("bot.c","bot.py","jsmn.h"):
            self.assertEqual((STARTER/"examples"/name).read_bytes(),(BACKEND/"sdk"/name).read_bytes())
        for name in ("bot.cpp","json.hpp"):
            self.assertEqual((STARTER/"bot"/name).read_bytes(),(BACKEND/"sdk"/name).read_bytes())

    def test_standalone_python_can_check_from_another_working_directory(self):
        result = self.run_local("--bot", STARTER/"examples/bot.py", "--check")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)["plies"],2)

    def test_full_sample_game_terminates_with_a_valid_local_result(self):
        result = self.run_local("--bot", STARTER/"examples/bot.py")
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads(result.stdout)
        self.assertLessEqual(report["plies"],512)
        self.assertIn(report["result"]["winner"],("red","blue",None))
        self.assertNotEqual(report["result"]["reason"],"local_check_complete")

    def test_two_entrypoints_are_rejected_before_executing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ("bot.py","bot.c"):(root/name).write_text("")
            result=self.run_local("--bot",root,"--check")
        self.assertEqual(result.returncode,1)
        self.assertIn("đúng một file chính",result.stderr)

    def test_hanging_bot_is_killed_after_the_startup_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"bot.py"
            path.write_text("import time\ntime.sleep(30)\n")
            result=self.run_local("--bot",path,"--check")
        self.assertEqual(result.returncode,1)
        self.assertIn("quá thời gian",result.stderr)

    def test_oversized_stdout_is_bounded_and_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"bot.py"
            path.write_text("import sys\nsys.stdout.write('x'*70000+'\\n');sys.stdout.flush()\n")
            result=self.run_local("--bot",path,"--check")
        self.assertEqual(result.returncode,1)
        self.assertIn("64 KiB",result.stderr)
