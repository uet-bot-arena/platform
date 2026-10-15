"""Load and restart checks on a COPY of experimental_integration proof data.

First run ARENA_EXPERIMENTAL_PROOF_DATA=experimental-ui-proof-final with
experimental_integration.py. No requests target the user's local controller.
Completed replay/preview throughput is measured; this is not live-game capacity.
"""
import asyncio
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import httpx

from load_resilience import Harness, ROOT, OUTPUT, percentile
from arena import experimental, sandbox

async def main(proof_dir):
    folder=(OUTPUT/proof_dir).resolve()
    assert folder.parent==OUTPUT.resolve(),'Proof must be a test-results subdirectory'
    proof=folder/'arena.sqlite'
    assert proof.is_file(),'Run experimental_integration.py with the proof-data environment variable first'
    latest=json.loads((OUTPUT/'experimental-integration-latest.json').read_text())
    cid=latest['competition_id'];fid=latest['fixtures'][0]['id']
    base=f'/competitions/{cid}';event_path=f'{base}/fixtures/{fid}/events'
    with tempfile.TemporaryDirectory(prefix='experimental-load-',dir=OUTPUT) as directory:
        original=sqlite3.connect(f'file:{proof}?mode=ro',uri=True)
        copied=sqlite3.connect(Path(directory)/'arena.sqlite');original.backup(copied);original.close();copied.close()
        h=Harness(directory,sandbox.image_id('uet-arena-worker:experimental-clock-v2'),'experimental-load')
        async with httpx.AsyncClient(timeout=30,limits=httpx.Limits(max_connections=100,max_keepalive_connections=100)) as guest,httpx.AsyncClient(timeout=30) as admin:
            try:
                await h.start(guest)
                before=(await h.request(guest,'GET',event_path)).json()
                baseline={p:(await h.request(guest,'GET',p)).json() for p in (base,base+'/standings',base+'/watch',event_path)}
                assert baseline[base]['status']=='completed' and baseline[base]['config']['ruleset']
                async def batch(name,count,concurrency,operation):
                    lock=asyncio.Semaphore(concurrency);times=[];codes=Counter()
                    async def one(i):
                        async with lock:
                            start=time.perf_counter();r=await operation(i);times.append((time.perf_counter()-start)*1000);codes[str(r.status_code)]+=1
                            assert r.status_code==200,(name,r.status_code,r.text[:500])
                    await asyncio.gather(*(one(i) for i in range(count)))
                    row=dict(name=name,requests=count,concurrency=concurrency,codes=dict(codes),p50_ms=percentile(times,.5),p95_ms=percentile(times,.95),max_ms=round(max(times),3))
                    h.report['load'].append(row);print('PASS',json.dumps(row),flush=True)
                async def read(i):
                    path=list(baseline)[i%len(baseline)];r=await guest.get(h.origin+'/api/v1'+path)
                    if r.status_code==200:
                        assert r.json()==baseline[path]
                        assert not r.headers.get('Set-Cookie')
                    return r
                await batch('experimental public views and replay',240,12,read)
                async def sse(i):
                    path=base+'/watch' if i%2 else event_path
                    r=await guest.get(h.origin+'/api/v1'+path,headers={'Accept':'text/event-stream'})
                    if r.status_code==200:
                        assert r.headers['content-type'].startswith('text/event-stream')
                        assert 'source_path' not in r.text and 'artifact_sha256' not in r.text
                        if i%2==0:assert '"energy"' in r.text and '"ruleset"' in r.text
                    return r
                await batch('completed replay SSE and competition SSE',120,24,sse)
                login=await h.request(admin,'POST','/auth/login',json=dict(username='organizer',password='experimental-test-only-password'))
                admin.headers['X-CSRF-Token']=login.json()['csrf_token']
                d=deepcopy(experimental.PRESETS['domination']);d['name']='Load movement preview';d['modifiers']=[dict(id='wide',filter={},pattern='orthogonal',range=3,cost=0,cooldown=1)]
                experimental.validate(d)
                async def preview(i):
                    r=await admin.post(h.origin+'/api/v1/admin/rulesets/preview',json={'definition':d,'actions':['a0']*(i%4)})
                    if r.status_code==200:
                        o=r.json()['observation'];assert len(o['legal_actions'])<=240 and len(json.dumps(o).encode())<60000
                    return r
                await batch('bounded admin rule preview',96,8,preview)
                h.checked('replay_payloads_identical_under_concurrent_reads_no_private_fields')
                # Kill only the owned test CLI while idle. This verifies WAL
                # recovery and immutable replay, not in-flight match recovery.
                h.process.kill();await asyncio.to_thread(h.process.wait,10)
                await h.start(guest)
                after=(await h.request(guest,'GET',event_path)).json()
                assert before==after
                for path in (base,base+'/standings',base+'/watch'):assert (await h.request(guest,'GET',path)).json()==baseline[path]
                h.checked('owned_controller_sigkill_restart_keeps_ruleset_replay_and_points')
                first=before['items'][len(before['items'])//2]['seq']
                wire=(await h.request(guest,'GET',event_path,headers={'Accept':'text/event-stream','Last-Event-ID':before['attempt_id']+':'+str(first)})).text
                assert f"id: {before['attempt_id']}:{first}\n" not in wire and 'event: arena' in wire
                h.checked('sse_cursor_reconnect_after_restart_without_duplicate_events')
                h.report.update(passed=True,scope='Completed experimental HTTP/SSE replay and declarative preview on a cloned DB; no active worker capacity claim')
            finally:
                if h.process and h.process.poll() is None:await h.stop_idle()
                h.log.close()
        (OUTPUT/'experimental-load-latest.json').write_text(json.dumps(h.report,indent=2),encoding='utf-8')
    print('ALL EXPERIMENTAL LOAD / RESTART CHECKS PASSED',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof-dir',default='experimental-ui-proof-final')
    asyncio.run(main(parser.parse_args().proof_dir))
