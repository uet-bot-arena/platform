"""Measure 500 ms/sample checks on Windows: SDK C++/C/Python and alpha-beta250.

Four new sources and one cached-source check per bot. One slot; no mail/GitHub.
"""
import io
import json
from pathlib import Path
import statistics
import zipfile

from grader_cluster import Cluster,ROOT


def main():
    cluster=Cluster();cluster.results['mode']='benchmark-timing'
    output=ROOT/'test-results/m1/g8/windows-check-timing.json';output.parent.mkdir(parents=True,exist_ok=True)
    result=dict(backend='windows',slots=1,cpu=3,checks=[],summary={})
    team=None
    try:
        cluster.start_hub();cluster.grader('check-timing-win','windows',3);cluster.wait_online(['check-timing-win'])
        team=cluster.team('Bounded check timing','check-timing-1')
        comp=cluster.api(cluster.admin,'POST','/admin/competitions',expected=201,json=dict(title='Independent check clock',max_teams=2,match_ms=60000))
        cluster.api(cluster.admin,'POST','/admin/competitions/'+comp['id']+'/open')
        bots=(('cpp17','bot.cpp','sdk/bot.cpp'),('c11','bot.c','sdk/bot.c'),('python3','bot.py','sdk/bot.py'),('alpha250','bot.cpp','examples/alphabeta/bot.cpp'))
        for index,(label,name,path) in enumerate(bots):
            runtime='cpp17' if label=='alpha250' else label
            for number in range(5):
                if number<4:
                    source=(ROOT/path).read_text(encoding='utf-8')+('\n#' if runtime=='python3' else '\n//')+' bounded check fixture '+str(number)+'\n'
                    raw=io.BytesIO()
                    with zipfile.ZipFile(raw,'w',zipfile.ZIP_DEFLATED) as bundle:
                        bundle.writestr(name,source)
                        if runtime=='cpp17':bundle.write(ROOT/'sdk/json.hpp','json.hpp')
                        if runtime=='c11':bundle.write(ROOT/'sdk/jsmn.h','jsmn.h')
                    sub=cluster.api(team,'POST','/submissions',expected=201,content=raw.getvalue(),headers={'Content-Type':'application/zip','X-Submission-Version':f'v1.{index}.{number}','X-Arena-Runtime':runtime})
                job=cluster.api(team,'POST','/benchmarks',expected=202,json=dict(submission_id=sub['id'],competition_id=comp['id']))
                job=cluster.wait_job(team,job['id'],timeout=300)
                assert job['status']=='completed' and job['report']['passed'],job.get('error')
                report=job['report'];assert report['clock_scope']=='per_sample_500_ms' and report['profile']['turn_ms']==500
                assert len(report['samples'])==72 and all(s['wall_ms']<500 for s in report['samples'])
                cluster.api(team,'POST','/competitions/'+comp['id']+'/entry',json=dict(submission_id=sub['id'],benchmark_id=job['id'],delegate_timeout_claims=True))
                lease=cluster.lease_row(job['id'])
                row=dict(bot=label,runtime=runtime,cold_source=number<4,leased_seconds=(lease['ended_ms']-lease['created_ms'])/1000,
                         samples=72,max_sample_ms=max(s['wall_ms'] for s in report['samples']),profile_id=report['profile']['id'])
                result['checks'].append(row);print('PASS',json.dumps(row),flush=True)
        for label,_,_ in bots:
            values=[r['leased_seconds'] for r in result['checks'] if r['bot']==label and r['cold_source']]
            result['summary'][label]=dict(cold_checks=len(values),mean=statistics.mean(values),maximum=max(values),warm_seconds=next(r['leased_seconds'] for r in result['checks'] if r['bot']==label and not r['cold_source']))
        print('ALL 20 NATIVE BOUNDED CHECKS PASSED',json.dumps(result['summary']),flush=True)
    finally:
        if team:team.close()
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        cluster.stop()


if __name__=='__main__':main()
