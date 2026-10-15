"""200 actual SSE connections + 40 authenticated teams, real Docker jobs."""
import asyncio
import io
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import uuid
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'/'load_lab'))
import httpx
from lab_common import Api,ADMIN_PW,start_server,stop_server

def percentile(values,p=.95):return sorted(values)[min(len(values)-1,int(len(values)*p))]
def suite_rate(job,events):
    phases=[e['server_unix_ms'] for e in events if e['type']=='phase' and e['data'].get('phase')=='benchmarking']
    ends=[e['server_unix_ms'] for e in events if e['type']=='completed']
    return 72000/max(1,ends[-1]-phases[0]) if phases and ends else None

def main():
    report={}
    with tempfile.TemporaryDirectory(prefix='arena-load-acceptance-') as tmp:
        origin,server,thread=start_server(Path(tmp),limit_concurrency=1024)
        admin=Api(origin);teams=[]
        try:
            until=time.monotonic()+30
            while time.monotonic()<until:
                if admin.http.get('/api/v1/health/ready').status_code==200:break
                time.sleep(.1)
            admin.login('organizer',ADMIN_PW)
            source=(ROOT/'examples/reference-bots/bot.cpp').read_text()
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:z.writestr('bot.cpp',source)
            for i in range(40):
                credential=admin.json('POST','/admin/teams',expected=(201,),json={'username':f'load{i:04}','profile':{'team':f'Load Team {i}','email':f'load{i}@example.test','confirmed':True,'members':[{'name':f'Test {n}','student_id':f'LOAD{i:03}{n}'} for n in range(3)]}})
                api=Api(origin);api.login(f'load{i:04}',credential['temporary_password'])
                changed=api.json('POST','/auth/password',json={'current_password':credential['temporary_password'],'new_password':'load-only-new-password'})
                api.http.headers['X-CSRF-Token']=changed['csrf_token']
                sub=api.json('POST','/submissions',expected=(201,),content=raw.getvalue(),headers={'Content-Type':'application/zip','X-Submission-Version':'v1.0.0'})
                teams.append((api,sub['id']))
            async def run():
                async def wait(api,ident):
                    until=time.monotonic()+240
                    while time.monotonic()<until:
                        job=await asyncio.to_thread(api.json,'GET','/jobs/'+ident)
                        if job['status'] in ('completed','failed','aborted'):
                            assert job['status']=='completed',job
                            return job
                        await asyncio.sleep(.25)
                    raise AssertionError('Load job timed out')
                # Compare the same worker concurrency, with and without viewers.
                # HTTP timings below use async sockets, not a bounded client
                # executor whose own queue would inflate the server latency.
                baseline=[]
                for api,sid in teams[:server.config.app.state.service.worker_count]:
                    baseline.append(api.json('POST','/benchmarks',expected=(202,),json={'submission_id':sid,'match_ms':60000}))
                await asyncio.gather(*(wait(api,job['id']) for (api,_),job in zip(teams,baseline)))
                idle_rate=statistics.median(suite_rate({},api.json('GET','/jobs/'+job['id']+'/events')['items']) for (api,_),job in zip(teams,baseline))
                cup=admin.json('POST','/admin/competitions',expected=(201,),json={'title':'SSE load view','max_teams':2,'match_ms':60000})
                admin.json('POST','/admin/competitions/'+cup['id']+'/open',json={})
                for (entry_api,entry_sid),bench in zip(teams[:2],baseline):
                    entry_api.json('POST','/competitions/'+cup['id']+'/entry',json={'submission_id':entry_sid,'benchmark_id':bench['id'],'delegate_timeout_claims':True})
                admin.json('POST','/admin/competitions/'+cup['id']+'/lock',json={})
                fid=admin.json('GET','/competitions/'+cup['id']+'/fixtures')['items'][0]['id']
                async with httpx.AsyncClient(base_url=origin,timeout=60,limits=httpx.Limits(max_connections=450,max_keepalive_connections=40)) as client:
                    connected=0;statuses=[];stop=asyncio.Event()
                    async def viewer(path):
                        nonlocal connected
                        async with client.stream('GET',path,headers={'Accept':'text/event-stream'}) as response:
                            statuses.append(response.status_code);assert response.status_code==200
                            connected+=1
                            async for line in response.aiter_lines():
                                if stop.is_set():break
                    paths=['/api/v1/competitions/'+cup['id']+'/watch','/api/v1/competitions/'+cup['id']+'/fixtures/'+fid+'/events']
                    viewers=[asyncio.create_task(viewer(path)) for _ in range(200) for path in paths]
                    until=time.monotonic()+40
                    while connected<400 and time.monotonic()<until:await asyncio.sleep(.05)
                    assert connected==400,connected
                    async def enqueue(api,sid):
                        started=time.perf_counter()
                        response=await client.post('/api/v1/benchmarks',json={'submission_id':sid,'match_ms':60000},
                            headers={'Idempotency-Key':uuid.uuid4().hex,'X-CSRF-Token':api.http.headers['X-CSRF-Token']},cookies=api.http.cookies)
                        return {'latency_ms':(time.perf_counter()-started)*1000,'status':response.status_code,'job_id':response.json().get('id')}
                    queued=await asyncio.gather(*(enqueue(api,sid) for api,sid in teams))
                    assert all(r['status']==202 for r in queued),queued
                    # Authenticated team reads, while all 200 feeds remain connected.
                    async def read_team(api):
                        started=time.perf_counter();response=await client.get('/api/v1/dashboard',cookies=api.http.cookies)
                        return {'latency_ms':(time.perf_counter()-started)*1000,'status':response.status_code}
                    reads=await asyncio.gather(*(read_team(api) for api,_ in teams))
                    await asyncio.to_thread(admin.json,'POST','/admin/competitions/'+cup['id']+'/start',json={})
                    jobs=await asyncio.gather(*(wait(api,row['job_id']) for (api,_),row in zip(teams,queued)))
                    stop.set();await asyncio.wait_for(asyncio.gather(*viewers),10)
                rates=[]
                for (api,_),row in zip(teams,queued):
                    rates.append(suite_rate({},api.json('GET','/jobs/'+row['job_id']+'/events')['items']))
                report.update(viewers=200,sse_connections=400,teams=40,sse_statuses=statuses,enqueue=queued,team_reads=reads,
                    enqueue_p95_ms=percentile([r['latency_ms'] for r in queued]),team_api_p95_ms=percentile([r['latency_ms'] for r in reads]),
                    idle_samples_per_second=idle_rate,loaded_median_samples_per_second=statistics.median(rates),
                    worker_rate_ratio=statistics.median(rates)/idle_rate,all_jobs_completed=len(jobs),
                    acceptance={'enqueue_p95_lt_300':percentile([r['latency_ms'] for r in queued])<300,
                                'team_p95_lt_500':percentile([r['latency_ms'] for r in reads])<500,
                                'worker_retains_90pct':statistics.median(rates)/idle_rate>=.9})
            asyncio.run(run())
        finally:
            stop_server(server,thread)
            output=ROOT/'test-results';output.mkdir(exist_ok=True)
            (output/'load-acceptance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('enqueue','team_reads','sse_statuses')},indent=2))

if __name__=='__main__':main()
