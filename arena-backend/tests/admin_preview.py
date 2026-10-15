"""Opt-in organizer browser fixture: real HTTP/auth/SQLite, disposable data.

Run from arena-backend: .venv/bin/python tests/admin_preview.py
Local fixture login: organizer / preview-only-admin-password
Never uses .platform-state. Synthetic jobs are labelled; no worker is started.
"""
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena.store import Store
from arena.service import Service
from arena_api.accounts import Accounts, now_ms
from arena_api.app import create_app
from arena_api.operations import Operations
from arena_api.competitions import Competitions
from arena_api.settings import Settings


def main():
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='admin-preview-', dir=output) as directory:
        store = Store(directory, recover_jobs=False)
        accounts = Accounts(store)
        accounts.bootstrap_admin('organizer', 'preview-only-admin-password')
        service = Service(store, start_worker=False)
        competitions = Competitions(store, service, accounts)
        ops = Operations(store, accounts, service, competitions)
        for i in range(23):
            profile = {'team':f'Đội thử nghiệm {i+1:02}', 'email':f'captain{i+1}@example.test', 'phone':'',
                'members':[{'name':f'Sinh viên {j+1}', 'student_id':f'2602{i:03}{j}'} for j in range(3)]}
            issued = ops.create_team(profile, f'preview-team-{i+1:02}', None, 'browser-fixture')
            tid = issued['account']['team_id']
            if i == 0:
                for status in ('completed', 'failed', 'aborted'):
                    store.create('job', tid, {'kind':'benchmark', 'status':status, 'submission_ids':[],
                        'report':{'passed':True, 'turn_wall_ms':{'p95_ms':2.64,'samples':72}} if status=='completed' else None,
                        'error':None if status=='completed' else {'code':'fixture_error','detail':'Synthetic browser fixture; not a real bot run'}})
                store.create('submission', tid, {'version':'v0.0.0-preview', 'runtime':'cpp17',
                    'manifest':{'files':['bot.cpp'], 'note':'Synthetic fixture; no source/binary'}, 'source_path':'not-served'})
        with store.db:
            for i in range(2):
                accounts.register({'team':f'Hồ sơ chờ {i+1}', 'email':f'pending{i}@example.test', 'phone':'',
                    'members':[{'name':f'Sinh viên {j}', 'student_id':f'270200{j}'} for j in range(3)], 'confirmed':True}, 'preview')
        # Seed a display-only competition even when Desktop is unavailable.
        # The running API/health remains unmocked and still fails closed.
        with patch('arena.sandbox.image_id', return_value='preview-only-not-a-worker-image'):
            c = competitions.create('browser-fixture', {'title':'Giải thử nghiệm admin', 'max_teams':16, 'turn_ms':15000}, 'preview-competition')
        competitions.transition('browser-fixture', c['id'], 'open', 'preview-open')
        accounts.recover('preview-team-01','captain1@example.test','preview')
        if '--adjudication' in sys.argv:
            from adjudication_preview import seed
            with patch('arena.sandbox.image_id',return_value='preview-only-not-a-worker-image'):
                seed(store,competitions)
        store.db.close()
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0)); sock.listen(128)
        origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
        app = create_app(Settings(data=Path(directory), origin=origin), start_worker=False)
        print('ADMIN_PREVIEW_URL='+origin, flush=True)
        uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning', proxy_headers=False)).run(sockets=[sock])


if __name__ == '__main__':
    main()
