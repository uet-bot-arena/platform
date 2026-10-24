"""Three complete M1 seasons: 60 Mailpit identities, GitHub HTTP, two Windows graders.

Run in WSL from arena-backend. Only the league calendar is accelerated; bot
clocks, leases, SMTP, HTTP, compilation and games run at real speed. The test
factory is never loaded by the production CLI. No real SMTP/GitHub credentials.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import quote, urlsplit
from unittest.mock import patch
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from arena.ladder_clock import boundary
from arena.store import Store
from arena_api.accounts import Accounts
from grader_cluster import Cluster, PASSWORD, windows_temp
from elo_native import source_fixture
from login_capacity import stack
from season_github import GitHubFixture


def factory():
    from fastapi import Depends, Request
    from arena_api.app import create_app
    from arena_api.dependencies import admin_principal
    from arena_api.github_client import GitHubClient
    from arena_api.settings import Settings
    profile_path=os.environ.get('ARENA_LAB_SQL_PROFILE')
    if profile_path:
        import sql_profile
        sqlite3.connect=sql_profile.connect
    app = create_app(Settings())
    clock_file = Path(os.environ['ARENA_LAB_CLOCK'])
    clock = [json.loads(clock_file.read_text()), time.monotonic()]
    def now_ms():return clock[0]+int((time.monotonic()-clock[1])*1000)
    import _argon2_cffi_bindings._ffi as argon_ffi
    binding_sha=hashlib.sha256(Path(argon_ffi.__file__).read_bytes()).hexdigest()
    hash_metrics=dict(calls=0,cpu_seconds=0.0);hash_lock=threading.Lock()
    original = app.router.lifespan_context
    github_origin = os.environ['ARENA_LAB_GITHUB']

    def transport(method, url, headers, body, limit):
        parsed = urlsplit(url)
        assert parsed.netloc in ('api.github.com', 'codeload.github.com')
        return GitHubClient.http(method, github_origin+parsed.path+('?' + parsed.query if parsed.query else ''), headers, body, limit)

    @asynccontextmanager
    async def lifespan(app):
        calendar = SimpleNamespace(time_ns=lambda:now_ms()*1_000_000, time=lambda:now_ms()/1000)
        # Install the transport before intake threads start, including when a
        # mature capacity DB already contains repositories at startup.
        client=lambda key,limit:GitHubClient(key,limit,transport=transport)
        with patch('arena_api.competitions.now_ms', now_ms), patch('arena_api.elo_scheduler.time', calendar), patch('arena_api.github.GitHubClient',client):
            async with original(app):
                app.state.github.clock = now_ms
                verify=app.state.accounts.verify
                def timed_verify(*args,**kwargs):
                    began=time.thread_time()
                    try:return verify(*args,**kwargs)
                    finally:
                        with hash_lock:
                            hash_metrics['calls']+=1;hash_metrics['cpu_seconds']+=time.thread_time()-began
                app.state.accounts.verify=timed_verify
                try:yield
                finally:
                    if profile_path:sql_profile.save(profile_path)
    app.router.lifespan_context = lifespan

    @app.get('/api/v1/admin/__lab/hash-cpu',include_in_schema=False)
    async def hash_cpu(auth=Depends(admin_principal)):
        with hash_lock:return dict(hash_metrics,parameters=dict(memory_kib=app.state.accounts.hasher.memory_cost,time_cost=app.state.accounts.hasher.time_cost,parallelism=app.state.accounts.hasher.parallelism,binding_sha256=binding_sha))

    @app.post('/api/v1/admin/__lab/calendar', include_in_schema=False)
    async def advance(request: Request, auth=Depends(admin_principal)):
        value = (await request.json())['now_ms']
        assert isinstance(value,int) and value >= now_ms()
        clock_file.write_text(json.dumps(value)); clock[:] = [value,time.monotonic()]
        return dict(now_ms=value)
    return app


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


def query(data, sql, args=()):
    with sqlite3.connect('file:'+quote(str(data/'arena.sqlite'))+'?mode=ro',uri=True,timeout=10) as db:
        db.execute('PRAGMA query_only=ON'); db.row_factory=sqlite3.Row
        return [dict(row) for row in db.execute(sql,args)]


def disk(data):
    logical = allocated = 0
    for path in data.rglob('*'):
        try:
            if path.is_file():
                stat=path.stat(); logical+=stat.st_size; allocated+=stat.st_blocks*512
        except FileNotFoundError: pass
    return dict(logical_bytes=logical,allocated_bytes=allocated)


def wait(condition, description, seconds=600):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        value=condition()
        if value:return value
        time.sleep(.2)
    raise AssertionError('Timed out: '+description)


def run(number, output, demo=False):
    cluster=Cluster(); cluster.results['mode']='season-m1'
    github=GitHubFixture(); clients=[]; mailpit=None; monitoring=None; stop=threading.Event()
    result=dict(run=number,teams=15,people=60,windows_graders=2,slots=2,
        calendar='Accelerated fixed Vietnam boundaries; bot/lease clocks real',checks=[],samples=[],stages=[])
    started=time.monotonic(); log_file=output/f'run-{number}-stack.log'; log=log_file.open('w')
    try:
        smtp,web=port(),port(); mailbox=f'http://127.0.0.1:{web}'
        def pause(label):
            if demo:
                print(f'DEMO: {label} | Hub {cluster.origin}/#/home | Mailpit {mailbox}',flush=True)
                input('Nhấn Enter để tiếp tục; dữ liệu test sẽ được dọn khi kết thúc. ')
        clock_file=cluster.data/'calendar.json'; clock=int(time.time()*1000); clock_file.write_text(json.dumps(clock))
        key=cluster.data/'fixture-app.pem'
        subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        key.chmod(0o600); webhook=secrets.token_urlsafe(32)
        private=cluster.data/'lab-secrets.toml'
        private.write_text('\n'.join(k+'='+json.dumps(v) for k,v in dict(verification_hmac_key=secrets.token_urlsafe(32),github_app_id='123456',github_app_private_key=str(key),github_webhook_secret=webhook).items()))
        private.chmod(0o600)
        config=cluster.data/'lab.toml'
        config.write_text(f'''[hub]
data_dir={json.dumps(str(cluster.data))}
origin={json.dumps(cluster.origin)}
port={cluster.port}
hash_workers=1
[[email.providers]]
name="mailpit"
host="127.0.0.1"
port={smtp}
tls="none"
sender="no-reply@vnu.test"
daily_limit=10000
hourly_limit=10000
[accounts]
student_email='^\\d{{8}}@vnu\\.test$'
staff_domains=["vnu.test"]
''')
        cluster.env.update(ARENA_CONFIG=str(config),ARENA_SECRETS=str(private),ARENA_LAB_CLOCK=str(clock_file),ARENA_LAB_GITHUB=github.origin)
        store=Store(cluster.data,recover_jobs=False); Accounts(store).bootstrap_admin('organizer',PASSWORD); store.db.close()
        mailpit=subprocess.Popen([str(Path.home()/'.local/bin/mailpit'),'--smtp',f'127.0.0.1:{smtp}','--listen',f'127.0.0.1:{web}','--database',str(cluster.data/'mailpit.db'),'--disable-version-check'],stdout=log,stderr=log)
        py=str(ROOT/'.venv/bin/python')
        cluster.hub=subprocess.Popen(['systemd-run','--user','--scope','--quiet','-p','CPUQuota=200%','-p','MemoryMax=2G','-p','MemorySwapMax=0',py,str(Path(__file__).resolve()),'--stack'],env=cluster.env,cwd=ROOT,stdout=log,stderr=log)
        health=httpx.Client(timeout=2,verify=False); clients.append(health)
        def ready(origin,path):
            try:return health.get(origin+path).status_code==200
            except httpx.HTTPError:return False
        wait(lambda:ready(mailbox,'/api/v1/info'),'Mailpit',30)
        wait(lambda:ready(cluster.origin,'/api/v1/health/ready'),'hub',30)
        cg=Path('/sys/fs/cgroup'+Path(f'/proc/{cluster.hub.pid}/cgroup').read_text().strip().split('::',1)[1])
        result['cgroup']=dict(cpu_max=(cg/'cpu.max').read_text().strip(),memory_max=int((cg/'memory.max').read_text()),swap_max=int((cg/'memory.swap.max').read_text()))
        assert result['cgroup']==dict(cpu_max='200000 100000',memory_max=2*1024**3,swap_max=0)
        def cpu():return int(next(line.split()[1] for line in (cg/'cpu.stat').read_text().splitlines() if line.startswith('usage_usec ')))
        def monitor():
            before,used=time.monotonic(),cpu()
            while not stop.wait(5):
                now,next_used=time.monotonic(),cpu()
                result['samples'].append(dict(seconds=round(now-started,3),cpu_pct_of_2vcpu=round((next_used-used)/(now-before)/20000,2),ram_bytes=int((cg/'memory.current').read_text()),**disk(cluster.data)))
                before,used=now,next_used
        monitoring=threading.Thread(target=monitor);monitoring.start()
        cluster.admin.headers['X-CSRF-Token']=cluster.api(cluster.admin,'POST','/auth/login',json=dict(username='organizer',password=PASSWORD))['csrf_token']
        cluster.grader('season-win-a','windows','0');cluster.grader('season-win-b','windows','2')
        cluster.wait_online(['season-win-a','season-win-b'])
        result['stages'].append(dict(stage='base',**disk(cluster.data)))
        pause('Hub và hai máy chấm đã sẵn sàng')
        people=[httpx.Client(base_url=cluster.origin,timeout=180,verify=False) for _ in range(60)];clients.extend(people)
        def enroll(i):
            client=people[i];email=f'{26000000+i}@vnu.test'
            request=cluster.api(client,'POST','/auth/email/request',expected=202,json=dict(email=email))
            def code():
                for message in health.get(mailbox+'/api/v1/messages?limit=100').json()['messages']:
                    if any(to['Address']==email for to in message['To']):return re.search(r': (\d{6})$',message['Subject']).group(1)
            otp=wait(code,'email '+str(i),60)
            wait(lambda:client.get('/api/v1/auth/email/requests/'+request['id']).json()['status']=='sent','delivery commit',10)
            proof=cluster.api(client,'POST','/auth/email/verify',json=dict(request_id=request['id'],code=otp))
            cluster.api(client,'POST','/auth/enroll',expected=201,json=dict(request_id=request['id'],verification_token=proof['verification_token'],password=PASSWORD,display_name=f'Sinh viên {i}'))
            session=cluster.api(client,'POST','/auth/login',json=dict(username=email,password=PASSWORD))
            client.headers['X-CSRF-Token']=session['csrf_token']
        with ThreadPoolExecutor(60) as pool:list(pool.map(enroll,range(60)))
        result['checks'].append('60 personal registrations verified through real SMTP and Mailpit HTTP')
        print('PASS',number,'60 verified people',flush=True)
        teams=[]
        for i in range(15):
            captain=people[i*4];team=cluster.api(captain,'POST','/teams',expected=201,json=dict(name=f'Season {number} team {i}'))
            for member in people[i*4+1:i*4+4]:cluster.api(member,'POST','/teams/join',json=dict(code=team['invite_code']))
            team=captain.get('/api/v1/users/me').json()['team'];assert len(team['roster'])==4
            teams.append((captain,team))
        result['checks'].append('15 full four-person teams created/joined via personal APIs')
        print('PASS',number,'15 teams',flush=True)
        pause('Đã tạo đủ tài khoản và đội; chuẩn bị nộp bot')
        deadline=boundary(clock,next=True)+6*3600_000+60_000
        item=cluster.api(cluster.admin,'POST','/admin/competitions',expected=201,json=dict(title=f'M1 season {number}',format='elo',max_teams=20,match_ms=60000,grader_platform='windows',submission_deadline=deadline,official=dict(general_rounds=3,group_size=8,finalists=4)))
        ident=item['id'];cluster.api(cluster.admin,'POST','/admin/competitions/'+ident+'/open')
        shas={}
        for i,(captain,team) in enumerate(teams):
            archive,_=source_fixture(i);repo=github.add('captain-'+str(i),'ott2026-'+team['repo_code'],archive,clock)
            shas[team['id']]=repo['sha']
            cluster.api(captain,'POST','/teams/me/github',expected=201,json=dict(url='https://github.com/'+repo['owner']+'/'+repo['name']))
            body=json.dumps(dict(repository=dict(id=repo['id']),after=repo['sha'],ref='refs/heads/main')).encode()
            signature='sha256='+hmac.new(webhook.encode(),body,hashlib.sha256).hexdigest()
            response=health.post(cluster.origin+'/api/v1/github/webhook',content=body,headers={'X-Hub-Signature-256':signature,'X-GitHub-Delivery':uuid.uuid4().hex,'X-GitHub-Event':'push','Content-Type':'application/json'})
            assert response.status_code==202,response.text
        def checks_passed():
            rows=query(cluster.data,"SELECT state,COUNT(*) AS n FROM github_checks GROUP BY state")
            if any(r['state']=='fail' for r in rows):
                result['failed_checks']=query(cluster.data,"SELECT id,json_extract(data,'$.error') AS error,json_extract(data,'$.report.passed') AS passed,json_extract(data,'$.report.fault') AS fault FROM documents WHERE kind='job' AND json_extract(data,'$.status')='failed'")
                raise AssertionError(result['failed_checks'] or rows)
            return rows==[dict(state='pass',n=15)]
        wait(checks_passed,'15 native 72-sample checks',1200)
        wait(lambda:len({r['repo'] for r in github.statuses if r['state']=='success'})==15,'GitHub final statuses',30)
        payload=query(cluster.data,"SELECT data FROM documents WHERE kind='job' AND json_extract(data,'$.kind')='benchmark' AND json_extract(data,'$.status')='completed' LIMIT 1")[0]['data']
        (output/'native-job-template.json').write_text(payload+'\n',encoding='utf-8')
        result['stages'].append(dict(stage='sources-and-checks',**disk(cluster.data)))
        result['checks'].append('GitHub App JWT/token, branch/tree/tarball, signed push, 15 Windows checks and 15 success statuses')
        for number_round in range(1,4):
            pause('Chuẩn bị mở ladder đợt '+str(number_round))
            clock=boundary(clock,next=True)+500
            cluster.api(cluster.admin,'POST','/admin/__lab/calendar',json=dict(now_ms=clock))
            wait(lambda:query(cluster.data,"SELECT id FROM elo_rounds WHERE competition_id=? AND phase='ladder' AND number=? AND status='completed'",(ident,number_round)),f'ladder round {number_round}',900)
            print('PASS',number,'ladder',number_round,flush=True)
        pause('Chuẩn bị chốt hạn và chạy xếp hạng chính thức')
        cluster.api(cluster.admin,'POST','/admin/__lab/calendar',json=dict(now_ms=deadline+1))
        def official_done():
            state=cluster.api(cluster.admin,'GET','/competitions/'+ident)
            assert state['status']!='paused',state['config'].get('elo_error')
            return state if state['status']=='completed' else None
        final=wait(official_done,'official general and round robin',1200)
        assert len(final['config']['finalists'])==4
        games=query(cluster.data,'SELECT g.*,r.phase FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=?',(ident,))
        assert len(games)==140 and all(g['status']=='completed' and g['red_sha']==shas[g['red_team_id']] and g['blue_sha']==shas[g['blue_team_id']] for g in games)
        assert not query(cluster.data,"SELECT id FROM documents WHERE kind='job' AND json_extract(data,'$.status') IN ('failed','aborted')")
        assert not github.errors,github.errors
        standings=cluster.api(cluster.admin,'GET','/competitions/'+ident+'/standings')
        assert standings['ranking']=='round_robin_points' and len(standings['matrix'])==56
        wait(lambda:health.get(cluster.origin+'/snapshots/standings/'+ident+'.json').status_code==200,'published standings',20)
        wait(lambda:health.get(cluster.origin+'/snapshots/standings/'+ident+'.json').json()['items']==standings['items'],'final snapshot matches standings',20)
        cluster.api(cluster.admin,'POST','/admin/retention/run')
        source_sizes=[]
        for source in query(cluster.data,"SELECT data FROM documents WHERE kind='submission' AND team IN (SELECT id FROM teams)"):
            archive=Path(json.loads(source['data'])['source_path']).with_suffix('.zip')
            assert archive.is_file(),archive
            source_sizes.append(archive.stat().st_size)
        binary_sizes=[p.stat().st_size for p in (cluster.data/'grader-artifacts/windows').glob('*.gz')]
        result['storage_measurements']=dict(
            source_zip_bytes=source_sizes,binary_gzip_bytes=binary_sizes,
            job_documents=query(cluster.data,"SELECT json_extract(data,'$.kind') AS kind,COUNT(*) AS count,AVG(length(data)) AS mean_bytes FROM documents WHERE kind='job' GROUP BY 1"),
            replay_archives=query(cluster.data,'SELECT COUNT(*) AS count,AVG(length(data)) AS mean_bytes,MIN(length(data)) AS min_bytes,MAX(length(data)) AS max_bytes FROM replay_archives'),
            elo_rounds=query(cluster.data,'SELECT r.phase,length(r.snapshot)+length(r.after_state)+length(r.config) AS bytes,(SELECT COUNT(*) FROM json_each(r.snapshot,\'$.entries\')) AS teams FROM elo_rounds r WHERE competition_id=?',(ident,)),
            elo_games=query(cluster.data,'SELECT COUNT(*) AS count,AVG(length(red_entry)+length(blue_entry)+length(result)) AS mean_entry_result_bytes FROM elo_games WHERE round_id IN (SELECT id FROM elo_rounds WHERE competition_id=?)',(ident,)))
        assert 'Traceback' not in log_file.read_text() and 'Competition scheduler failed' not in log_file.read_text(),'Unexpected error in stack log'
        result['checks'].extend(['3 fixed ladder rounds +3 automatic general rounds +top8 double round robin56 games; selected4','140 native Windows games, pinned GitHub SHAs, no failed/aborted jobs','public snapshot matches hub standings'])
        result.update(games=len(games),rounds=query(cluster.data,'SELECT phase,number,status FROM elo_rounds WHERE competition_id=? ORDER BY started_ms',(ident,)),github_http_requests=github.requests,ram_peak_bytes=int((cg/'memory.peak').read_text()),wall_seconds=round(time.monotonic()-started,3))
        result['stages'].append(dict(stage='completed',**disk(cluster.data)))
        if number==1:
            with sqlite3.connect('file:'+quote(str(cluster.data/'arena.sqlite'))+'?mode=ro',uri=True) as source_db, sqlite3.connect(output/'storage-fixture.sqlite') as target_db:
                source_db.backup(target_db)
                from arena.service import EVENT_TYPES
                candidates=[]
                for job_raw,archive_raw in source_db.execute("SELECT d.data,a.data FROM documents d JOIN replay_archives a ON a.job=d.id WHERE d.kind='job' AND json_extract(d.data,'$.kind')='match' AND json_extract(d.data,'$.status')='completed' AND json_extract(d.data,'$.profile.match_ms')=10000"):
                    replay=json.loads(gzip.decompress(archive_raw));job=json.loads(job_raw)
                    native_report=dict(job['report'],**replay.get('report',{}))
                    events=[e for e in replay['events'] if e['type'] in EVENT_TYPES]
                    span=events[-1]['server_unix_ms']-events[0]['server_unix_ms']
                    if span>0 and len(native_report.get('moves',[]))>=10 and not native_report.get('fault') and not native_report.get('startup_faults'):
                        candidates.append((len(events)/span,job['id'],events,native_report,span))
                assert candidates,'Need a real played 10-second game for the load fixture'
                _,job_id,events,native_report,span=max(candidates,key=lambda v:(v[0],v[1]))
                (output/'native-match-report.json').write_text(json.dumps(native_report,separators=(',',':'))+'\n')
                (output/'native-event-trace.json').write_text(json.dumps(events,separators=(',',':'))+'\n')
                (output/'native-trace-selection.json').write_text(json.dumps(dict(policy='Highest event density among completed 10-second games with at least10moves and no bot/startup fault in season1',
                    candidates=len(candidates),job_id=job_id,events=len(events),span_ms=span,moves=len(native_report['moves'])),indent=2)+'\n')
        print('PASS COMPLETE SEASON',number,result['wall_seconds'],'seconds',flush=True)
        pause('Kết quả cuối và snapshot đã khớp; Enter để dọn dữ liệu test')
    finally:
        stop.set()
        if monitoring:monitoring.join(10)
        for client in clients:client.close()
        if mailpit:
            mailpit.terminate();mailpit.wait(20)
        github.close()
        result['cluster_checks']=cluster.results['checks']
        # Only clean the new temporary directories created by this run.
        assert cluster.shared.resolve().parent==windows_temp().resolve() and cluster.shared.name.startswith('uet-cluster-')
        assert cluster.data.resolve().parent==Path('/tmp') and cluster.data.name.startswith('cluster-hub-')
        cluster.stop();result['grader_logs']=cluster.results.get('logs',{});log.close()
        (output/f'run-{number}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--demo',action='store_true',help='Tạm dừng từng cảnh để quay video; không dùng để đo hiệu năng')
    parser.add_argument('--stack',action='store_true');parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    if args.serve:
        import uvicorn
        uvicorn.run(factory,factory=True,host='127.0.0.1',port=int(os.environ['ARENA_PORT']),access_log=os.environ.get('ARENA_LAB_ACCESS_LOG')=='1',log_level='info' if os.environ.get('ARENA_LAB_ACCESS_LOG')=='1' else 'warning',proxy_headers=False)
    elif args.stack:stack([sys.executable,str(Path(__file__).resolve()),'--serve'])
    else:
        output=ROOT/'test-results/m1/g10/season';output.mkdir(parents=True,exist_ok=True)
        for number in range(1,args.runs+1):(output/f'run-{number}.json').unlink(missing_ok=True)
        for number in range(1,args.runs+1):run(number,output,args.demo)
