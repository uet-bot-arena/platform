"""Backup integrity, bounded retention and restore without touching host services."""
from contextlib import nullcontext, redirect_stdout
import importlib.util
import io
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('hub_backup', Path(__file__).resolve().parents[2] / 'scripts/hub-backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@unittest.skipUnless(shutil.which('sqlite3'), 'Deployment installs sqlite3 CLI on Ubuntu')
class HubBackup(unittest.TestCase):
    def test_rotation_integrity_and_restore_preserve_db_sources_and_private_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, config, copies, offsite = [root / name for name in ('data', 'config', 'copies', 'offsite')]
            for path in (data, config, offsite):
                path.mkdir()
            for name in ('sources', 'grader-artifacts'):
                (data / name).mkdir()
                (data / name / 'fixture').write_bytes(b'kept-private-content')
            with sqlite3.connect(data / 'arena.sqlite') as db:
                db.execute('CREATE TABLE proof(value TEXT)')
                db.execute("INSERT INTO proof VALUES('complete')")
            (config / 'config.toml').write_text('[hub]\nport=4181\n')
            (config / 'secrets.toml').write_text('verification_hmac_key="backup-fixture-only"\n')
            with patch.multiple(backup, DATA=data, CONFIG=config, BACKUPS=copies), patch.object(backup, 'paused', nullcontext), redirect_stdout(io.StringIO()):
                for _ in range(8):
                    backup.backup(offsite)
                self.assertEqual(len(list(copies.glob('*.tar.gz'))), 2)
                self.assertEqual(len(list(offsite.glob('*.tar.gz'))), 7)
                archive = sorted(copies.glob('*.tar.gz'))[-1]
                original = archive.read_bytes()
                archive.write_bytes(original + b'corruption')
                with self.assertRaisesRegex(AssertionError, 'SHA mismatch'):
                    backup.restore(archive)
                archive.write_bytes(original)
                with self.assertRaisesRegex(AssertionError, 'empty data directory'):
                    backup.restore(archive)
                data.rename(root / 'old-data')
                data.mkdir()
                leftover=data/'leftover'
                leftover.write_text('keep')
                with self.assertRaisesRegex(AssertionError, 'empty data directory'):
                    backup.restore(archive)
                self.assertEqual(leftover.read_text(),'keep')
                leftover.unlink()
                with patch.object(backup.subprocess, 'run') as command:
                    backup.restore(archive)
                    command.assert_called_once_with(['chown', '-R', 'arena:arena', str(data), str(config)], check=True)
                with sqlite3.connect(data / 'arena.sqlite') as db:
                    self.assertEqual(db.execute('SELECT value FROM proof').fetchone(), ('complete',))
                for name in ('sources', 'grader-artifacts'):
                    self.assertEqual((data / name / 'fixture').read_bytes(), b'kept-private-content')
                self.assertEqual((config / 'secrets.toml').stat().st_mode & 0o777, 0o600)
