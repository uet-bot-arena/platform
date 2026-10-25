"""The five independent D0 generators; none runs inside arena.slice."""
import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import threading
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]


def paths(document):
    result={}
    for line in document.splitlines():
        cells=[v.strip() for v in line.split('|')]
        if len(cells)==5 and cells[1] in ('home','team','submissions','standings','schedule','queue'):
            result[cells[1]]=[[x for x in cell.split(', ') if x!='—'] for cell in cells[2:4]]
    assert len(result)==6,'tests/fixtures/ui-routes.md navigation contract is missing'
    return result


def event_digest(events):
    return hashlib.sha256(json.dumps([dict(type=e['type'],data=e['data']) for e in events],separators=(',',':'),allow_nan=False).encode()).hexdigest()


def fake_github(meta,ready,commands,results):
    from season_github import GitHubFixture
    fixture=GitHubFixture()
    fixture.repositories={(r['owner'],r['name']):r for r in meta['repositories']}
    handler=fixture.server.RequestHandlerClass;original=handler.route
    def slow(self,method):
        time.sleep(.2)  # Outside the fixture lock: 32 HTTP polls can overlap.
        original(self,method)
    handler.route=slow
    ready.put(('github',fixture.origin))
    before=0;cpu=0;began=0
    try:
        while True:
            command=commands.get()
            if command=='warm':results.put(dict(unique=len(fixture.polls),errors=list(fixture.errors)))
            elif command=='begin':
                before=sum(fixture.polls.values());cpu=time.process_time();began=time.monotonic();results.put(True)
            elif command=='end':
                results.put(dict(polls=sum(fixture.polls.values())-before,errors=list(fixture.errors),cpu_seconds=time.process_time()-cpu,seconds=time.monotonic()-began))
            elif command=='stop':break
    finally:fixture.close()


def user_worker(number,people,origin,cid,contract,start,stop,end,ready,output,login_interval=1,pool_count=256):
    import httpx
    async def run():
        latencies=[];login=[];statuses=Counter();errors=[]
        # httpcore probes sniffio on each I/O; keep the optional package pinned in dev requirements.
        import sniffio
        assert sniffio.current_async_library()=='asyncio'
        async with AsyncExitStack() as stack:
            # Small independent pools avoid httpcore scanning hundreds of idle sockets per request.
            clients=[await stack.enter_async_context(httpx.AsyncClient(timeout=30,verify=False,
                limits=httpx.Limits(max_connections=2,max_keepalive_connections=2))) for _ in range(pool_count)]
            client=await stack.enter_async_context(httpx.AsyncClient(timeout=30,verify=False))
            async def read(path,person,measured):
                began=time.monotonic()
                try:
                    headers={'Cookie':person['cookie'],'X-Forwarded-For':person['ip'],'Accept-Encoding':'gzip'}
                    etags=person.setdefault('etags',{})
                    if path.startswith('/snapshots/') and path in etags:headers['If-None-Match']=etags[path]
                    response=await clients[person['index']%pool_count].send(httpx.Request('GET',origin+path,headers=headers),stream=True)
                    try:
                        # Measure through the complete wire body; browser gzip decoding is outside hub latency.
                        async for _ in response.aiter_raw():pass
                    finally:await response.aclose()
                    if path.startswith('/snapshots/') and 'etag' in response.headers:etags[path]=response.headers['etag']
                    if not measured:assert response.status_code in (200,304),(path,response.status_code)
                    if measured:
                        latencies.append(time.monotonic()-began);statuses[str(response.status_code)]+=1
                        if response.status_code!=200 and not (path.startswith('/snapshots/') and response.status_code==304):errors.append(path+':'+str(response.status_code))
                except Exception as exc:
                    if measured:errors.append(type(exc).__name__)
                    else:raise
            async def page(route,person,measured):
                api,files=contract[route]
                await asyncio.gather(*(read('/api/v1'+path,person,measured) for path in api),
                    *(read('/snapshots/'+path.replace('{id}',cid),person,measured) for path in files))
            routes=['home','team','submissions','standings','schedule','queue']
            weights=[15,25,15,25,15,5]
            visible=[]
            for i,person in enumerate(people):
                rng=random.Random(20261024+number*2000+i)
                if person['index']%3:
                    visible.append((person,rng,rng.choices(routes,weights)[0]))
            # Warm pages in bounded batches; thousands of pool waiters distort the generator.
            for offset in range(0,len(visible),32):
                batch=visible[offset:offset+32]
                await asyncio.gather(*(page(route,person,False) for person,_,route in batch),
                    *(read('/snapshots/announcements.json',person,False) for person,_,_ in batch))
            ready.put(('users-'+str(number),len(visible)))
            await asyncio.to_thread(start.wait)
            cpu=time.process_time();began=time.monotonic()
            async def person_loop(person,rng,route):
                nav=began+rng.uniform(30,90)
                interval=3 if person['checking'] else 30
                private=began+rng.uniform(0,interval)
                public=began+rng.uniform(0,10);banner=began+rng.uniform(0,10)
                while not stop.is_set() and time.monotonic()<end.value:
                    now=time.monotonic()
                    if now>=nav:
                        route=rng.choices(routes,weights)[0]
                        await page(route,person,True);nav=time.monotonic()+rng.uniform(30,90)
                        private=time.monotonic()+interval;public=time.monotonic()+10
                    if route in ('team','submissions') and now>=private:
                        await page(route,person,True);private=time.monotonic()+interval
                    if route in ('home','standings','schedule','queue') and now>=public:
                        # Same shared reads as watchElo, not the private navigation request.
                        _,files=contract[route]
                        await asyncio.gather(*(read('/snapshots/'+path.replace('{id}',cid),person,True) for path in files))
                        public=time.monotonic()+10
                    if now>=banner:
                        await read('/snapshots/announcements.json',person,True);banner=time.monotonic()+10
                    next_due=min(nav,banner,private if route in ('team','submissions') else public)
                    await asyncio.sleep(max(.001,min(next_due,end.value)-time.monotonic()))
            async def logins():
                i=0;due=began
                while not stop.is_set() and time.monotonic()<end.value:
                    await asyncio.sleep(max(0,due-time.monotonic()))
                    if time.monotonic()>=end.value:break
                    person=people[i%len(people)];t=time.monotonic()
                    try:
                        response=await client.post(origin+'/api/v1/auth/login',json=dict(username=person['username'],password='capacity-fixture-only-password'),headers={'X-Forwarded-For':person['ip']})
                        login.append(time.monotonic()-t);statuses['login:'+str(response.status_code)]+=1
                    except Exception as exc:errors.append('login:'+type(exc).__name__)
                    i+=1;due=began+i*login_interval
            tasks=[person_loop(*person) for person in visible]
            if number==0:tasks.append(logins())
            await asyncio.gather(*tasks)
            output.put(dict(name='users-'+str(number),cpu_seconds=time.process_time()-cpu,seconds=time.monotonic()-began,
                latencies=latencies,login_latencies=login,statuses=dict(statuses),errors=errors))
    import uvloop
    with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:runner.run(run())
    stop.wait()  # Keep /proc CPU counters available until the coordinator samples the phase end.


def sse_worker(origin,cid,fixture,people,start,stop,end,ready,output):
    import httpx
    async def run():
        opened=set();states=set();errors=[];cpu=[None];began=[None]
        async with httpx.AsyncClient(timeout=httpx.Timeout(10,read=65),limits=httpx.Limits(max_connections=100,max_keepalive_connections=100)) as client:
            async def watch(i):
                cursor='';current=None
                while not stop.is_set():
                    target=fixture.value.decode()
                    if not target:await asyncio.sleep(.05);continue
                    if target!=current:cursor='';current=target
                    headers={'Cookie':people[i]['cookie'],'Accept':'text/event-stream','X-Forwarded-For':people[i]['ip']}
                    if cursor:headers['Last-Event-ID']=cursor
                    try:
                        async with client.stream('GET',origin+f'/api/v1/competitions/{cid}/fixtures/{target}/events',headers=headers) as response:
                            assert response.status_code==200,response.status_code
                            opened.add(i)
                            async for line in response.aiter_lines():
                                if start.is_set() and cpu[0] is None:cpu[0]=time.process_time();began[0]=time.monotonic()
                                if line.startswith('id:'):cursor=line[3:].strip()
                                if line.startswith('data:'):
                                    data=json.loads(line[5:])
                                    if start.is_set() and data.get('type')=='state':states.add(i)
                                if stop.is_set() or current!=fixture.value.decode():break
                    except Exception as exc:
                        if start.is_set() and not stop.is_set():errors.append(type(exc).__name__)
                        elif not stop.is_set():raise
                    await asyncio.sleep(.05)
            tasks=[asyncio.create_task(watch(i)) for i in range(100)]
            while len(opened)<100:
                for task in tasks:
                    if task.done():task.result()
                await asyncio.sleep(.05)
            ready.put(('sse',100))
            await asyncio.to_thread(start.wait)
            cpu[0]=time.process_time();began[0]=time.monotonic()
            while not stop.is_set():await asyncio.sleep(.1)
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            output.put(dict(name='sse',states=len(states),errors=errors,cpu_seconds=time.process_time()-cpu[0],seconds=time.monotonic()-began[0]))
    asyncio.run(run())


def grader_worker(origin,tokens,runtime_id,fixture,start,stop,end,ready,output,state_dir):
    from arena.grader import Grader,GraderRunner,HubClient
    trace=json.loads((ROOT/'tests/load_lab/native-event-trace.json').read_text())
    report=json.loads((ROOT/'tests/load_lab/native-match-report.json').read_text())
    assert len(trace)==178
    identity=dict(platform='windows',backend='windows',runtime_id=runtime_id)
    graders=[];records=[];errors=[];guard=threading.Lock()
    class Recording(HubClient):
        def call(self,method,path,*args,**kwargs):
            answer=super().call(method,path,*args,**kwargs)
            if path.endswith('/result-replay'):self.payload=kwargs['data']
            return answer
    for i,token in enumerate(tokens):
        grader=Grader(origin,token,backend='windows',state_dir=Path(state_dir)/str(i),backend_factory=lambda *_:SimpleNamespace(identity=lambda:identity))
        grader.client=Recording(origin,token)
        grader.settings=grader.client.call('POST','/api/v1/grader/hello',dict(**identity,slots=1,version='users-4000-trace'))
        graders.append(grader)
    ready.put(('graders',len(graders)));began=time.monotonic();cpu=time.process_time()
    def slot(i,grader):
        runner=None;beat=None
        try:
            # Warm the real lease/result loop too; phase starts then stay spread over a game.
            if stop.wait(i*10/len(graders)):return
            while not stop.is_set() and (not start.is_set() or time.monotonic()<end.value):
                lease=grader.client.call('POST','/api/v1/grader/lease',dict(wait_seconds=1))['lease']
                if lease is None:continue
                received=time.monotonic()
                assert lease['job']['profile']==report['profile']
                if i==0:fixture.value=lease['job']['official']['fixture_id'].encode()
                runner=GraderRunner(grader,None,lease,threading.Event())
                beat=threading.Thread(target=grader.heartbeat_loop,args=(runner,));beat.start()
                for event in trace:
                    time.sleep(max(0,received+(event['server_unix_ms']-trace[0]['server_unix_ms'])/1000-time.monotonic()))
                    runner.record(lease['job']['id'],event['type'],event['data'])
                time.sleep(max(0,received+10-time.monotonic()))
                assert runner.finish()
                runner.report=report
                answer=grader.post_result(runner,'completed');assert answer['status']=='completed'
                with guard:records.append(dict(job=lease['job']['id'],finished=time.monotonic(),count=len(runner.replay),digest=event_digest(runner.replay),gzip=grader.client.payload))
                runner.lost.set();runner.heartbeat_wake.set();beat.join(2)
        except Exception as exc:
            with guard:errors.append(repr(exc))
        finally:
            if runner:runner.lost.set();runner.heartbeat_wake.set();runner.flush_wake.set();runner.flusher.join(10)
            if beat:beat.join(2)
    threads=[threading.Thread(target=slot,args=(i,g)) for i,g in enumerate(graders)]
    for thread in threads:thread.start()
    for thread in threads:thread.join()
    output.put(dict(name='graders',records=records,errors=errors,cpu_seconds=time.process_time()-cpu,seconds=time.monotonic()-began))
