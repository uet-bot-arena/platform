"""Same 100 ms alpha-beta, K pinned workers; actual sandbox node retention."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import engine,sandbox
from arena.config import request_profile
from arena.service import fixtures


def main():
    profile=request_profile({'match_ms':60000})
    image=sandbox.image_id('uet-arena-worker:clock-v2')
    cpus=sorted(os.sched_getaffinity(0))[2:] or sorted(os.sched_getaffinity(0))
    cases=fixtures()[::2]
    report={'image_id':image,'scope':'same alpha-beta, 100 ms wall per turn, 12 positions per worker, separate CPU per worker','runs':[]}
    with tempfile.TemporaryDirectory(prefix='arena-search-scaling-') as tmp:
        root=Path(tmp);source=root/'source';source.mkdir()
        (source/'bot.cpp').write_text((ROOT/'examples/reference-bots/bot.cpp').read_text())
        artifact=root/'artifact';result=sandbox.build(source,artifact,profile,image)
        assert result['ok'],result
        for count in (1,2,4,8,10):
            if count>len(cpus):continue
            ready=threading.Barrier(count)
            def run(slot):
                sandbox.controller_scope(root,cpus[slot])
                worker=sandbox.Worker(artifact,profile,image)
                try:
                    worker.start('scaling-'+str(slot),'red')
                    ready.wait(timeout=30)
                    started=time.perf_counter()
                    for index,case in enumerate(cases):
                        state=case['state']
                        result=worker.turn({'protocol_version':2,'type':'observation','match_id':'scaling-'+str(slot),
                            'turn_id':str(index),'state_version':state['ply'],'payload':engine.observation(state)})
                        assert result['message']['payload'] in engine.legal_moves(state)
                    elapsed=time.perf_counter()-started
                finally:
                    metrics=worker.close()
                stats=[json.loads(line) for line in metrics['stderr'].splitlines() if line.startswith('{')]
                assert len(stats)==len(cases)
                return {'cpu':cpus[slot],'elapsed_seconds':elapsed,'turns':stats}
            with ThreadPoolExecutor(count) as pool:rows=list(pool.map(run,range(count)))
            speed=statistics.median(s['nodes']/(s['search_ms']/1000) for row in rows for s in row['turns'])
            entry={'workers':count,'nodes_per_second_median':speed,'depth_median':statistics.median(s['depth'] for row in rows for s in row['turns']),
                   'aggregate_turns_per_second':count*len(cases)/max(r['elapsed_seconds'] for r in rows),'rows':rows}
            report['runs'].append(entry)
            print(count,round(speed),entry['depth_median'],flush=True)
    baseline=report['runs'][0]['nodes_per_second_median']
    for run in report['runs']:run['node_retention_vs_k1']=run['nodes_per_second_median']/baseline
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    (output/'search-scaling.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':main()
