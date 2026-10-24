"""G10 mature 1,000-team capacity: history, GitHub HTTP polling, 100 SSE, live events.

Requires season_m1.py's native-job-template.json. History rows are synthetic
copies of that real 72-sample report, not additional native timing samples.
Run after the functional/unit tests have stopped so capacity has no competing lab.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace


import httpx

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from arena.grader import Grader, GraderRunner, HubClient
from arena import engine
from arena.store import Store
from arena.service import Service
from arena_api.accounts import Accounts
from arena_api.competitions import Competitions
from arena_api.settings import AccountRules
from arena_api.teams import Teams
from login_capacity import seed,percentile,PASSWORD,PAGE
from season_m1 import port,disk,wait
from season_github import GitHubFixture
from elo_native import source_fixture


class RecordingHubClient(HubClient):
    def __init__(self,*args):
        super().__init__(*args)
        self.payloads={};self.calls=Counter();self.guard=threading.Lock()

    def call(self,method,path,*args,**kwargs):
        with self.guard:self.calls[path.rsplit('/',1)[-1]]+=1
        answer=super().call(method,path,*args,**kwargs)
        if path.endswith('/result-replay'):
            with self.guard:self.payloads[path]=kwargs['data']
        return answer


def event_digest(events):
    values=[dict(type=e['type'],data=e['data']) for e in events]
    return hashlib.sha256(json.dumps(values,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def prepare(data,github,template):
    template=json.loads(json.dumps(template))
    template['report']['artifacts'][0]['artifact_sha256']='4'*64
    placeholder=data/'grader-artifacts/windows'/('4'*64)
    placeholder.parent.mkdir(parents=True)
    placeholder.write_bytes(b'capacity-placeholder-never-downloaded-or-executed')
    cookies=seed(data,cookie_teams=1000)
    raw,_=source_fixture(0);base=github.add('capacity-0','ott2026-000000',raw,int(time.time()*1000))
    github.repositories.clear()
    store=Store(data,recover_jobs=False)
    try:
        accounts=Accounts(store);service=Service(store,start_worker=False);comp=Competitions(store,service,accounts)
        comp.teams=Teams(accounts,AccountRules())
        teams=accounts.rows('SELECT id,repo_code FROM teams ORDER BY id')
        now=int(time.time()*1000);qualified={}
        with store.transaction():
            for i,team in enumerate(teams):
                repo=dict(base,id=i+100,owner='capacity-'+str(i),name='ott2026-'+team['repo_code'])
                github.repositories[repo['owner'],repo['name']]=repo
                store.db.execute('INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms,etag) VALUES(?,?,?,?,?,?,?)',
                    (team['id'],repo['id'],repo['owner'],repo['name'],repo['id'],now,repo['sha']))
                source=store.create('submission',team['id'],dict(version='v1.0.0',runtime='cpp17',protocol_version=2,
                    source_path='/synthetic-capacity-only',source=template['report']['artifacts'][0]['source'],cache_pruned=True))
                for version in range(20):
                    job=dict(template,id=uuid.uuid4().hex,team_id=team['id'],submission_ids=[source['id']],created_unix_ms=now-20000+version)
                    for key in ('competition_id','lease_id','grader_id','fencing','worker','runtime','lease_until_ms'):job.pop(key,None)
                    store.db.execute('INSERT INTO documents VALUES(?,?,?,?)',(job['id'],'job',team['id'],json.dumps(job)))
                    sha=repo['sha'] if version==19 else hashlib.sha1((team['id']+str(version)).encode()).hexdigest()
                    store.db.execute("INSERT INTO github_commits(team_id,sha,received_ms,pushed_ms,submission_id,bot_hash,state) VALUES(?,?,?,?,?,?,'pass')",
                        (team['id'],sha,now-20000+version,now-20000+version,source['id'],source['source']['zip_sha256']))
                    store.db.execute("INSERT INTO github_checks(team_id,sha,purpose,job_id,state,status_sent,finished_ms) VALUES(?,?,'practice',?,'pass',1,?)",(team['id'],sha,job['id'],now-20000+version))
                    store.db.execute('INSERT INTO github_observations(team_id,sha,observed_ms,pushed_ms) VALUES(?,?,?,?)',(team['id'],sha,now-20000+version,now-20000+version))
                qualified[team['id']]=dict(submission_id=source['id'],benchmark_id=job['id'],github_sha=repo['sha'],bot_hash=source['source']['zip_sha256'])
        actor=accounts.one("SELECT id FROM accounts WHERE username='organizer'")['id']
        runtime_id=template['report']['runtime_id']
        item=comp.create(actor,dict(title='Synthetic 1000-entry Elo capacity',format='elo',max_teams=1000,match_ms=10000,runtime_id=runtime_id,submission_deadline=now-1000),'large-create')
        cid=item['id'];comp.transition(actor,cid,'open','large-open')
        entries={}
        with store.transaction():
            for team in teams:
                chosen=qualified[team['id']]
                entry=comp.qualification(item['config'],team['id'],chosen['submission_id'],chosen['benchmark_id'])
                entries[team['id']]=dict(entry,github_sha=chosen['github_sha'],bot_hash=chosen['bot_hash'])
                store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(cid,team['id'],chosen['submission_id'],chosen['benchmark_id'],json.dumps(entry),now))
                store.db.execute("INSERT INTO github_deadlines(competition_id,team_id,source,candidates,state,selected_sha,pushed_ms) VALUES(?,?,'synthetic-capacity','[]','locked',?,?)",(cid,team['id'],chosen['github_sha'],now-1000))
        comp.lock(actor,cid,'large-lock');comp.transition(actor,cid,'running','large-start')
        comp.elo.book.seed_official(cid,entries,now)
        comp.elo.book.start(cid,'general',1,now,entries)
        comp.tick()
        assert len(comp.view(cid)['entries'])==1000
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:store.db.close()
    # Reclaim only this fixture's clean file cache, so its subsequent reads are
    # charged to the measured cgroup rather than the offline seed process.
    for path in data.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:os.posix_fadvise(stream.fileno(),0,0,os.POSIX_FADV_DONTNEED)
    return cookies,cid


def main():
    output=ROOT/'test-results/m1/g10/large';output.mkdir(parents=True,exist_ok=True)
    template=json.loads((ROOT/'test-results/m1/g10/season/native-job-template.json').read_text())
    assert len(template['report']['samples'])==72
    report=dict(teams=1000,competition_entries=1000,competition_format='elo',people=4000,detailed_checks=20000,github_commits=20000,sse=100,levels=[],samples=[],access_log=os.environ.get('ARENA_LAB_ACCESS_LOG')=='1',sql_profile_enabled=bool(os.environ.get('ARENA_LAB_SQL_PROFILE')),
        assumptions='Synthetic historical copies of a native report with fixture identities; placeholder binary is never downloaded/executed; fake GitHub HTTP, real production poll/client; no upstream TLS latency; recorded native event/report replay through real fenced completion; no additional native timing claims',
        expected_peak=dict(page_views_per_second=24,gets_per_view=7,logins_per_second=1,sse=100,github_repos=1000,poll_seconds=45))
    with tempfile.TemporaryDirectory(prefix='g10-large-') as temporary:
        data=Path(temporary);github=GitHubFixture();hub=emitter=monitoring=cg=None;stop=threading.Event();sse_stop=threading.Event();viewers=[];emitters=[];sent={};emission_start=threading.Event();flush_modes=Counter();cutoff=[None]
        active=set();chunks=Counter();opens=Counter();states=Counter();stream_errors=[];event_errors=[];stream_lock=threading.Lock()
        local=threading.local();load_clients=[];client_lock=threading.Lock()
        def load_http():
            if not hasattr(local,'http'):
                local.http=httpx.Client(timeout=120,verify=False,limits=httpx.Limits(max_connections=1,max_keepalive_connections=1))
                with client_lock:load_clients.append(local.http)
            return local.http
        http=httpx.Client(timeout=120,verify=False);log=(output/'stack.log').open('w')
        try:
            cookies,cid=prepare(data,github,template);hub_port=port();origin=f'http://127.0.0.1:{hub_port}'
            key=data/'fixture.pem';subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);key.chmod(0o600)
            private=data/'lab-secrets.toml';private.write_text('\n'.join(k+'='+json.dumps(v) for k,v in dict(verification_hmac_key=secrets.token_urlsafe(32),github_app_id='123456',github_app_private_key=str(key)).items()));private.chmod(0o600)
            clock=data/'clock.json';clock.write_text(json.dumps(int(time.time()*1000)))
            # Leave scheduler/request overhead inside the one-minute workload.
            config=data/'lab.toml';config.write_text('[github]\npoll_seconds=45\n')
            env=dict(os.environ,ARENA_CONFIG=str(config),ARENA_SECRETS=str(private),ARENA_API_DATA=str(data),ARENA_PORT=str(hub_port),ARENA_API_ORIGIN=origin,ARENA_HASH_WORKERS='1',ARENA_LAB_CLOCK=str(clock),ARENA_LAB_GITHUB=github.origin)
            system_scope=os.environ.get('ARENA_LAB_SYSTEM_SCOPE')=='1'
            if system_scope:assert os.geteuid()==0,'System-scope lab requires root'
            report['scope_manager']='system' if system_scope else 'user'
            hub=subprocess.Popen(['systemd-run',*([] if system_scope else ['--user']),'--scope','--quiet','-p','CPUQuota=200%','-p','MemoryMax=2G','-p','MemorySwapMax=0',sys.executable,str(Path(__file__).with_name('season_m1.py')),'--stack'],env=env,cwd=ROOT,stdout=log,stderr=log)
            def ready():
                nonlocal cg
                try:
                    candidate=Path('/sys/fs/cgroup'+Path(f'/proc/{hub.pid}/cgroup').read_text().strip().split('::',1)[1])
                    if (candidate/'cpu.max').read_text().startswith('200000 '):
                        cg=candidate
                        report['startup_memory_events']=(cg/'memory.events').read_text()
                        report['startup_limits']=dict(cpu_max=(cg/'cpu.max').read_text().strip(),memory_max=int((cg/'memory.max').read_text()),swap_max=int((cg/'memory.swap.max').read_text()))
                        report['startup_ram_peak_bytes']=int((cg/'memory.peak').read_text())
                    return http.get(origin+'/api/v1/health/ready',timeout=1).status_code==200
                except (httpx.HTTPError,OSError):return False
            wait(ready,'large stack',60)
            cg=Path('/sys/fs/cgroup'+Path(f'/proc/{hub.pid}/cgroup').read_text().strip().split('::',1)[1])
            report['cgroup']=dict(cpu_max=(cg/'cpu.max').read_text().strip(),memory_max=int((cg/'memory.max').read_text()),swap_max=int((cg/'memory.swap.max').read_text()))
            assert report['cgroup']==dict(cpu_max='200000 100000',memory_max=2*1024**3,swap_max=0)
            def post(path,body=None,headers=None):
                response=(load_http() if path.startswith('/grader/') else http).post(origin+'/api/v1'+path,json=body or {},headers=headers or {});assert response.status_code in (200,201),(path,response.status_code)
                return response.json()
            session=post('/auth/login',dict(username='organizer',password=PASSWORD));admin={'Cookie':'; '.join(k+'='+v for k,v in http.cookies.items()),'X-CSRF-Token':session['csrf_token']}
            from arena.service import EVENT_TYPES
            trace=json.loads(Path(__file__).with_name('native-event-trace.json').read_text())
            trace=[e for e in trace if e['type'] in EVENT_TYPES]
            first=trace[0]['server_unix_ms'];span=(trace[-1]['server_unix_ms']-first)/1000
            assert 0<span<30 and all(e['server_unix_ms']>=first for e in trace)
            report['grader_event_trace']=dict(slots=32,events_per_game=len(trace),native_span_seconds=span,flush_seconds=.15,sha256=hashlib.sha256(Path(__file__).with_name('native-event-trace.json').read_bytes()).hexdigest(),
                boundary='Real production GraderRunner/HubClient replay recorded native events on 32 slots; fenced heartbeat/completion/re-lease; no bot/backend invoked; native execution measured separately')
            report['grader_event_trace']['selection']=json.loads(Path(__file__).with_name('native-trace-selection.json').read_text())
            slot_headers=[];leases=[]
            for number in range(32):
                token=post('/admin/graders',dict(name=f'Native trace replay slot {number}'),dict(admin,**{'Idempotency-Key':uuid.uuid4().hex}))
                grader={'Authorization':'Bearer '+token['token'],'Cookie':''}
                post('/grader/hello',dict(platform='windows',runtime_id=template['report']['runtime_id'],backend='windows',slots=1,version='native-trace-capacity'),grader)
                lease=None
                while lease is None:lease=post('/grader/lease',dict(wait_seconds=1),grader)['lease']
                assert lease['job']['official']['competition_id']==cid
                slot_headers.append(grader);leases.append(lease)
            fixture=leases[0]['job']['official']['fixture_id']
            native_report=json.loads(Path(__file__).with_name('native-match-report.json').read_text())
            assert native_report['profile']==leases[0]['job']['profile'],'Use the 10-second native season report, not a legacy clock'
            from arena.verify import verify
            verify(leases[0]['job'],'completed',native_report)
            completed_gzip={};grader_clients=[];lease_waits=Counter();games_by_slot=Counter()
            def emit(number):
                nonlocal fixture
                lease=leases[number];runner=beat=None;beats=[]
                # Only the transport/Runner hooks run here; no bot/backend is invoked.
                identity=dict(platform='windows',backend='windows',runtime_id=template['report']['runtime_id'])
                grader=Grader(origin,slot_headers[number]['Authorization'][7:],backend='windows',
                    state_dir=data/'synthetic-graders'/str(number),backend_factory=lambda *_:SimpleNamespace(identity=lambda:identity))
                grader.client=RecordingHubClient(origin,slot_headers[number]['Authorization'][7:])
                grader.settings=dict(heartbeat_seconds=lease['heartbeat_seconds'])
                grader_clients.append(grader.client)
                try:
                    first_game=True
                    while True:
                        runner=GraderRunner(grader,None,lease,threading.Event())
                        beat=threading.Thread(target=grader.heartbeat_loop,args=(runner,));beat.start();beats.append(beat)
                        if first_game:
                            while not emission_start.wait(.1):
                                if stop.is_set():return
                            first_game=False
                        if time.monotonic()>=cutoff[0] or stop.is_set():break
                        if number==0:fixture=lease['job']['official']['fixture_id']
                        began=time.monotonic()
                        for event in trace:
                            time.sleep(max(0,began+(event['server_unix_ms']-first)/1000-time.monotonic()))
                            runner.record(lease['job']['id'],event['type'],event['data'])
                            flush_modes[150 if runner.event_flush_seconds else 0]+=1
                        assert runner.finish(),'Runner could not finish fenced replay'
                        runner.report=native_report
                        answer=grader.post_result(runner,'completed')
                        assert answer and answer['status']=='completed',answer
                        job=lease['job']['id']
                        assert len(runner.replay)==len(trace)
                        sent[job]=dict(count=len(runner.replay),sha256=event_digest(runner.replay))
                        completed_gzip[job]=grader.client.payloads[runner.path('result-replay')]
                        runner.lost.set();runner.heartbeat_wake.set();games_by_slot[number]+=1
                        if time.monotonic()>=cutoff[0] or stop.is_set():break
                        waited=time.monotonic();lease=None
                        while lease is None and time.monotonic()<cutoff[0] and not stop.is_set():
                            lease=grader.client.call('POST','/api/v1/grader/lease',dict(wait_seconds=min(25,max(1,int(cutoff[0]-time.monotonic())))))['lease']
                        lease_waits[number]+=time.monotonic()-waited
                        if lease is None:break
                except Exception as exc:event_errors.append(repr(exc))
                finally:
                    if runner:
                        runner.lost.set();runner.heartbeat_wake.set();runner.flush_wake.set()
                        runner.flusher.join(10)
                    for thread in beats:thread.join(12)
            for number in range(32):
                thread=threading.Thread(target=emit,args=(number,));thread.start();emitters.append(thread)
            def viewer(number):
                cursor='';viewed_fixture=None
                try:
                    # Match EventSource: normal 60-second EOF renews the stream.
                    while not sse_stop.is_set():
                        if viewed_fixture!=fixture:cursor='';viewed_fixture=fixture
                        headers={'Cookie':cookies[number%len(cookies)],'Accept':'text/event-stream'}
                        if cursor:headers['Last-Event-ID']=cursor
                        try:
                            with load_http().stream('GET',origin+f'/api/v1/competitions/{cid}/fixtures/{viewed_fixture}/events',headers=headers,timeout=httpx.Timeout(20,read=65)) as response:
                                assert response.status_code==200,response.status_code
                                with stream_lock:active.add(number);opens[number]+=1
                                pending=b''
                                for raw in response.iter_raw():
                                    with stream_lock:chunks[number]+=1
                                    pending+=raw.replace(b'\r\n',b'\n')
                                    while b'\n\n' in pending:
                                        block,pending=pending.split(b'\n\n',1)
                                        for line in block.splitlines():
                                            if line.startswith(b'id:'):cursor=line[3:].strip().decode()
                                            if line.startswith(b'data:'):
                                                try:
                                                    if json.loads(line[5:]).get('type')=='state':states[number]+=1
                                                except (ValueError,AttributeError):pass
                                    if sse_stop.is_set():break
                        finally:
                            with stream_lock:active.discard(number)
                        # A completed board waits for the next leased game, rather than
                        # reopening the same finished replay in a tight reconnect loop.
                        while not sse_stop.is_set() and viewed_fixture==fixture:sse_stop.wait(.1)
                except Exception as exc:
                    if not sse_stop.is_set():stream_errors.append(type(exc).__name__)
                finally:
                    with stream_lock:active.discard(number)
            for number in range(100):
                thread=threading.Thread(target=viewer,args=(number,));thread.start();viewers.append(thread)
                time.sleep(.02)
            wait(lambda:len(active)==100,'100 real SSE streams',30)
            wait(lambda:len(github.polls)==1000,'warm all 1000 GitHub repositories',240)
            initial=http.get(origin+'/api/v1/competitions/'+cid,headers={'Cookie':cookies[0]}).json()
            assert len(initial['entries'])==1000 and sum(initial['fixture_counts'].values())==1000,initial['fixture_counts']
            hash_before=http.get(origin+'/api/v1/admin/__lab/hash-cpu',headers=admin).json()
            report['base_disk']=disk(data);begin=time.monotonic();cutoff[0]=begin+60;print('BEGIN32',flush=True);emission_start.set()
            def cpu():return int(next(line.split()[1] for line in (cg/'cpu.stat').read_text().splitlines() if line.startswith('usage_usec ')))
            def process_cpu():
                result={}
                for pid in (cg/'cgroup.procs').read_text().split():
                    try:
                        args=Path('/proc',pid,'cmdline').read_bytes().split(bytes([0]))
                        label='hub' if b'--serve' in args else 'publisher' if b'publish' in args else 'mailer' if b'arena_api.mailer' in args else 'launcher'
                        fields=Path('/proc',pid,'stat').read_text().rsplit(')',1)[1].split()
                        result[label]=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')
                    except FileNotFoundError:pass
                return result
            def monitor():
                previous,used=time.monotonic(),cpu()
                while not stop.wait(5):
                    now,next_used=time.monotonic(),cpu()
                    report['samples'].append(dict(seconds=round(now-begin,3),cpu_pct_of_2vcpu=round((next_used-used)/(now-previous)/20000,2),ram_bytes=int((cg/'memory.current').read_text()),sse_active=len(active),sse_live_viewers=sum(t.is_alive() for t in viewers),**disk(data)))
                    previous,used=now,next_used
            monitoring=threading.Thread(target=monitor);monitoring.start();login_serial=0
            rolling=[]
            def page_view(number):
                rows=[]
                for path in (*PAGE,'/team/elo','/teams/me/github'):
                    started=time.monotonic()
                    try:code=load_http().get(origin+'/api/v1'+path,headers={'Cookie':cookies[number%len(cookies)]}).status_code
                    except httpx.HTTPError:code=0
                    elapsed=time.monotonic()-started
                    route_times.setdefault(path,[]).append(elapsed)
                    rows.append((elapsed,code));rolling.append(elapsed)
                return rows
            def login(number):
                started=time.monotonic()
                response=load_http().post(origin+'/api/v1/auth/login',json=dict(username=f'{26000000+number}@vnu.test',password=PASSWORD),headers={'Cookie':''})
                return time.monotonic()-started,response.status_code
            with ThreadPoolExecutor(256) as pages,ThreadPoolExecutor(64) as logins:
                for rate in (24,):
                    duration=60
                    rolling=[];route_times={}
                    started,used=time.monotonic(),cpu();process_before=process_cpu();old_polls=sum(github.polls.values());futures=[];auth=[];next_login=0
                    for number in range(rate*duration):
                        time.sleep(max(0,started+number/rate-time.monotonic()))
                        futures.append(pages.submit(page_view,number))
                        if number/rate>=next_login/1:
                            auth.append(logins.submit(login,login_serial));login_serial+=1;next_login+=1
                    rows=[row for future in futures for row in future.result()];authenticated=[f.result() for f in auth];elapsed=time.monotonic()-started
                    result=dict(page_views_per_second=rate,wall_seconds=round(elapsed,3),offered_views=len(futures),get_requests=len(rows),page_p95_ms=percentile([d for d,c in rows],.95),page_codes=dict(Counter(str(c) for d,c in rows)),
                        login_p95_ms=percentile([d for d,c in authenticated],.95),login_codes=dict(Counter(str(c) for d,c in authenticated)),cpu_pct_of_2vcpu=round((cpu()-used)/elapsed/20000,2),ram_bytes=int((cg/'memory.current').read_text()),github_repo_reads=sum(github.polls.values())-old_polls,sse_active=len(active))
                    result['process_cpu_pct_of_2vcpu']={k:round((v-process_before.get(k,0))/elapsed/2*100,2) for k,v in process_cpu().items()}
                    result['routes']={path:dict(requests=len(values),p95_ms=percentile(values,.95),mean_ms=round(sum(values)/len(values)*1000,2)) for path,values in route_times.items()}
                    hash_after=http.get(origin+'/api/v1/admin/__lab/hash-cpu',headers=admin).json()
                    result['argon2_cpu_seconds']=round(hash_after['cpu_seconds']-hash_before['cpu_seconds'],3)
                    result['argon2_verifications']=hash_after['calls']-hash_before['calls']
                    report['argon2_parameters']=hash_after['parameters']
                    report['levels'].append(result);print(json.dumps(result),flush=True)
                    result['all_requests_ok']=result['page_codes']=={'200':len(rows)} and result['login_codes']=={'200':len(authenticated)}
                    result['github_repo_reads_per_minute']=round(result['github_repo_reads']/elapsed*60,2)

                    if result['page_p95_ms']>300 or result['login_p95_ms']>1000:break
            report.update(ram_peak_bytes=int((cg/'memory.peak').read_text()),sse_chunks=sum(chunks.values()),github_repositories_seen=len(github.polls),event_errors=event_errors,stream_errors=stream_errors,github_errors=github.errors)
            stop.set()
            for thread in emitters:thread.join(20)
            # Include the final in-flight games in the whole-phase CPU and RAM budget.
            baseline=report['levels'][0]
            elapsed=time.monotonic()-started
            baseline.update(wall_seconds=round(elapsed,3),cpu_pct_of_2vcpu=round((cpu()-used)/elapsed/20000,2),
                ram_bytes=int((cg/'memory.current').read_text()),github_repo_reads=sum(github.polls.values())-old_polls)
            baseline['github_repo_reads_per_minute']=round(baseline['github_repo_reads']/elapsed*60,2)
            baseline['process_cpu_pct_of_2vcpu']={k:round((v-process_before.get(k,0))/elapsed/2*100,2) for k,v in process_cpu().items()}
            report['ram_peak_bytes']=int((cg/'memory.peak').read_text())
            print('FINAL32 '+json.dumps(baseline),flush=True)
            with sqlite3.connect(data/'arena.sqlite') as db:
                for job,expected in sent.items():
                    raw=db.execute('SELECT data FROM replay_archives WHERE job=?',(job,)).fetchone()[0]
                    assert raw==completed_gzip[job],'Compressed bytes must be stored unchanged'
                    stored=json.loads(gzip.decompress(raw))['events']
                    assert len(stored)==expected['count'] and event_digest(stored)==expected['sha256'],job
            report['events_verified']=sum(item['count'] for item in sent.values())
            report['grader_http_calls']=dict(sum((client.calls for client in grader_clients),Counter()))
            report['games_by_slot']=dict(games_by_slot)
            report['lease_wait_seconds_by_slot']={str(k):round(v,3) for k,v in lease_waits.items()}
            report['event_jobs_verified']=len(sent)
            report['negotiated_flush_ms_counts']=dict(flush_modes)
            report['sse_viewers_with_data']=len(chunks)
            report['sse_viewers_with_state']=len(states)
            report['sse_min_states']=min(states.values(),default=0)
            assert report['events_verified']>=150000 and len(sent)>=32 and len(states)==100 and not event_errors
            baseline=report['levels'][0]
            assert baseline['all_requests_ok'] and not stream_errors and not github.errors and baseline['github_repo_reads_per_minute']>=1000,baseline
            assert baseline['page_p95_ms']<=300 and baseline['login_p95_ms']<=1000 and baseline['cpu_pct_of_2vcpu']<=50,baseline
            assert report['ram_peak_bytes']<=.6*2*1024**3,report['ram_peak_bytes']
            assert 'Traceback' not in (output/'stack.log').read_text()
        finally:
            report['github_repositories_seen']=len(github.polls)
            report['github_repo_reads']=sum(github.polls.values())
            report.update(sse_chunks=sum(chunks.values()),sse_connections=sum(opens.values()),sse_clients_with_data=len(chunks),stream_errors=stream_errors,event_errors=event_errors,github_errors=github.errors)
            if cg and cg.exists():
                report['memory_events']=(cg/'memory.events').read_text()
                report['ram_peak_bytes']=int((cg/'memory.peak').read_text())
            stop.set();sse_stop.set()
            for thread in emitters:thread.join(20)
            if monitoring:monitoring.join(10)
            for thread in viewers:thread.join(20)
            if hub:
                hub.terminate()
                try:hub.wait(30)
                except subprocess.TimeoutExpired:hub.kill();hub.wait()
            for client in load_clients:client.close()
            github.close();http.close();log.close()
            (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
