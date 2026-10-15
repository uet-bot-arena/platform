"""Real C++/Docker proof of cumulative, independent, hard game clocks.

The 450ms private test profile accelerates exhaustion; HTTP only accepts 1/5min.
Does not change the local platform database or replace legacy worker images.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import sandbox
from arena.config import match_profile, worker_image
from arena.protocol import Fault
from arena.service import Service
from arena.store import Store
from arena.adjudication import CURRENT


def main():
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    p=replace(match_profile(60000),match_ms=450,grace_ms=30)
    image=sandbox.image_id(worker_image(p))
    checks=[]
    def checked(name,**data):
        checks.append({'name':name,'passed':True,**data});print('PASS',name,flush=True)
    source=(ROOT/'sdk'/'bot.cpp').read_text().replace('#include <iostream>',
        '#include <iostream>\n#include <unistd.h>').replace('const auto legal =','usleep(160000);\n                const auto legal =')
    with tempfile.TemporaryDirectory(prefix='bank-integration-',dir=output) as folder:
        root=Path(folder);src=root/'source';src.mkdir();(src/'bot.cpp').write_text(source)
        built=sandbox.build(src,root/'artifact',p,image);assert built['ok'],built
        store=Store(root/'state',recover_jobs=False);service=Service(store,start_worker=False)
        report={'samples':[]}
        job=store.create('job','proof',{'kind':'match','status':'queued','profile':p.public(),'adjudication':CURRENT})
        workers=[]
        try:
            for side in ('red','blue'):
                worker=sandbox.Worker(root/'artifact',p,image);workers.append(worker)
                worker.start(job['id'],side,{'kind':'match','adjudication':CURRENT})
            service.run_match(job,workers,report,p)
            assert report['result']=={'winner':'blue','reason':'timeout'},report
            assert [s['side'] for s in report['samples']]==['red','blue','red','blue'],report
            assert report['clocks']['red']==0 and report['clocks']['blue']>0,report
            checked('cumulative_timeout_on_third_red_turn',clocks=report['clocks'])
            events=store.events(job['id']);states=[e['data']['clocks'] for e in events if e['type']=='state']
            assert states[0]=={'red':450,'blue':450}
            assert states[1]['red']<300 and states[1]['blue']==450
            assert states[2]['red']==states[1]['red'] and states[2]['blue']<300
            assert states[3]['red']<states[1]['red'] and states[3]['blue']==states[2]['blue']
            checked('opponent_bank_frozen_and_remaining_persisted_for_replay')
            pending=next(e for e in events if e['type']=='timeout_pending')
            claim=next(e for e in events if e['type']=='timeout_claimed')
            assert claim['data']['grace_waited_ms']>=30
            assert pending['data']['clocks']['red']==0
            assert report['final_state']['ply']==4,'late fifth action must not change board'
            checked('hard_deadline_late_action_rejected_and_delegated_claim',grace_ms=claim['data']['grace_waited_ms'])
            for s in report['samples']:
                assert int(s['received_mono_ns'])<int(s['deadline_mono_ns'])
                assert abs(float(s['remaining_ms'])-(450-s['wall_ms'] if s['turn_id'] in ('1','2') else states[1 if s['side']=='red' else 2][s['side']]-s['wall_ms']))<0.00001
            checked('worker_nanosecond_accounting')
        finally:
            for worker in workers:worker.close()
            store.db.close()
    # Full public-duration benchmark + match using the unchanged SDK v2 format.
    with tempfile.TemporaryDirectory(prefix='bank-suite-',dir=output) as folder:
        store=Store(folder,recover_jobs=False);service=Service(store,start_worker=False)
        import io,zipfile,uuid
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:z.writestr('bot.cpp',(ROOT/'sdk'/'bot.cpp').read_bytes())
        sub=service.submit('proof','v1.0.0','cpp17',archive.getvalue(),uuid.uuid4().hex)
        try:
            for ms in (60000,300000):
                bench=service.enqueue('proof','benchmark',{'submission_id':sub['id'],'match_ms':ms},uuid.uuid4().hex)
                service.execute(bench);bench=store.get(bench['id'])
                assert bench['status']=='completed' and len(bench['report']['samples'])==72
                assert 0<bench['report']['clocks']['red']<ms
                checked(f'72_sample_benchmark_{ms//1000}s',remaining_ms=bench['report']['clocks']['red'])
                match=service.enqueue('proof','match',{'red_submission_id':sub['id'],'blue_submission_id':sub['id'],
                    'match_ms':ms,'delegate_timeout_claims':True},uuid.uuid4().hex)
                service.execute(match);match=store.get(match['id'])
                assert match['status']=='completed' and match['report']['result']
                assert all(0<x<ms for x in match['report']['clocks'].values())
                checked(f'full_classic_match_{ms//1000}s',clocks=match['report']['clocks'],result=match['report']['result'])
        finally:store.db.close()
    (output/'time-bank-latest.json').write_text(json.dumps({'image_id':image,'checks':checks},indent=2))


if __name__=='__main__':main()
