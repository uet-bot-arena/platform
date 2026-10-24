"""D0/D1: installed 4-vCPU hub, 4,000 normal users, 100 SSE and 32 ten-second slots.

Run as root INSIDE UET-M1-HUB-20261024, after the owner's explicit readiness reply.
Uses the existing copied fixture at /root/g10b-fixture; never builds it or runs large_m1.
The five generators are separate processes outside arena.slice. No native bot runs.
"""
import argparse
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
import shutil
import sqlite3
import subprocess
import sys
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


def prepare():
    from arena.store import Store
    from arena_api.accounts import Accounts
    assert os.geteuid()==0
    assert FIXTURE.resolve()==Path('/root/g10b-fixture') and (FIXTURE/'meta.json').is_file()
    assert DATA.resolve()==Path('/var/lib/uet-arena/data') and not ORIGINAL.exists(),'Earlier lab data must be restored first'
    command('systemctl','stop',*UNITS)
    if DATA.exists():DATA.rename(ORIGINAL)
    shutil.copytree(FIXTURE/'data',DATA)
    (DATA/'.g10b-lab-data').write_text('Disposable copy of the explicit 1,000-team fixture\n')
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
    command('systemctl','daemon-reload')
    assert DATA.resolve()==Path('/var/lib/uet-arena/data')
    if ORIGINAL.exists():
        assert (DATA/'.g10b-lab-data').is_file(),'Refuse to remove an unmarked data directory'
        shutil.rmtree(DATA);ORIGINAL.rename(DATA)
    command('systemctl','start',*UNITS)


def run():
    import httpx
    from users_4000_workers import fake_github,user_worker,sse_worker,grader_worker,paths,event_digest
    assert os.geteuid()==0,'Run in the installed disposable distro as root'
    import tomllib
    read_workers=tomllib.loads(Path('/etc/uet-arena/config.toml').read_text()).get('hub',{}).get('read_workers',0)
    UNITS[:]=UNITS[:4]+['uet-arena-read@'+str(4183+i) for i in range(read_workers)]
    LAB.mkdir(parents=True,exist_ok=True,mode=0o700)
    ledger=LAB/'runs.json';runs=json.loads(ledger.read_text()) if ledger.exists() else []
    assert sum(r.get('valid',False) for r in runs)<3,'PLAN allows at most three valid large runs'
    meta=None;children=[];ctx=mp.get_context('spawn');ready=ctx.Queue();output=ctx.Queue()
    gh_commands=ctx.Queue();gh_results=ctx.Queue();start=ctx.Event();stop=ctx.Event();end=ctx.Value('d',0);fixture=ctx.Array('c',128)
    result={};http=None
    def spawn(target,*args):
        process=ctx.Process(target=target,args=args);process.start();children.append(process);return process
    try:
        meta=prepare();cid=meta['cid'];fixture.value=meta['first_fixture'].encode()
        github=spawn(fake_github,meta,ready,gh_commands,gh_results)
        label,github_origin=ready.get(timeout=30);assert label=='github'
        OVERRIDE.parent.mkdir(parents=True,exist_ok=True)
        OVERRIDE.write_text('[Service]\nEnvironment=ARENA_CONFIG='+str(LAB/'config.toml')+'\nEnvironment=ARENA_SECRETS='+str(LAB/'secrets.toml')+'\nEnvironment=ARENA_LAB_GITHUB='+github_origin+'\nEnvironment=PYTHONPATH='+str(ROOT/'tests/load_lab')+'\nExecStart=\nExecStart=/opt/uet-arena/venv/bin/python -m uvicorn users_4000:factory --factory --host 127.0.0.1 --port 4182 --no-proxy-headers --access-log\n')
        command('systemctl','daemon-reload');command('systemctl','start',*UNITS)
        command('systemctl','set-property','--runtime','arena.slice','CPUQuota=400%','MemoryMax=2G','MemorySwapMax=0')
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
        for i in range(32):
            response=http.post(ORIGIN+'/api/v1/admin/graders',json=dict(name='4000-user trace slot '+str(i)),headers=admin)
            assert response.status_code==201,response.status_code
            tokens.append(response.json()['token'])
        graders=spawn(grader_worker,ORIGIN,tokens,meta['runtime_id'],fixture,start,stop,end,ready,output,str(LAB/'synthetic-graders'))
        assert ready.get(timeout=60)==('graders',32)
        contract=paths((ROOT.parent/'docs/UI.md').read_text())
        users=[spawn(user_worker,i,meta['people'][i*2000:(i+1)*2000],ORIGIN,cid,contract,start,stop,end,ready,output) for i in range(2)]
        sse=spawn(sse_worker,ORIGIN,cid,fixture,meta['people'],start,stop,end,ready,output)
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
            if state['unique']==1000:break
            assert time.monotonic()<deadline,'Warm-up did not read all 1,000 repos'
            time.sleep(.2)
        group=Path('/sys/fs/cgroup'+subprocess.check_output(['systemctl','show','arena.slice','-p','ControlGroup','--value'],text=True).strip())
        assert (group/'cpu.max').read_text().strip()=='400000 100000'
        assert int((group/'memory.max').read_text())==2*1024**3 and int((group/'memory.swap.max').read_text())==0
        for process in children:
            assert 'arena.slice' not in Path('/proc',str(process.pid),'cgroup').read_text()
        def cpu():return int(dict(line.split() for line in (group/'cpu.stat').read_text().splitlines())['usage_usec'])
        def memory():
            current=int((group/'memory.current').read_text());stat=dict(line.split() for line in (group/'memory.stat').read_text().splitlines())
            return dict(at=time.monotonic(),without_file=current-int(stat['file']),current=current)
        assert http.post(ORIGIN+'/api/v1/admin/__lab/reset-metrics',headers=admin,json={}).status_code==200
        gh_commands.put('begin');assert gh_results.get(timeout=5)
        baseline_cpu=cpu();baseline_processes=slice_processes(group);generator_cpu={p.pid:proc_cpu(p.pid) for p in children}
        began=time.monotonic();end.value=began+120;samples=[memory()]
        run_index=len(runs);runs.append(dict(valid=True,status='started',began_unix=time.time()))
        ledger.write_text(json.dumps(runs,indent=2))
        start.set();print('BEGIN users_4000: 120 seconds',flush=True)
        while time.monotonic()<end.value:
            time.sleep(max(0,min(5,end.value-time.monotonic())));samples.append(memory())
        finished=time.monotonic();slice_cpu=(cpu()-baseline_cpu)/1_000_000
        processes_after=slice_processes(group);generator_delta={p.pid:proc_cpu(p.pid)-generator_cpu[p.pid] for p in children}
        stop.set();gh_commands.put('end');gh=gh_results.get(timeout=10)
        metrics=http.get(ORIGIN+'/api/v1/admin/__lab/metrics',headers=admin);assert metrics.status_code==200;metrics=metrics.json()
        reports={}
        for _ in range(4):
            report=output.get(timeout=60);reports[report['name']]=report
        gh_commands.put('stop')
        for process in children:process.join(20);assert process.exitcode==0,(process.pid,process.exitcode)
        process_cpu=Counter()
        for pid,(name,value) in processes_after.items():process_cpu[name]+=value-baseline_processes.get(pid,(name,0))[1]
        seconds=finished-began
        generators={name:dict(cpu_seconds=generator_delta[p.pid],percent_of_one_core=generator_delta[p.pid]/seconds*100)
            for name,p in [('github',github),('users-0',users[0]),('users-1',users[1]),('sse',sse),('graders',graders)]}
        statuses=Counter();latencies=[];logins=[];http_errors=[]
        for name in ('users-0','users-1'):
            report=reports[name];statuses.update(report['statuses']);latencies+=report['latencies'];logins+=report['login_latencies'];http_errors+=report['errors']
        http_errors+=[k+':'+str(n) for k,n in statuses.items() if k not in ('200','304','login:200')]
        event_errors=list(reports['graders']['errors']);completed=0;replay_checks=[]
        with sqlite3.connect(DATA/'arena.sqlite') as db:
            for record in reports['graders']['records']:
                raw=db.execute('SELECT data FROM replay_archives WHERE job=?',(record['job'],)).fetchone()
                if not raw or raw[0]!=record['gzip']:event_errors.append('gzip byte mismatch: '+record['job']);continue
                events=json.loads(gzip.decompress(raw[0]))['events']
                if len(events)!=record['count'] or event_digest(events)!=record['digest']:event_errors.append('event count/digest mismatch: '+record['job'])
                if began<=record['finished']<=finished:completed+=1
                replay_checks.append(dict(job=record['job'],expected_count=record['count'],stored_count=len(events),expected_digest=record['digest'],stored_digest=event_digest(events),
                    sent_gzip_sha256=hashlib.sha256(record['gzip']).hexdigest(),stored_gzip_sha256=hashlib.sha256(raw[0]).hexdigest(),bytes=len(raw[0]),in_phase=began<=record['finished']<=finished))
        result=dict(valid=all(v['percent_of_one_core']<=80 for v in generators.values()),duration_seconds=seconds,read_workers=read_workers,
            teams=1000,people=4000,warm_visible=warmed,slots=32,game_seconds=10,github_poll_seconds=60,
            http_p95_ms=percentile(latencies),http_statuses=dict(statuses),http_errors=http_errors,login_p95_ms=percentile(logins),
            slice_cpu_percent_of_4_vcpu=slice_cpu/seconds/4*100,process_cpu_seconds=dict(process_cpu),generator_cpu=generators,
            hub_cpu_without_argon_percent=max(0,process_cpu['hub']-metrics['hash_cpu'])/seconds*100,
            ram_peak_without_file=max(s['without_file'] for s in samples),memory_peak=int((group/'memory.peak').read_text()),memory_samples=samples,
            sse_states=reports['sse']['states'],sse_errors=reports['sse']['errors'],completed_games=completed,completed_games_per_second=completed/seconds,event_errors=event_errors,
            replay_checks=replay_checks,input_trace_sha256=hashlib.sha256((ROOT/'tests/load_lab/native-event-trace.json').read_bytes()).hexdigest(),
            github_polls_per_minute=gh['polls']/seconds*60,github_errors=gh['errors'],metrics=metrics,commits_per_second=metrics['commits']/seconds)
        result['failures']=evaluate(result);result['passed']=result['valid'] and not result['failures']
        runs[run_index]=result;ledger.write_text(json.dumps(runs,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in ('memory_samples','replay_checks')},indent=2),flush=True)
        return 0 if result['passed'] else 1
    finally:
        stop.set()
        if http:http.close()
        for process in children:
            if process.is_alive():process.terminate()
            process.join(5)
        if ORIGINAL.exists():restore()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args()
    raise SystemExit(run())
