import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from arena.archive import unpack
from arena.config import profile
from arena.protocol import Fault
from arena_api.arena_routes import practice_zip

def bundle(files):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():z.writestr(name,data)
    return b.getvalue()

class ZipHelp(unittest.TestCase):
    def error(self,files,runtime='python3'):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'source'
            with self.assertRaises(Fault) as caught:unpack(bundle(files),target,profile(),runtime)
            self.assertEqual(caught.exception.code,'invalid_archive');self.assertFalse(target.exists())
            return caught.exception.detail

    def test_main_filename_hint_and_one_other_source(self):
        for filename in ('main.py','my_bot.py'):
            self.assertIn(f'đổi tên {filename} thành bot.py',self.error({filename:'pass'}))
        self.assertIn('đổi tên main.cpp thành bot.cpp',self.error({'main.cpp':'int main(){}'},'cpp17'))
        self.assertIn('đổi tên main.c thành bot.c',self.error({'main.c':'int main(){}'},'c11'))

    def test_unsupported_languages_and_wrong_selection(self):
        for ext in ('.java','.jar','.js','.ts'):
            self.assertIn('chưa nhận Java/JavaScript',self.error({'main'+ext:'x'}))
        self.assertEqual(self.error({'bot.py':'pass'},'cpp17'),'ZIP có bot.py: hãy chọn Ngôn ngữ Python 3.')
        self.assertEqual(self.error({'bot.cpp':'x'}),'ZIP có bot.cpp: hãy chọn Ngôn ngữ C++17.')

    def test_forbidden_file_names_first_offender(self):
        self.assertIn('File không được phép: secret.exe.',self.error({'bot.py':'pass','secret.exe':'x','other.txt':'x'}))

    def test_practice_wrappers_and_github_zip_only_select_bot_source(self):
        for files in ({'botcuatoi/bot.py':'pass'}, {'repo-main/README.md':'repo','repo-main/run_local.py':'tools','repo-main/tools/engine.py':'tools','repo-main/bot/bot.py':'pass'}, {'README.md':'repo','bot/bot.py':'pass','tools/other.py':'tools'}):
            raw=practice_zip(bundle(files))
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                self.assertEqual(z.namelist(),['bot.py']);self.assertEqual(z.read('bot.py'),b'pass')
            with tempfile.TemporaryDirectory() as tmp:unpack(raw,Path(tmp)/'source',profile(),'python3')
        raw=bundle({'bot.py':'pass'});self.assertEqual(practice_zip(raw),raw)

    def test_practice_rejects_ambiguous_roots_and_traversal(self):
        for files,hint in (({'a/bot.py':'x','b/bot.py':'y'},'nhiều thư mục gốc'),({'repo/bot/../bot.py':'x'},'không an toàn'),({'repo/bot/sub/helper.py':'x'},'phẳng'),({'repo/bot/bot.py':'x','repo/../evil.py':'y'},'không an toàn')):
            with self.assertRaises(Fault) as caught:practice_zip(bundle(files))
            self.assertEqual(caught.exception.code,'invalid_archive');self.assertIn(hint,caught.exception.detail)
        with self.assertRaises(Fault):practice_zip(b'x'*(1024*1024+1))
