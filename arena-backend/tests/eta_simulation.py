"""4,000 FIFO jobs with service times resampled from the real Windows checks."""
import heapq
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from arena.config import benchmark_profile
from arena.eta import Timing
from arena.store import Store


def main():
    source=ROOT/'test-results/m1/g8/windows-check-timing.json'
    measured=json.loads(source.read_text())
    samples=[r['leased_seconds'] for r in measured['checks'] if r['cold_source']]
    assert len(samples)==16
    rng=random.Random(20261024);slots=20;jobs=4000;free=[(0,i) for i in range(slots)];timeline=[]
    for i in range(jobs):
        start,slot=heapq.heappop(free);end=start+rng.choice(samples)
        heapq.heappush(free,(end,slot));timeline.append((end,start,f'eta-{i:04d}'))
    finish=max(x[0] for x in timeline);timeline.sort();cursor=0;errors=[];covered=0
    profile=benchmark_profile().public();runtime=dict(platform='windows',runtime_id='simulation-timing-fixture')
    base=1_792_000_000_000
    with tempfile.TemporaryDirectory(prefix='eta-simulation-') as tmp:
        store=Store(tmp,recover_jobs=False)
        try:
            timing=Timing(store)
            with store.transaction():
                store.db.executemany('INSERT INTO documents VALUES(?,?,?,?)',[(ident,'job','simulation',json.dumps(dict(id=ident,kind='benchmark',profile=profile))) for _,_,ident in timeline])
            for now in range(600,int(finish),5):
                with store.transaction():
                    while cursor<jobs and timeline[cursor][0]<=now:
                        end,start,ident=timeline[cursor]
                        timing.record(dict(id=ident,kind='benchmark',profile=profile),runtime,base+round(start*1000),base+round(end*1000));cursor+=1
                estimate=timing.estimate('benchmark',profile['id'],'windows',runtime['runtime_id'],jobs-cursor,slots,started=True,now_ms=base+now*1000)
                assert estimate is not None
                predicted=((jobs-cursor)+slots-1)//slots*estimate['mean_game_seconds']
                actual=finish-now
                errors.append(abs(predicted-actual)/actual*100)
                covered+=estimate['lower_seconds']<=actual<=estimate['upper_seconds']
        finally:store.db.close()
    result=dict(jobs=jobs,slots=slots,seed=20261024,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                source='16 real cold-source Windows checks; uniform resampling across four measured bot types',
                completed_seconds=round(finish,3),after_warmup_seconds=600,estimates=len(errors),median_error_pct=statistics.median(errors),
                p95_error_pct=sorted(errors)[int(.95*(len(errors)-1))],range_coverage_pct=100*covered/len(errors))
    assert result['median_error_pct']<20,result
    (ROOT/'test-results/m1/g8/eta-simulation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
