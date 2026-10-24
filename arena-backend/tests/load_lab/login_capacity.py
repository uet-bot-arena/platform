"""G10 probe: 1,000 teams / 4,000 people; real Argon2 logins and page traffic.

Linux only. Hub, read-only publisher and idle mailer share a 2-vCPU/2-GiB
cgroup. Synthetic DB setup happens offline, outside the measured cgroup;
this probe does not replace the Mailpit/GitHub/native season acceptance.
"""
import argparse
import concurrent.futures as cf
from collections import Counter
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.dependencies import COOKIE

PASSWORD = 'capacity-fixture-only-password'
PAGE = ['/teams/me', '/dashboard', '/submissions?limit=50', '/jobs?limit=50', '/competitions']


def percentile(values, q):
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values)*q)-1)]*1000, 2) if values else None


def stack(hub_command=None):
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    children = []
    try:
        children.append(subprocess.Popen(hub_command or [sys.executable, '-m', 'arena_api', 'serve']))
        for _ in range(200):
            try:
                if httpx.get(os.environ['ARENA_API_ORIGIN']+'/api/v1/health/ready', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if stop.wait(.1):
                return
        else:
            raise RuntimeError('Hub did not start')
        children.extend(subprocess.Popen([sys.executable, '-m', module])
                        for module in ('arena_api.mailer',))
        children.append(subprocess.Popen([sys.executable, '-m', 'arena_api', 'publish']))
        while not stop.wait(.2):
            if any(p.poll() is not None for p in children):
                raise RuntimeError('Hub/publisher/mailer exited')
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(20)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


def seed(data, cookie_teams=60):
    store = Store(data, recover_jobs=False)
    cookies = []
    try:
        accounts = Accounts(store)
        accounts.bootstrap_admin('organizer', PASSWORD)
        encoded = accounts.hash_password(PASSWORD)
        now = int(time.time()*1000)
        with store.transaction():
            for i in range(1000):
                team = uuid.uuid4().hex
                members = [dict(name=f'Person {i}-{j}', student_id=f'{26000000+i*4+j}') for j in range(4)]
                store.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms,legacy_roster,repo_code) VALUES(?,?,?,'','',?,?,0,?)",
                                 (team, f'Team {i}', f'team {i}', json.dumps(members), now, f'{i:06x}'))
                for j in range(4):
                    ident, username = uuid.uuid4().hex, f'{26000000+i*4+j}@vnu.test'
                    store.db.execute("INSERT INTO accounts(id,username,password_hash,role,team_id,must_change,created_ms,email,display_name) VALUES(?,?,?,'student',?,0,?,?,?)",
                                     (ident, username, encoded, team, now, username, f'Person {i}-{j}'))
                    store.db.execute('INSERT INTO team_members VALUES(?,?,?,?,?)', ('default', team, ident, int(j == 0), now))
                    if j == 0 and i < cookie_teams:
                        session = accounts.new_session(accounts.one('SELECT * FROM accounts WHERE id=?', (ident,)))
                        cookies.append(COOKIE+'='+session['token'])
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        store.db.close()
    return cookies


def main(output):
    output = Path(output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='g10-login-') as temporary:
        data = Path(temporary); cookies = seed(data)
        secrets = data/'lab-secrets.toml'
        secrets.write_text('verification_hmac_key = "capacity-lab-key-only-no-production-use-2026"\n')
        secrets.chmod(0o600)
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        origin = f'http://127.0.0.1:{port}'
        env = dict(os.environ, ARENA_CONFIG='none', ARENA_SECRETS=str(secrets), ARENA_API_DATA=str(data),
                   ARENA_API_ORIGIN=origin, ARENA_PORT=str(port), ARENA_HASH_WORKERS='1')
        log = (data/'stack.log').open('w')
        hub = subprocess.Popen(['systemd-run', '--user', '--scope', '--quiet', '-p', 'CPUQuota=200%',
            '-p', 'MemoryMax=2G', '-p', 'MemorySwapMax=0', sys.executable, str(Path(__file__).resolve()), '--stack'],
            env=env, cwd=ROOT, stdout=log, stderr=log)
        stop = threading.Event(); report = dict(teams=1000, people=4000, hash_workers=1,
            hash=dict(algorithm='Argon2id', memory_kib=65536, time_cost=2, parallelism=1),
            fixture='Offline synthetic DB, shared valid password hash; real uncached verification on every login',
            stack=['hub', 'publisher', 'mailer (idle; no email in this probe)'], samples=[], phases=[])
        http = httpx.Client(base_url=origin, timeout=120, limits=httpx.Limits(max_connections=512, max_keepalive_connections=256))
        try:
            for _ in range(200):
                if hub.poll() is not None:
                    raise RuntimeError('Stack exited; inspect temporary stack log')
                try:
                    if httpx.get(origin+'/api/v1/health/ready', timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError('Stack did not start')
            cg = Path('/sys/fs/cgroup'+Path(f'/proc/{hub.pid}/cgroup').read_text().strip().split('::', 1)[1])
            report['cgroup'] = dict(cpu_max=(cg/'cpu.max').read_text().strip(), memory_max=int((cg/'memory.max').read_text()), swap_max=int((cg/'memory.swap.max').read_text()))
            assert report['cgroup']['cpu_max'].split()[0] == '200000', report['cgroup']
            assert report['cgroup']['memory_max'] == 2*1024**3 and report['cgroup']['swap_max'] == 0

            def cpu():
                return int(next(line.split()[1] for line in (cg/'cpu.stat').read_text().splitlines() if line.startswith('usage_usec ')))

            def monitor():
                previous, used = time.monotonic(), cpu()
                while not stop.wait(5):
                    current, next_used = time.monotonic(), cpu()
                    size = sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
                    report['samples'].append(dict(at_seconds=round(current-start, 2), cpu_pct_of_2vcpu=round((next_used-used)/(current-previous)/20000, 2),
                        ram_bytes=int((cg/'memory.current').read_text()), data_bytes=size))
                    previous, used = current, next_used

            pages = []; page_stop = threading.Event()
            def page_view(number):
                out = []
                for path in PAGE:
                    before = time.monotonic()
                    try:
                        response = http.get('/api/v1'+path, headers={'Cookie': cookies[number % len(cookies)]})
                        code = response.status_code
                    except httpx.HTTPError: code = 0
                    out.append((before, time.monotonic()-before, code))
                return out

            def page_load():
                with cf.ThreadPoolExecutor(96) as pool:
                    futures = []; begin = time.monotonic(); number = 0
                    while not page_stop.is_set():
                        if page_stop.wait(max(0, begin+number/24-time.monotonic())): break
                        futures.append(pool.submit(page_view, number)); number += 1
                    for future in futures: pages.extend(future.result())

            start = time.monotonic(); monitoring = threading.Thread(target=monitor); monitoring.start()
            traffic = threading.Thread(target=page_load); traffic.start()
            serial = 0
            def login(number):
                before = time.monotonic()
                try:
                    response = http.post('/api/v1/auth/login', headers={'Cookie':''}, json=dict(username=f'{26000000+number}@vnu.test', password=PASSWORD))
                    code = response.status_code
                except httpx.HTTPError: code = 0
                return time.monotonic()-before, code, before

            with cf.ThreadPoolExecutor(256) as pool:
                for label, count, rate in [('steady-1/s',30,1), ('steady-2/s',60,2), ('steady-4/s',120,4), ('burst-60',60,0), ('burst-200',200,0)]:
                    begin, cpu_begin = time.monotonic(), cpu(); futures = []
                    for number in range(count):
                        if rate: time.sleep(max(0, begin+number/rate-time.monotonic()))
                        futures.append(pool.submit(login, serial)); serial += 1
                    results = [f.result() for f in futures]; end = time.monotonic()
                    codes = {}; latency = []
                    for duration, code, arrived in results: codes[str(code)] = codes.get(str(code),0)+1; latency.append(duration)
                    row = dict(phase=label, start_seconds=round(begin-start,2), end_seconds=round(end-start,2), requests=count,
                        wall_seconds=round(end-begin,3), arrival_span_ms=round((max(r[2] for r in results)-min(r[2] for r in results))*1000,2), p50_ms=percentile(latency,.5), p95_ms=percentile(latency,.95), max_ms=round(max(latency)*1000,2),
                        cpu_pct_of_2vcpu=round((cpu()-cpu_begin)/(end-begin)/20000,2), codes=codes)
                    report['phases'].append(row); print(json.dumps(row), flush=True)
                    assert codes == {'200':count}, row
            page_stop.set(); traffic.join(); stop.set(); monitoring.join()
            for row in report['phases']:
                phase_pages = [(d,c) for t,d,c in pages if row['start_seconds'] <= t-start <= row['end_seconds']]
                row['page_requests'] = len(phase_pages)
                row['page_p95_ms'] = percentile([d for d,c in phase_pages],.95)
                row['page_codes'] = dict(Counter(str(c) for _,c in phase_pages))
            report['ram_peak_bytes'] = int((cg/'memory.peak').read_text())
            report['page_errors'] = sum(c != 200 for _,_,c in pages)
            assert not report['page_errors'], report['page_errors']
        finally:
            stop.set()
            if 'page_stop' in locals(): page_stop.set()
            if 'traffic' in locals(): traffic.join(130)
            if 'monitoring' in locals(): monitoring.join(10)
            hub.terminate()
            try: hub.wait(30)
            except subprocess.TimeoutExpired: hub.kill(); hub.wait()
            http.close()
            log.close()
            if hub.returncode not in (0, -15):
                report['stack_log'] = (data/'stack.log').read_text()[-4000:]
            output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', default='test-results/m1/g10/login-capacity.json')
    parser.add_argument('--stack', action='store_true'); args = parser.parse_args()
    stack() if args.stack else main(args.output)
