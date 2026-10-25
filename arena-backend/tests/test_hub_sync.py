from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class HubSync(unittest.TestCase):
    def test_reinstall_removes_stale_code_preserves_excludes_and_external_venv(self):
        script=(Path(__file__).resolve().parents[2]/'scripts/install-hub.sh').read_text()
        block=re.search(r'  for arena_dir in arena-backend uet-bot-arena luat; do\n(.*?)\n  done',script,re.S)[1]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';target=root/'installed';venv=root/'venv'
            for name in ['arena-backend','uet-bot-arena','luat']:
                (source/name).mkdir(parents=True);(target/name).mkdir(parents=True)
                (source/name/'current').write_text('new');(target/name/'obsolete').write_text('old')
            venv.mkdir();(venv/'marker').write_text('keep external environment')
            (target/'arena-backend/.venv').mkdir();(target/'arena-backend/.venv/marker').write_text('excluded')
            code='arena_source="$1"\nfor arena_dir in arena-backend uet-bot-arena luat; do\n'+block.replace('/opt/uet-arena/app/', '"$2"/')+'\ndone'
            subprocess.run(['bash','-eu','-c',code,'sync-test',str(source),str(target)],check=True,capture_output=True)
            for name in ['arena-backend','uet-bot-arena','luat']:
                self.assertFalse((target/name/'obsolete').exists())
                self.assertEqual((target/name/'current').read_text(),'new')
            self.assertTrue((target/'arena-backend/.venv/marker').is_file())
            self.assertEqual((venv/'marker').read_text(),'keep external environment')
