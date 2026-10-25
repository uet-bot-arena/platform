"""D2 / M2-2: installed 4-vCPU hub, normal users, 100 SSE and ten-second slots.

Without --remote, run as root inside the installed disposable distro (D2).
With --remote SSH_ALIAS, run the generators on the PC and server steps on the VPS.
Uses the existing copied fixture at /root/g10b-fixture; never builds it or runs large_m1.
The five generators are separate processes outside arena.slice. No native bot runs.
"""
import argparse
import base64
from collections import Counter
from contextlib import asynccontextmanager
import gzip
import hashlib
import json
import math
import multiprocessing as mp
import os
import queue
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'tests/load_lab')]
DATA=Path('/var/lib/uet-arena/data')
ORIGINAL=Path('/var/lib/uet-arena/g10b-original-data')
LAB=Path('/var/lib/uet-arena/g10b-lab')
FIXTURE=Path('/root/g10b-fixture')
OVERRIDE=Path('/etc/systemd/system/uet-arena-hub.service.d/g10b-lab.conf')
NGINX_OVERRIDE=Path('/etc/systemd/system/uet-arena-nginx.service.d/g10b-lab.conf')
UNITS=['uet-arena-hub','uet-arena-publisher','uet-arena-mailer','uet-arena-nginx']
ORIGIN='http://127.0.0.1:4181'


def factory():
    from fastapi import Depends
    from arena_api.app import create_app
    from arena_api.dependencies import admin_principal
    from arena_api.github_client import GitHubClient
    from arena_api.settings import Settings
    from route_metrics import instrument
    app=create_app(Settings());original=app.router.lifespan_context
    metrics={};hash_cpu=[0.0]
    def transport(method,url,headers,body,limit):
        parsed=urlsplit(url)
        assert parsed.netloc in ('api.github.com','codeload.github.com')
        return GitHubClient.http(method,os.environ['ARENA_LAB_GITHUB']+parsed.path+('?' +parsed.query if parsed.query else ''),headers,body,limit)
    @asynccontextmanager
    async def lifespan(app):
        with patch('arena_api.github.GitHubClient',lambda key,limit:GitHubClient(key,limit,transport=transport)):
            async with original(app):
                meter,commits=instrument(app.state.store);metrics.update(meter=meter,commits=commits)
                verify=app.state.accounts.verify
                def measured(*args,**kwargs):
                    started=time.thread_time()
                    try:return verify(*args,**kwargs)
                    finally:hash_cpu[0]+=time.thread_time()-started
                app.state.accounts.verify=measured
                try:yield
                finally:meter.close()
    app.router.lifespan_context=lifespan
    @app.get('/api/v1/admin/__lab/metrics',include_in_schema=False)
    def read(auth=Depends(admin_principal)):
        meter=metrics['meter']
        return dict(hash_cpu=hash_cpu[0],commits=metrics['commits'][0],lock_max_without_gc_ms=meter.longest_ms,
            lock_top5=sorted(meter.sites.items(),key=lambda item:-item[1])[:5],gc_gen2_max_ms=meter.gc_gen2_max_ms,
            hash_parameters=dict(memory_kib=app.state.accounts.hasher.memory_cost,time_cost=app.state.accounts.hasher.time_cost,parallelism=app.state.accounts.hasher.parallelism))
    @app.post('/api/v1/admin/__lab/reset-metrics',include_in_schema=False)
    def reset(auth=Depends(admin_principal)):
        meter=metrics['meter'];meter.longest_ms=0;meter.sites.clear();meter.gc_gen2_max_ms=0
        metrics['commits'][0]=0;hash_cpu[0]=0
        return dict(ok=True)
    return app


def command(*args):return subprocess.run(args,check=True,stdout=subprocess.DEVNULL)


def percentile(values):
    return sorted(values)[max(0,math.ceil(.95*len(values))-1)]*1000 if values else None


def evaluate(result):
    failures=[]
    def require(condition,name):
        if not condition:failures.append(name)
    require(result['http_p95_ms'] is not None and result['http_p95_ms']<=300,'HTTP p95 <= 300 ms')
    require(not result['http_errors'],'All HTTP 200 (snapshot 304 accepted)')
    require(result['login_p95_ms'] is not None and result['login_p95_ms']<=1000,'Login p95 <= 1 s')
    require(result['slice_cpu_percent_of_4_vcpu']<=50,'Slice average CPU <= 50% of 4 vCPU')
    require(result['ram_peak_without_file']<=.6*2*1024**3,'RAM excluding file <= 60% of 2 GiB')
    require(result['sse_states']==100 and not result['sse_errors'],'100/100 SSE states, no errors/timeouts')
    require(result['completed_games_per_second']>=3,'Graders >= 3.0 games/s')
    require(not result['event_errors'],'Gzip byte/count/digest match, no event errors')
    require(result['github_polls_per_minute']>=950 and not result['github_errors'],'GitHub >= 950 repo reads/minute, no errors')
    return failures


def proc_cpu(pid):
    fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
    return (int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')


def slice_processes(group):
    result={}
    for file in group.rglob('cgroup.procs'):
        for pid in file.read_text().split():
            try:
                args=Path('/proc',pid,'cmdline').read_bytes().replace(b'\0',b' ').decode()
                if '--read-only' in args:
                    name='read-'+args.split('--port ',1)[1].split()[0]
                else:
                    name='hub' if 'uvicorn' in args else 'publisher' if 'publish' in args else 'mailer' if 'mailer' in args else 'nginx' if 'nginx' in args else args[:60]
                result[pid]=(name,proc_cpu(pid))
            except FileNotFoundError:pass
    return result


def installed_units():
    import tomllib
    count=tomllib.loads(Path('/etc/uet-arena/config.toml').read_text()).get('hub',{}).get('read_workers',0)
    UNITS[:]=UNITS[:4]+['uet-arena-read@'+str(4183+i) for i in range(count)]
    return count


def prepare(repos=1000):
    from arena.store import Store
    from arena_api.accounts import Accounts
    assert os.geteuid()==0
    assert FIXTURE.resolve()==Path('/root/g10b-fixture') and (FIXTURE/'meta.json').is_file()
    assert DATA.resolve()==Path('/var/lib/uet-arena/data') and not ORIGINAL.exists(),'Earlier lab data must be restored first'
    command('systemctl','stop',*UNITS)
    if DATA.exists():DATA.rename(ORIGINAL)
    DATA.mkdir()
    (DATA/'.g10b-lab-data').write_text('Disposable copy of the explicit 1,000-team fixture\n')
    shutil.copytree(FIXTURE/'data',DATA,dirs_exist_ok=True)
    LAB.mkdir(parents=True,exist_ok=True,mode=0o700)
    meta=json.loads((FIXTURE/'meta.json').read_text())
    store=Store(DATA,recover_jobs=False);accounts=Accounts(store,session_seconds=14*86400)
    people=[]
    try:
        assert store.db.execute("SELECT COUNT(*) FROM elo_rounds WHERE phase='ladder' AND status='completed'").fetchone()[0]==168
        assert store.db.execute("SELECT COUNT(*) FROM elo_games").fetchone()[0]==1000
        checking={r[0] for r in store.db.execute("SELECT DISTINCT team_id FROM github_checks WHERE state='checking'")}
        assert len(checking)==40
        with store.transaction():
            store.db.execute('UPDATE sessions SET expires_ms=?',(time.time_ns()//1_000_000+14*86400000,))
            for i,account in enumerate(accounts.rows("SELECT * FROM accounts WHERE role='student' ORDER BY username")):
                session=accounts.new_session(account)
                people.append(dict(index=i,username=account['username'],cookie='arena_web_session='+session['token'],team_id=account['team_id'],checking=account['team_id'] in checking,
                    ip=f'198.18.{i//250}.{i%250+1}'))
            organizer=accounts.one("SELECT * FROM accounts WHERE username='organizer'")
            session=accounts.new_session(organizer)
            meta['admin']={'Cookie':'arena_web_session='+session['token'],'X-CSRF-Token':session['csrf_token']}
        assert len(people)==4000 and sum(p['checking'] for p in people)==160
        meta['people']=people
        meta['runtime_id']=json.loads(store.db.execute('SELECT config FROM competitions WHERE id=?',(meta['cid'],)).fetchone()[0])['runtime_id']
        meta['first_fixture']=store.db.execute('SELECT id FROM elo_games ORDER BY ordinal LIMIT 1').fetchone()[0]
        if repos!=1000:
            selected=[r['id'] for r in meta['repositories'][:repos]]
            with store.transaction():
                store.db.execute('UPDATE github_repositories SET next_poll_ms=? WHERE repo_id NOT IN ('+
                    ','.join('?' for _ in selected)+')',(time.time_ns()//1_000_000+86400000,*selected))
    finally:store.db.close()
    command('openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(LAB/'fixture.pem'))
    (LAB/'secrets.toml').write_text('verification_hmac_key='+json.dumps(secrets.token_urlsafe(32))+'\ngithub_app_id="123456"\ngithub_app_private_key='+json.dumps(str(LAB/'fixture.pem'))+'\n')
    (LAB/'config.toml').write_text('[hub]\norigin="http://127.0.0.1:4181"\nport=4182\nbind="127.0.0.1"\ndata_dir="/var/lib/uet-arena/data"\nui_root="/opt/uet-arena/app/uet-bot-arena/dist"\ncookie_secure=false\nhash_workers=2\ntrusted_proxies=["127.0.0.1/32"]\n[github]\npoll_seconds=60\n')
    command('chown','-R','arena:arena',str(DATA),str(LAB))
    for file in LAB.iterdir():
        if file.is_file():file.chmod(0o600)
    os.sync()
    return meta


def restore():
    command('systemctl','stop',*UNITS)
    OVERRIDE.unlink(missing_ok=True)
    NGINX_OVERRIDE.unlink(missing_ok=True)
    for path in read_overrides():path.unlink(missing_ok=True)
    command('systemctl','daemon-reload')
    assert DATA.resolve()==Path('/var/lib/uet-arena/data')
    if ORIGINAL.exists():
        if DATA.exists():
            assert (DATA/'.g10b-lab-data').is_file(),'Refuse to remove an unmarked data directory'
            shutil.rmtree(DATA)
        ORIGINAL.rename(DATA)
    command('systemctl','start',*UNITS)


def read_overrides():
    return [Path('/etc/systemd/system')/(unit+'.service.d/g10b-lab.conf')
            for unit in UNITS if unit.startswith('uet-arena-read@')]


def start_server(github_origin):
    assert github_origin.startswith('http://127.0.0.1:')
    OVERRIDE.parent.mkdir(parents=True,exist_ok=True)
    OVERRIDE.write_text('[Service]\nEnvironment=ARENA_CONFIG='+str(LAB/'config.toml')+'\nEnvironment=ARENA_SECRETS='+str(LAB/'secrets.toml')+'\nEnvironment=ARENA_LAB_GITHUB='+github_origin+'\nEnvironment=PYTHONPATH='+str(ROOT/'tests/load_lab')+'\nExecStart=\nExecStart=/opt/uet-arena/venv/bin/python -m uvicorn users_4000:factory --factory --host 127.0.0.1 --port 4182 --no-proxy-headers --access-log\n')
    # Production's HTTPS origin differs from the loopback lab; readers need the same boundary.
    for path in read_overrides():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('[Service]\nEnvironment=ARENA_CONFIG='+str(LAB/'config.toml')+'\nEnvironment=ARENA_SECRETS='+str(LAB/'secrets.toml')+'\n')
    # Lab-only Host normalization lets independent forwards use different PC ports.
    config=Path('/etc/uet-arena/nginx.conf').read_text()
    routes=Path('/etc/uet-arena/nginx-read-routes.conf').read_text()
    config=config.replace('include /etc/uet-arena/nginx-read-routes.conf;',routes)
    config=config.replace('$http_host','127.0.0.1:4181')
    config=config.replace("log_format arena '$remote_addr - \"$request\" $status $body_bytes_sent';",
        "log_format arena '$msec $request_time $upstream_response_time';")
    config=config.replace('access_log syslog:server=unix:/dev/log,tag=uet_arena_nginx,nohostname arena;',
        'access_log /run/uet-arena-nginx/lab-timing.log arena;')
    assert '$request_time' in config
    (LAB/'nginx.conf').write_text(config)
    NGINX_OVERRIDE.parent.mkdir(parents=True,exist_ok=True)
    NGINX_OVERRIDE.write_text('[Service]\nExecStartPre=\nExecStartPre=/usr/sbin/nginx -t -c '+str(LAB/'nginx.conf')+'\nExecStart=\nExecStart=/usr/sbin/nginx -c '+str(LAB/'nginx.conf')+" -g 'daemon off;'\n")
    command('systemctl','daemon-reload');command('systemctl','start',*UNITS)
    command('systemctl','set-property','--runtime','arena.slice','CPUQuota=400%','MemoryMax=2G','MemorySwapMax=0')


def server_sample():
    group=Path('/sys/fs/cgroup'+subprocess.check_output(['systemctl','show','arena.slice','-p','ControlGroup','--value'],text=True).strip())
    assert (group/'cpu.max').read_text().strip()=='400000 100000'
    assert int((group/'memory.max').read_text())==2*1024**3 and int((group/'memory.swap.max').read_text())==0
    current=int((group/'memory.current').read_text());stat=dict(line.split() for line in (group/'memory.stat').read_text().splitlines())
    sshd={}
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():continue
        try:
            if (directory/'comm').read_text().strip().startswith('sshd'):sshd[directory.name]=proc_cpu(int(directory.name))
        except (FileNotFoundError,ProcessLookupError):pass
    return dict(usage_usec=int(dict(line.split() for line in (group/'cpu.stat').read_text().splitlines())['usage_usec']),
        memory=dict(at=time.monotonic(),without_file=current-int(stat['file']),current=current),
        memory_peak=int((group/'memory.peak').read_text()),processes=slice_processes(group),
        sshd=sshd,sshd_cpu_seconds=sum(sshd.values()))


def check_records(records,began,finished):
    from users_4000_workers import event_digest
    errors=[];completed=0;checks=[]
    with sqlite3.connect('file:'+str(DATA/'arena.sqlite')+'?mode=ro',uri=True) as db:
        for record in records:
            payload=record['gzip']
            if isinstance(payload,str):payload=base64.b64decode(payload,validate=True)
            raw=db.execute('SELECT data FROM replay_archives WHERE job=?',(record['job'],)).fetchone()
            if not raw or raw[0]!=payload:errors.append('gzip byte mismatch: '+record['job']);continue
            events=json.loads(gzip.decompress(raw[0]))['events']
            digest=event_digest(events)
            if len(events)!=record['count'] or digest!=record['digest']:errors.append('event count/digest mismatch: '+record['job'])
            in_phase=began<=record['finished']<=finished
            completed+=int(in_phase)
            checks.append(dict(job=record['job'],expected_count=record['count'],stored_count=len(events),expected_digest=record['digest'],stored_digest=digest,
                sent_gzip_sha256=hashlib.sha256(payload).hexdigest(),stored_gzip_sha256=hashlib.sha256(raw[0]).hexdigest(),bytes=len(raw[0]),in_phase=in_phase))
    return dict(errors=errors,completed=completed,checks=checks)


class Remote:
    def __init__(self,alias):
        import re
        assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',alias),'Use an SSH config alias'
        self.alias=alias;self.tunnels={};self.tunnel_logs={}
        self.private=tempfile.TemporaryDirectory(prefix='uet-m2-load-')
        Path(self.private.name).chmod(0o700)

    def call(self,action,*args,payload=None):
        remote=shlex.join(['/opt/uet-arena/venv/bin/python',
            '/opt/uet-arena/app/arena-backend/tests/load_lab/users_4000.py','--server',action,*map(str,args)])
        process=subprocess.run(['ssh','-o','BatchMode=yes',self.alias,remote],
            input=json.dumps(payload) if payload is not None else '',capture_output=True,text=True,timeout=180)
        if process.returncode:
            # Stderr can contain fixture data; keep it out of JSON/public logs.
            error=Path(self.private.name)/'server-error.log';error.write_text(process.stderr);error.chmod(0o600)
            raise RuntimeError('Remote step failed: '+action+'; private stderr at '+str(error))
        return json.loads(process.stdout)

    def prepare(self,repos):
        settings=self.call('prepare','--repos',repos)
        destination=Path(self.private.name)/'meta.json'
        command('scp','-q',self.alias+':'+str(LAB/'meta.json'),str(destination))
        destination.chmod(0o600)
        return json.loads(destination.read_text()),settings['read_workers']

    def connect(self,github_origin):
        port=urlsplit(github_origin).port
        forwards={'graders':['-L','127.0.0.1:4181:127.0.0.1:4181'],
            'users-0':['-L','127.0.0.1:14181:127.0.0.1:4181'],
            'users-1':['-L','127.0.0.1:14182:127.0.0.1:4181'],
            'sse':['-L','127.0.0.1:14183:127.0.0.1:4181'],
            'github':['-R',f'127.0.0.1:14190:127.0.0.1:{port}']}
        for name,forward in forwards.items():
            log=(Path(self.private.name)/(name+'.log')).open('w+b');self.tunnel_logs[name]=log
            self.tunnels[name]=subprocess.Popen(['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
                '-o','ControlMaster=no','-o','ControlPath=none',*forward,self.alias],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log)
        for port in (4181,14181,14182,14183):
            until=time.monotonic()+15
            while time.monotonic()<until:
                assert all(p.poll() is None for p in self.tunnels.values()),'SSH tunnel exited'
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.1)
            else:raise TimeoutError('SSH forward did not open')

    def cpu(self):return {name:proc_cpu(p.pid) for name,p in self.tunnels.items()}

    def close(self):
        for name,process in self.tunnels.items():
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log=self.tunnel_logs[name];log.seek(0);diagnostic=log.read()
            if diagnostic:
                output=ROOT/'test-results/m2/verification-load';output.mkdir(parents=True,exist_ok=True)
                (output/(name+'-ssh-'+str(time.time_ns())+'.log')).write_bytes(diagnostic)
            log.close()
        self.private.cleanup()


def server(args):
    assert os.geteuid()==0
    read_workers=installed_units()
    if args.server=='prepare':
        meta=prepare(args.repos)
        path=LAB/'meta.json';path.touch(mode=0o600);path.chmod(0o600);path.write_text(json.dumps(meta))
        answer=dict(ok=True,read_workers=read_workers)
    elif args.server=='start':start_server(args.github_origin);answer=server_sample()
    elif args.server=='sample':answer=server_sample()
    elif args.server=='check':
        body=json.load(sys.stdin);answer=check_records(body['records'],body['began'],body['finished'])
        rows=[line.split() for line in Path('/run/uet-arena-nginx/lab-timing.log').read_text().splitlines()]
        times=[float(r[1]) for r in rows if body['began_unix']<=float(r[0])<=body['finished_unix']]
        answer.update(vps_http_p95_ms=percentile(times),vps_request_count=len(times))
    else:restore();answer=dict(ok=True)
    print(json.dumps(answer),flush=True)


def run(args):
    import httpx
    from users_4000_workers import fake_github,user_worker,sse_worker,grader_worker,paths,event_digest
    remote=bool(args.remote)
    if remote:
        ledger=ROOT/'test-results/m2/verification-load/runs.json'
        ledger.parent.mkdir(parents=True,exist_ok=True)
        read_workers=None
    else:
        assert os.geteuid()==0,'Run in the installed disposable distro as root'
        read_workers=installed_units()
        LAB.mkdir(parents=True,exist_ok=True,mode=0o700)
        ledger=LAB/'runs.json'
    runs=json.loads(ledger.read_text()) if ledger.exists() else []
    if remote:
        assert args.people==4000 and args.slots==32 and args.repos==1000,'V1 keeps the full 4,000-user scenario'
        assert len(runs)<3,'V1 allows at most three attempts'
        assert not any(r.get('valid') and r.get('people')==args.people and r.get('slots')==args.slots
            and r.get('repos',1000)==args.repos for r in runs),'This level already has a valid measurement'
    else:assert sum(r.get('valid',False) for r in runs)<3,'PLAN allows at most three valid large runs'
    if remote:remote=Remote(args.remote)
    meta=None;children=[];ctx=mp.get_context('spawn');ready=ctx.Queue();output=ctx.Queue()
    gh_commands=ctx.Queue();gh_results=ctx.Queue();start=ctx.Event();stop=ctx.Event();end=ctx.Value('d',0);fixture=ctx.Array('c',128)
    result={};http=None;prepare_attempted=False;run_index=None
    if remote:
        run_index=len(runs)
        runs.append(dict(valid=False,status='preparing',remote=args.remote,people=args.people,slots=args.slots,repos=args.repos,began_unix=time.time()))
        ledger.write_text(json.dumps(runs,indent=2))
    def spawn(target,*args):
        process=ctx.Process(target=target,args=args);process.start();children.append(process);return process
    try:
        if remote:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                probe.bind(('127.0.0.1',4181))
        prepare_attempted=True
        if remote:meta,read_workers=remote.prepare(args.repos)
        else:meta=prepare(args.repos)
        cid=meta['cid'];fixture.value=meta['first_fixture'].encode()
        github=spawn(fake_github,meta,ready,gh_commands,gh_results)
        label,github_origin=ready.get(timeout=30);assert label=='github'
        if remote:
            remote.connect(github_origin);remote.call('start','--github-origin','http://127.0.0.1:14190')
        else:start_server(github_origin)
        http=httpx.Client(timeout=30);admin=meta['admin']
        deadline=time.monotonic()+60
        while True:
            try:
                response=http.get(ORIGIN+'/api/v1/health/ready')
                if response.status_code==200:break
            except httpx.HTTPError:pass
            assert time.monotonic()<deadline,'Installed hub did not become ready'
            time.sleep(.2)
        while True:
            response=http.get(ORIGIN+'/snapshots/competitions.json')
            if response.status_code==200 and any(c['id']==cid for c in response.json()['items']):break
            assert time.monotonic()<deadline,'Publisher did not create the copied fixture snapshots'
            time.sleep(.2)
        tokens=[]
        for i in range(args.slots):
            response=http.post(ORIGIN+'/api/v1/admin/graders',json=dict(name='4000-user trace slot '+str(i)),headers=admin)
            assert response.status_code==201,response.status_code
            tokens.append(response.json()['token'])
        state_dir=Path(remote.private.name)/'synthetic-graders' if remote else LAB/'synthetic-graders'
        graders=spawn(grader_worker,ORIGIN,tokens,meta['runtime_id'],fixture,start,stop,end,ready,output,str(state_dir))
        assert ready.get(timeout=60)==('graders',args.slots)
        contract=paths((ROOT/'tests/fixtures/ui-routes.md').read_text())
        count=(args.people+1)//2
        people=meta['people'][:args.people]
        # Keep the single SSH server process below its 1,024-FD limit; D2 stays at 256 pools.
        pools=128 if remote else 256
        users=[spawn(user_worker,i,people[i*count:(i+1)*count],('http://127.0.0.1:'+str(14181+i)) if remote else ORIGIN,cid,contract,start,stop,end,ready,output,4000/args.people,pools) for i in range(2)]
        sse=spawn(sse_worker,'http://127.0.0.1:14183' if remote else ORIGIN,cid,fixture,meta['people'],start,stop,end,ready,output)
        warmed={}
        for _ in range(3):
            deadline=time.monotonic()+120
            while True:
                failed=[p.pid for p in children if p.exitcode not in (None,0)]
                assert not failed,('Generator failed during warm-up',failed)
                try:
                    name,count=ready.get(timeout=min(1,max(.01,deadline-time.monotonic())));break
                except queue.Empty:
                    assert time.monotonic()<deadline,('Warm-up timed out',warmed)
            warmed[name]=count
            print('WARM ready:',name,count,flush=True)
        assert warmed.get('sse')==100
        deadline=time.monotonic()+120
        while True:
            gh_commands.put('warm');state=gh_results.get(timeout=5)
            assert not state['errors'],state['errors']
            if state['unique']==args.repos:break
            assert time.monotonic()<deadline,'Warm-up did not read all configured repos'
            time.sleep(.2)
        sample=remote.call if remote else None
        def snapshot():return sample('sample') if remote else server_sample()
        baseline=snapshot()
        for process in children:
            assert 'arena.slice' not in Path('/proc',str(process.pid),'cgroup').read_text()
        assert http.post(ORIGIN+'/api/v1/admin/__lab/reset-metrics',headers=admin,json={}).status_code==200
        gh_commands.put('begin');assert gh_results.get(timeout=5)
        baseline=snapshot();generator_cpu={p.pid:proc_cpu(p.pid) for p in children}
        ssh_cpu=remote.cpu() if remote else {}
        began_unix=time.time();began=time.monotonic();end.value=began+120;samples=[baseline['memory']]
        if run_index is None:
            run_index=len(runs);runs.append(dict(valid=True,status='started',began_unix=time.time()))
        else:runs[run_index]['status']='started'
        ledger.write_text(json.dumps(runs,indent=2))
        start.set();print('BEGIN users_4000: 120 seconds',flush=True)
        while time.monotonic()<end.value:
            time.sleep(max(0,min(5,end.value-time.monotonic())));after=snapshot();samples.append(after['memory'])
        finished_unix=time.time();finished=time.monotonic();slice_cpu=(after['usage_usec']-baseline['usage_usec'])/1_000_000
        generator_delta={p.pid:proc_cpu(p.pid)-generator_cpu[p.pid] for p in children}
        ssh_delta={name:value-ssh_cpu[name] for name,value in remote.cpu().items()} if remote else {}
        stop.set();gh_commands.put('end');gh=gh_results.get(timeout=10)
        metrics=http.get(ORIGIN+'/api/v1/admin/__lab/metrics',headers=admin);assert metrics.status_code==200;metrics=metrics.json()
        reports={}
        for _ in range(4):
            report=output.get(timeout=60);reports[report['name']]=report
        gh_commands.put('stop')
        for process in children:process.join(20);assert process.exitcode==0,(process.pid,process.exitcode)
        process_cpu=Counter()
        for pid,(name,value) in after['processes'].items():process_cpu[name]+=value-baseline['processes'].get(pid,(name,0))[1]
        seconds=finished-began
        generators={name:dict(cpu_seconds=generator_delta[p.pid],percent_of_one_core=generator_delta[p.pid]/seconds*100)
            for name,p in [('github',github),('users-0',users[0]),('users-1',users[1]),('sse',sse),('graders',graders)]}
        statuses=Counter();latencies=[];logins=[];http_errors=[]
        for name in ('users-0','users-1'):
            report=reports[name];statuses.update(report['statuses']);latencies+=report['latencies'];logins+=report['login_latencies'];http_errors+=report['errors']
        http_errors+=[k+':'+str(n) for k,n in statuses.items() if k not in ('200','304','login:200')]
        records=reports['graders']['records']
        if remote:
            records=[dict(r,gzip=base64.b64encode(r['gzip']).decode('ascii')) for r in records]
            checked=remote.call('check',payload=dict(records=records,began=began,finished=finished,began_unix=began_unix,finished_unix=finished_unix))
        else:checked=check_records(records,began,finished)
        event_errors=list(reports['graders']['errors'])+checked['errors'];completed=checked['completed'];replay_checks=checked['checks']
        result=dict(valid=all(v['percent_of_one_core']<=80 for v in generators.values()) and all(v/seconds*100<=80 for v in ssh_delta.values()),duration_seconds=seconds,read_workers=read_workers,
            teams=1000,people=args.people,warm_visible=warmed,slots=args.slots,repos=args.repos,user_pools_per_process=pools,game_seconds=10,github_poll_seconds=60,
            http_p95_ms=percentile(latencies),http_statuses=dict(statuses),http_errors=http_errors,login_p95_ms=percentile(logins),
            slice_cpu_percent_of_4_vcpu=slice_cpu/seconds/4*100,process_cpu_seconds=dict(process_cpu),generator_cpu=generators,
            hub_cpu_without_argon_percent=max(0,process_cpu['hub']-metrics['hash_cpu'])/seconds*100,
            ram_peak_without_file=max(s['without_file'] for s in samples),memory_peak=after['memory_peak'],memory_samples=samples,
            sse_states=reports['sse']['states'],sse_errors=reports['sse']['errors'],completed_games=completed,completed_games_per_second=completed/seconds,event_errors=event_errors,
            replay_checks=replay_checks,input_trace_sha256=hashlib.sha256((ROOT/'tests/load_lab/native-event-trace.json').read_bytes()).hexdigest(),
            github_polls_per_minute=gh['polls']/seconds*60,github_errors=gh['errors'],metrics=metrics,commits_per_second=metrics['commits']/seconds)
        diagnostic=args.slots!=32 or args.repos!=1000
        if remote:
            result.update(remote=args.remote,ssh_client_cpu_percent={name:value/seconds*100 for name,value in ssh_delta.items()},
                vps_http_p95_ms=checked['vps_http_p95_ms'],vps_request_count=checked['vps_request_count'],
                sshd_cpu_percent_of_one_core=sum(max(0,value-baseline['sshd'].get(pid,0)) for pid,value in after['sshd'].items())/seconds*100)
        result['diagnostic']=diagnostic
        result['failures']=[] if diagnostic else evaluate(result)
        result['passed']=None if diagnostic else result['valid'] and not result['failures']
        runs[run_index]=result;ledger.write_text(json.dumps(runs,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in ('memory_samples','replay_checks')},indent=2),flush=True)
        return 0 if (result['passed'] or diagnostic and result['valid']) else 1
    except BaseException as exc:
        if remote and run_index is not None and runs[run_index].get('status') in ('started','preparing'):
            runs[run_index].update(valid=False,status='error',error=type(exc).__name__)
            ledger.write_text(json.dumps(runs,indent=2))
        raise
    finally:
        stop.set()
        if http:http.close()
        for process in children:
            if process.is_alive():process.terminate()
            process.join(5)
        try:
            if remote and prepare_attempted:remote.call('restore')
            elif ORIGINAL.exists():restore()
        finally:
            if remote:remote.close()


def self_check():
    """Offline checks inside the two-file scope of M2-2; no service/network calls."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from users_4000_workers import event_digest
    with tempfile.TemporaryDirectory() as temp:
        directory=Path(temp);data=directory/'data';data.mkdir()
        events=[dict(type='state',data=dict(ply=1))]
        payload=gzip.compress(json.dumps(dict(events=events)).encode())
        record=dict(job='test-job',gzip=payload,count=1,digest=event_digest(events),finished=15)
        with sqlite3.connect(data/'arena.sqlite') as db:
            db.execute('CREATE TABLE replay_archives(job TEXT PRIMARY KEY,data BLOB)')
            db.execute('INSERT INTO replay_archives VALUES(?,?)',('test-job',payload))
        with patch(__name__+'.DATA',data):
            native=check_records([record],10,20)
            wire=check_records([dict(record,gzip=base64.b64encode(payload).decode())],10,20)
            assert native==wire and not wire['errors'] and wire['completed']==1
            assert check_records([dict(record,gzip=b'wrong')],10,20)['errors']
            assert check_records([dict(record,digest='wrong')],10,20)['errors']
        fake_data=MagicMock();fake_data.resolve.return_value=DATA;fake_data.exists.return_value=True
        fake_data.__truediv__.return_value.is_file.return_value=False
        with patch(__name__+'.DATA',fake_data),patch(__name__+'.ORIGINAL') as original,\
                patch(__name__+'.OVERRIDE'),patch(__name__+'.command'),patch(__name__+'.shutil.rmtree') as delete:
            original.exists.return_value=True
            try:restore()
            except AssertionError:pass
            else:raise AssertionError('Restore deleted an unmarked directory')
            delete.assert_not_called()
        args=SimpleNamespace(remote='offline-test',people=4000,slots=32,repos=1000)
        with patch(__name__+'.ROOT',directory),patch(__name__+'.Remote') as remote:
            remote.return_value.prepare.side_effect=RuntimeError('injected prepare failure')
            try:run(args)
            except RuntimeError:pass
            else:raise AssertionError('Injected failure was swallowed')
            remote.return_value.call.assert_called_once_with('restore')
            remote.return_value.close.assert_called_once()
        with patch(__name__+'.subprocess.Popen') as popen,patch(__name__+'.socket.create_connection'):
            popen.return_value.poll.return_value=None
            remote=Remote('offline-test')
            remote.connect('http://127.0.0.1:4190')
            calls=[call.args[0] for call in popen.call_args_list]
            assert len(calls)==5 and sum('-L' in c for c in calls)==4 and sum('-R' in c for c in calls)==1
            assert len({c[c.index('-L')+1] for c in calls if '-L' in c})==4
            remote.close()
        try:Remote('-oProxyCommand=bad')
        except AssertionError:pass
        else:raise AssertionError('SSH option accepted as an alias')
    print('PASS offline: byte/base64 replay verification, tamper rejection, restore guard, finally restore, alias validation')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote',help='SSH config alias; generators stay on this PC')
    parser.add_argument('--people',type=int,choices=(200,1000,2000,4000),default=4000)
    parser.add_argument('--slots',type=int,choices=(8,32),default=32)
    parser.add_argument('--repos',type=int,choices=(200,1000),default=1000)
    parser.add_argument('--server',choices=('prepare','start','sample','check','restore'))
    parser.add_argument('--github-origin')
    parser.add_argument('--self-test',action='store_true',help='Offline safety checks; never starts a load run')
    args=parser.parse_args()
    if args.self_test:self_check()
    elif args.server:server(args)
    else:raise SystemExit(run(args))
