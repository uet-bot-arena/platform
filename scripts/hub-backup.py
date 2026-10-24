#!/usr/bin/env python3
"""Root-only consistent backup/restore for the fixed install-hub.sh layout."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile

DATA = Path('/var/lib/uet-arena/data')
CONFIG = Path('/etc/uet-arena')
BACKUPS = Path('/var/backups/uet-arena')
UNITS = ['uet-arena-hub', 'uet-arena-publisher', 'uet-arena-mailer']


@contextmanager
def paused():
    running = [u for u in UNITS if subprocess.run(['systemctl', 'is-active', '--quiet', u]).returncode == 0]
    subprocess.run(['systemctl', 'stop', *UNITS], check=True)
    try:
        yield
    finally:
        if running:
            subprocess.run(['systemctl', 'start', *running], check=True)


def backup(offsite=None):
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    target = BACKUPS / ('arena-' + stamp + '.tar.gz')
    with tempfile.TemporaryDirectory(prefix='arena-backup-') as temporary, paused():
        stage = Path(temporary)
        db = stage / 'data' / 'arena.sqlite'
        db.parent.mkdir()
        # sqlite3 .backup gives a coherent DB, including committed WAL records.
        subprocess.run(['sqlite3', str(DATA / 'arena.sqlite'), '.backup ' + str(db)], check=True)
        with sqlite3.connect(db) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        for name in ('sources', 'grader-artifacts'):
            if (DATA / name).exists():
                shutil.copytree(DATA / name, db.parent / name)
        shutil.copytree(CONFIG, stage / 'config')
        (stage / 'manifest.json').write_text(json.dumps(dict(format=1, created=stamp, data_dir=str(DATA))) + '\n')
        pending = target.with_suffix('.partial')
        try:
            with tarfile.open(pending, 'w:gz') as archive:
                for name in ('data', 'config', 'manifest.json'):
                    archive.add(stage / name, arcname=name)
            pending.chmod(0o600)
            with pending.open('rb+') as stream:
                os.fsync(stream.fileno())
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with target.with_suffix(target.suffix + '.sha256').open('w') as stream:
        stream.write(digest + '  ' + target.name + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    sync_local_directory()
    if offsite:
        offsite = offsite.resolve()
        assert offsite.is_dir() and not offsite.is_relative_to(BACKUPS), 'Use an existing separate backup destination'
        for source in (target, target.with_suffix(target.suffix + '.sha256')):
            shutil.copy2(source, offsite / source.name)
        rotate(offsite, 7)
    rotate(BACKUPS, 2)
    sync_local_directory()
    print(json.dumps(dict(backup=str(target), sha256=digest, local_copies=2, offsite=str(offsite) if offsite else None,
        note=None if offsite else 'No offsite destination configured; copy backups off VPS to retain seven versions.')))


def rotate(directory, count):
    for old in sorted(directory.glob('arena-*.tar.gz'), reverse=True)[count:]:
        old.unlink()
        old.with_suffix(old.suffix + '.sha256').unlink(missing_ok=True)


def sync_local_directory():
    descriptor=os.open(BACKUPS,os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore(source):
    source = source.resolve(strict=True)
    expected = source.with_suffix(source.suffix + '.sha256').read_text().split()[0]
    with source.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected, 'Backup SHA mismatch'
    with tempfile.TemporaryDirectory(prefix='arena-restore-') as temporary:
        stage = Path(temporary)
        with tarfile.open(source) as archive:
            assert all(member.isfile() or member.isdir() for member in archive.getmembers()), 'Backup links are refused'
            archive.extractall(stage, filter='data')
        manifest = json.loads((stage / 'manifest.json').read_text())
        assert manifest['format'] == 1 and manifest['data_dir'] == str(DATA)
        with sqlite3.connect(stage / 'data' / 'arena.sqlite') as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert (stage / 'config' / 'config.toml').is_file() and (stage / 'config' / 'secrets.toml').is_file()
        # Refuse to replace an existing season; owner first makes/moves a backup.
        assert not DATA.exists() or not any(DATA.iterdir()), 'Restore requires an empty data directory'
        with paused():
            DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
            for item in (stage / 'data').iterdir():
                target = DATA / item.name
                assert not target.exists(), str(target)
                shutil.move(str(item), target)
            CONFIG.mkdir(mode=0o700, exist_ok=True)
            for item in (stage / 'config').iterdir():
                shutil.copy2(item, CONFIG / item.name)
            subprocess.run(['chown', '-R', 'arena:arena', str(DATA), str(CONFIG)], check=True)
            for root in (DATA, CONFIG):
                root.chmod(0o700)
                for item in root.rglob('*'):
                    item.chmod(0o700 if item.is_dir() else 0o600)
    print('Restored DB, sources, binaries and private configuration. Snapshots regenerate automatically.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    save = commands.add_parser('backup')
    save.add_argument('--offsite-dir', type=Path, help='An already mounted off-VPS destination; retain seven copies there')
    load = commands.add_parser('restore')
    load.add_argument('archive', type=Path)
    args = parser.parse_args()
    assert os.geteuid() == 0, 'Run as root'
    os.umask(0o077)
    if args.action == 'backup':
        configured = CONFIG / 'backup-destination'
        destination = args.offsite_dir or (Path(configured.read_text().strip()) if configured.exists() else None)
        backup(destination)
    else:
        restore(args.archive)


if __name__ == '__main__':
    main()
