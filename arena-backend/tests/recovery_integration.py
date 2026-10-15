"""Kill an actual controller; verify scoped cleanup and durable queued jobs."""
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from arena import sandbox
from arena.service import Service
from arena.store import Store


def until(check, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError('Recovery condition did not become true')


def main():
    image = sandbox.image_id('uet-arena-worker:clock-v2')
    report = {'image_id': image, 'checks': []}
    with tempfile.TemporaryDirectory(prefix='arena-recovery-') as tmp:
        root = Path(tmp)
        owner = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        client = httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10)
        store = Store(root)
        service = Service(store, start_worker=False)
        jobs = []
        for i in range(3):
            raw = io.BytesIO()
            source = (ROOT/'sdk/bot.py').read_text()
            if i == 0:
                source = source.replace('def choose(payload):', 'def choose(payload):\n    import time\n    time.sleep(100)')
            with zipfile.ZipFile(raw, 'w') as archive:
                archive.writestr('bot.py', source)
            submission = service.submit(str(i), 'v1.0.0', 'python3', raw.getvalue(), uuid.uuid4().hex)
            jobs.append(service.enqueue(str(i), 'benchmark', {'submission_id':submission['id'], 'match_ms':60000}, uuid.uuid4().hex)['id'])
        store.db.close()
        process = None
        foreign = 'arena-recovery-foreign-' + uuid.uuid4().hex
        log = (root/'controller.log').open('w')
        def start(offline=False):
            env = {**os.environ, 'ARENA_API_DATA':str(root), 'ARENA_API_ORIGIN':str(client.base_url).rstrip('/'), 'ARENA_WORKERS':'1'}
            if offline:
                env['DOCKER_HOST'] = 'unix:' + str(root/'unavailable-docker.sock')
            return subprocess.Popen([sys.executable, '-m', 'arena_api', 'serve', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
        def health(path, code):
            try:
                return client.get('/api/v1/health/'+path).status_code == code
            except httpx.HTTPError:
                return False
        try:
            process = start(offline=True)
            until(lambda: health('live', 200))
            assert health('ready', 503)
            check = Store(root, recover_jobs=False)
            assert all(check.get(job)['status'] == 'queued' for job in jobs)
            check.db.close()
            report['checks'].append('unreachable Docker socket: API live, readiness 503, queued jobs retained, no host fallback')
            process.send_signal(signal.SIGKILL); process.wait()
            sandbox.command(['docker','run','-d','--name',foreign,'--label','uet-arena.owner=foreign-test-owner',
                             '--network=none','--entrypoint','python3',image,'-c','import time;time.sleep(240)'])
            process = start()
            until(lambda: health('ready', 200))
            def containers():
                state = Store(root, recover_jobs=False)
                try:
                    playing = state.get(jobs[0])['status'] == 'benchmarking'
                finally:
                    state.db.close()
                return sandbox.command(['docker','ps','-q','--filter','label=uet-arena.owner='+owner,'--filter','name=arena-run-']).decode().split() if playing else []
            original = until(containers)
            process.send_signal(signal.SIGKILL); process.wait()
            process = start()
            until(lambda: health('ready', 200))
            def finished():
                state = Store(root, recover_jobs=False)
                try:
                    values = [state.get(job) for job in jobs]
                    return values if all(j['status'] in ('completed','failed','aborted') for j in values) else None
                finally:
                    state.db.close()
            values = until(finished, 120)
            assert values[0]['status'] == 'aborted', values[0]
            assert all(j['status'] == 'completed' for j in values[1:]), values[1:]
            remaining = sandbox.command(['docker','ps','-aq','--filter','label=uet-arena.owner='+owner]).decode().split()
            assert not set(original).intersection(remaining)
            assert sandbox.command(['docker','inspect',foreign,'--format','{{.State.Running}}']).strip() == b'true'
            report['checks'] += ['SIGKILL: active job aborted without competitive result',
                                 'two queued jobs survive and complete after restart',
                                 'startup removes only own orphan containers; foreign container remains running']
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            sandbox.cleanup_orphans(root)
            sandbox.cleanup(foreign)
            client.close(); log.close()
    output = ROOT/'test-results'; output.mkdir(exist_ok=True)
    (output/'recovery.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
