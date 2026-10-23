"""Measure real native Windows matches; no mail, GitHub or production data.

Run from arena-backend in WSL, using the existing Windows grader toolchain.
Reports include cold build and warm, sequential, one-slot service time separately.
"""
import argparse
import io
import sqlite3
import json
from pathlib import Path
import statistics
import time
import zipfile

from grader_cluster import Cluster, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'test-results/m1/g7b/windows-timing.json')
    args=parser.parse_args()
    cluster=Cluster(); cluster.results['mode']='elo-timing'
    output=args.output
    output.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    try:
        cluster.start_hub(); cluster.grader('elo-timing-win','windows',3)
        cluster.wait_online(['elo-timing-win'])
        team=cluster.team('Windows timing fixture','timing-1')
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            z.write(ROOT/'sdk/bot.cpp','bot.cpp')
            z.write(ROOT/'sdk/json.hpp','json.hpp')
        source=cluster.api(team,'POST','/submissions',expected=201,content=raw.getvalue(),
                           headers={'Content-Type':'application/zip','X-Submission-Version':'v1.0.0'})
        for ms in (10000,60000):
            for number in range(11):
                before=time.monotonic()
                job=cluster.api(team,'POST','/matches',expected=202,json=dict(
                    red_submission_id=source['id'],blue_submission_id=source['id'],match_ms=ms,delegate_timeout_claims=True))
                job=cluster.wait_job(team,job['id'])
                assert job['status']=='completed',(job.get('error'),job['id'])
                report=cluster.api(team,'GET','/jobs/'+job['id']+'/report')
                lease=cluster.lease_row(job['id'])
                with sqlite3.connect(f'file:{cluster.data / "arena.sqlite"}?mode=ro',uri=True) as db:
                    timing=db.execute('SELECT duration_ms FROM duration_samples WHERE job=?',(job['id'],)).fetchone()
                assert timing is not None and timing[0]==lease['ended_ms']-lease['created_ms']
                row=dict(match_ms=ms,number=number,cold_build=ms==10000 and number==0,
                         observed_seconds=round(time.monotonic()-before,3),
                         leased_seconds=(lease['ended_ms']-lease['created_ms'])/1000,
                         result=report.get('result'),metrics=report.get('sandbox_metrics'))
                rows.append(row); print(json.dumps(row),flush=True)
        summaries={}
        for ms in (10000,60000):
            values=[r['leased_seconds'] for r in rows if r['match_ms']==ms and not r['cold_build']]
            summaries[ms]=dict(games=len(values),mean=statistics.mean(values),median=statistics.median(values),maximum=max(values))
        output.write_text(json.dumps(dict(backend='windows',slots=1,cpu=3,bot='sdk/bot.cpp (template strategy)',
            games=rows,summary=summaries,timing_samples_verified=len(rows)),ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(summaries),flush=True)
    finally:
        team.close() if 'team' in locals() else None
        cluster.stop()


if __name__=='__main__':main()
