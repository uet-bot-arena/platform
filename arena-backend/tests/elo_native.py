"""20-team Windows acceptance with real builds/checks/games and frozen source SHAs.

--scheduler: real deadline and production scheduler, 3 general rounds, top8 double
round robin, select4 (116 games). Default: accelerated 3-round ladder prelude and
explicit round dispatch (176 games). BTC fixtures replace GitHub delivery; no mail.
"""
import io
import argparse
import json
from pathlib import Path
import statistics
import threading
import time
import uuid
import zipfile
import hashlib
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

import uvicorn
from arena.conf import Secrets
from arena.config import match_profile
from arena.elo import rank,select,top_group,round_robin_rank
from arena.ladder_clock import boundary
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.elo_rounds import RoundBook
from arena_api.settings import Settings
from grader_cluster import Cluster,ROOT,PASSWORD


def source_fixture(number):
    source=(ROOT/'sdk/bot.cpp').read_text(encoding='utf-8')
    source=source.replace('#include <algorithm>','#include <algorithm>\n#include <functional>',1)
    source=source.replace('using json = nlohmann::json;',f'using json = nlohmann::json;\nstatic constexpr unsigned M1_VARIANT = {number};',1)
    source=source.replace('        if (to == base) score += 10000;',
        '        score += static_cast<int>(std::hash<std::string>{}(move.dump() + std::to_string(state.value("ply",0)) + std::to_string(M1_VARIANT)) % (2 + M1_VARIANT % 5));\n        if (to == base) score += 10000;',1)
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('bot.cpp',source);z.write(ROOT/'sdk/json.hpp','json.hpp')
    return raw.getvalue(),hashlib.sha1(source.encode()).hexdigest()


def run_scheduler(cluster,app,entries,item,result,output):
    comp=app.state.competitions;book=comp.elo.book;store=app.state.store;ident=item['id']
    with store.transaction():
        for team,entry in entries.items():
            saved=dict(entry);saved.pop('bot_hash',None)
            store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(ident,team,entry['submission_id'],entry['benchmark_id'],json.dumps(saved),time.time_ns()//1_000_000))
        store.db.execute('UPDATE competitions SET submission_deadline=? WHERE id=?',(time.time_ns()//1_000_000-1,ident))
    seen=set();reported=set();until=time.monotonic()+1200
    while time.monotonic()<until:
        for row in comp.rows('SELECT id FROM elo_rounds WHERE competition_id=? ORDER BY started_ms',(ident,)):
            round_item=book.get(row['id'])
            for game in book.games(row['id']):
                if game['status']!='completed' or game['id'] in seen:continue
                assert game['red_sha']==entries[game['red_team_id']]['github_sha']
                assert game['blue_sha']==entries[game['blue_team_id']]['github_sha']
                job=store.get(game['current_job_id'])
                assert comp.valid_report(job,item['config'])
                timing=comp.one('SELECT duration_ms FROM duration_samples WHERE job=?',(job['id'],))
                assert timing is not None
                result['games'].append(dict(phase=round_item['phase'],round=round_item['number'],seconds=timing['duration_ms']/1000,result=json.loads(game['result'])))
                seen.add(game['id'])
            if round_item['status']=='completed' and row['id'] not in reported:
                result['rounds'].append(dict(phase=round_item['phase'],number=round_item['number'],games=len(book.games(row['id'])),wall_seconds=(round_item['completed_ms']-round_item['started_ms'])/1000))
                print('PASS SCHEDULER',json.dumps(result['rounds'][-1]),flush=True);reported.add(row['id'])
        current=comp.get(ident)
        assert current['status']!='paused',current['config'].get('elo_error')
        if current['status']=='completed' and len(seen)==116 and len(reported)==4:break
        time.sleep(.2)
    assert current['status']=='completed' and len(seen)==116
    assert [r['phase'] for r in result['rounds']]==['general']*3+['round_robin']
    rr=comp.one("SELECT id FROM elo_rounds WHERE competition_id=? AND phase='round_robin'",(ident,))
    state=book.get(rr['id'])
    assert state['snapshot']['ratings']==state['after_state']['ratings']
    result['finalists']=current['config']['finalists'];assert len(result['finalists'])==4
    sys.path.insert(0,str(ROOT.parent/'tools'))
    from recompute_elo import recompute
    exported=book.export(ident,'official');replayed=recompute(exported)
    assert replayed['finalists']==result['finalists']
    stored={r['team_id']:r['rating'] for r in comp.rows("SELECT * FROM elo_ratings WHERE competition_id=? AND stage='official'",(ident,))}
    assert replayed['ratings']==stored
    output.with_name('native20-scheduler-export.json').write_text(json.dumps(exported,indent=2),encoding='utf-8')
    result['checks'].extend(['real wall-clock deadline auto lock/start','116/116 real native games dispatched/reconciled by production scheduler','3 general rounds +top8 double round robin56 games; selected4','Elo frozen in round robin; export ratings and finalist seeds match hub'])
    print('ALL NATIVE SCHEDULER CHECKS PASSED',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scheduler',action='store_true');args=parser.parse_args()
    cluster=Cluster();cluster.results['mode']='elo-scheduler' if args.scheduler else 'elo-native'
    result=dict(backend='windows',slots=2,cpus=[0,2],teams=20,source_mode='BTC synthetic fixtures; source SHA labels, no GitHub network',
                calendar='accelerated Vietnam fixed boundaries; real Windows execution',rounds=[],games=[],checks=[])
    output=ROOT/('test-results/m1/g7b/native20-scheduler.json' if args.scheduler else 'test-results/m1/g7b/native20.json');output.parent.mkdir(parents=True,exist_ok=True)
    clients=[];server=None;thread=None
    try:
        store=Store(cluster.data);Accounts(store).bootstrap_admin('organizer',PASSWORD);store.db.close()
        app=create_app(Settings(data=cluster.data,origin=cluster.origin,secrets=Secrets({})))
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=cluster.port,log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        until=time.monotonic()+30
        while not server.started and time.monotonic()<until:time.sleep(.05)
        assert server.started
        login=cluster.api(cluster.admin,'POST','/auth/login',json=dict(username='organizer',password=PASSWORD))
        cluster.admin.headers['X-CSRF-Token']=login['csrf_token']
        cluster.grader('elo-native-win','windows','0,2',slots=2);cluster.wait_online(['elo-native-win'])
        item=cluster.api(cluster.admin,'POST','/admin/competitions',expected=201,
            json=dict(title='Windows 20-team Elo acceptance',max_teams=20,match_ms=60000,grader_platform='windows',**(dict(format='elo',submission_deadline=9_000_000_000_000,official=dict(general_rounds=3,group_size=8,finalists=4)) if args.scheduler else {})))
        ident=item['id'];cluster.api(cluster.admin,'POST','/admin/competitions/'+ident+'/open')
        comp=app.state.competitions;book=RoundBook(comp);store=app.state.store;service=app.state.service
        pending=[]
        for i in range(20):
            client=cluster.team('Native Elo '+str(i),'elo-native-'+str(i));clients.append(client)
            raw,sha=source_fixture(i)
            sub=cluster.api(client,'POST','/submissions',expected=201,content=raw,
                headers={'Content-Type':'application/zip','X-Submission-Version':'v1.0.0'})
            check=cluster.api(client,'POST','/benchmarks',expected=202,json=dict(submission_id=sub['id'],competition_id=ident))
            pending.append((client,sub,check,sha))
        entries={}
        for client,sub,check,sha in pending:
            job=cluster.wait_job(client,check['id'],timeout=900)
            if job['status']!='completed' or not job['report']['passed']:
                (output.parent/'native20-failed-check.json').write_text(json.dumps(job,indent=2),encoding='utf-8')
            assert job['status']=='completed' and job['report']['passed'],job.get('error')
            team=sub['team_id']
            entries[team]=dict(comp.qualification(item['config'],team,sub['id'],check['id']),github_sha=sha,bot_hash=sub['source']['zip_sha256'])
        result['checks'].append('20/20 real native 72-sample checks passed')
        print('PASS 20/20 Windows checks',flush=True)
        if args.scheduler:
            result['calendar']='real scheduler and deadline; native source fixtures replace GitHub delivery'
            run_scheduler(cluster,app,entries,item,result,output)
            return
        fake_now=boundary(time.time_ns()//1_000_000,next=True)

        def run_round(phase,number,participants):
            nonlocal fake_now
            cfg=dict(item['config'])
            if phase=='ladder':cfg['profile']=match_profile(10000).public()
            started=time.monotonic()
            rid=book.start(ident,phase,number,fake_now,participants,config=cfg)
            for game in book.games(rid):
                pinned=[json.loads(game['red_entry']),json.loads(game['blue_entry'])]
                job=comp.match_job(item,pinned,game['id'],1,config=cfg)
                job['official'].update(phase=phase,round_id=rid)
                with store.transaction():
                    store.db.execute('INSERT INTO documents VALUES(?,?,?,?)',(job['id'],'job',job['team_id'],json.dumps(job)))
                    store.db.execute("UPDATE elo_games SET status='queued',current_job_id=? WHERE id=?",(job['id'],game['id']))
                    store.db.execute("INSERT INTO elo_attempts VALUES(?,?,?,'queued',NULL,?)",(game['id'],1,job['id'],time.time_ns()//1_000_000))
                    store.db.execute('INSERT INTO events VALUES(?,?,?)',(job['id'],1,json.dumps(dict(seq=1,type='queued',server_unix_ms=time.time_ns()//1_000_000,data=dict(kind='match')))))
                service.pending.put(job['id'],official=phase!='ladder',kind='match',platform=cfg['grader_platform'],runtime_id=cfg['runtime_id'])
            until=time.monotonic()+900
            remaining={g['id']:g for g in book.games(rid)}
            while remaining and time.monotonic()<until:
                for gid,game in list(remaining.items()):
                    job=store.get(game['current_job_id'])
                    if job['status'] not in ('completed','failed','aborted'):continue
                    report=job.get('report') or {}
                    assert job['status']=='completed' and report.get('eligible_for_tournament') is True,(phase,number,job.get('error'))
                    assert report['profile']==cfg['profile'] and report['runtime_id']==cfg['runtime_id']
                    assert game['red_sha']==participants[game['red_team_id']]['github_sha']
                    assert game['blue_sha']==participants[game['blue_team_id']]['github_sha']
                    with store.lock,store.db:
                        timing=store.db.execute('SELECT duration_ms FROM duration_samples WHERE job=?',(job['id'],)).fetchone()
                        assert timing is not None
                        store.db.execute("UPDATE elo_games SET status='completed',result=? WHERE id=?",(json.dumps(report['result']),gid))
                        store.db.execute("UPDATE elo_attempts SET status='completed' WHERE job_id=?",(job['id'],))
                    result['games'].append(dict(phase=phase,round=number,seconds=timing[0]/1000,plies=len(report.get('moves',[])),result=report['result']))
                    del remaining[gid]
                time.sleep(.1)
            assert not remaining,(phase,number,'timeout')
            elapsed=time.monotonic()-started
            fake_now+=max(1,int(elapsed*1000))
            book.finish(rid,fake_now)
            result['rounds'].append(dict(phase=phase,number=number,games=len(book.games(rid)),wall_seconds=round(elapsed,3),neighbor_ranks=book.get(rid)['neighbor_ranks']))
            print('PASS',json.dumps(result['rounds'][-1]),flush=True)
            if phase=='ladder':fake_now=boundary(fake_now,next=True)

        for n in range(1,4):run_round('ladder',n,entries)
        initial=book.seed_official(ident,entries,fake_now)
        for n in range(1,4):run_round('general',n,entries)
        ratings={r['team_id']:r['rating'] for r in comp.rows("SELECT * FROM elo_ratings WHERE competition_id=? AND stage='official'",(ident,))}
        games=[g for row in comp.rows("SELECT id FROM elo_rounds WHERE competition_id=? AND phase='general'",(ident,)) for g in book.scores(book.games(row['id']))]
        group={t:entries[t] for t in top_group(ratings,8)}
        result['group_size']=len(group)
        frozen=dict(ratings)
        run_round('round_robin',1,group)
        rr=comp.one("SELECT id FROM elo_rounds WHERE competition_id=? AND phase='round_robin'",(ident,))
        standings=book.get(rr['id'])['after_state']['standings']
        result['finalists']=select(standings,4)
        ratings={r['team_id']:r['rating'] for r in comp.rows("SELECT * FROM elo_ratings WHERE competition_id=? AND stage='official'",(ident,))}
        assert ratings==frozen
        assert len(result['games'])==176 and len(result['finalists'])==4
        import sys
        sys.path.insert(0,str(ROOT.parent/'tools'))
        from recompute_elo import recompute
        for stage in ('ladder','official'):
            exported=book.export(ident,stage);actual=recompute(exported)
            stored={r['team_id']:r['rating'] for r in comp.rows('SELECT * FROM elo_ratings WHERE competition_id=? AND stage=?',(ident,stage))}
            assert actual['ratings']==stored
            if stage=='official':assert actual['standings']==standings
            (output.parent/f'native20-{stage}-export.json').write_text(json.dumps(exported,indent=2),encoding='utf-8')
        result['checks'].extend(['176/176 real Windows games completed with pinned source SHA','3 ladder +3 general; top8 double round robin56 games; selected4; official Elo frozen','both exports recompute exactly to stored hub ratings'])
        result['summary']={phase:dict(games=len(values),mean=statistics.mean(values),median=statistics.median(values),maximum=max(values))
            for phase in ('ladder','general','round_robin') if (values:=[g['seconds'] for g in result['games'] if g['phase']==phase])}
        print('ALL 20-TEAM NATIVE CHECKS PASSED',json.dumps(result['summary']),flush=True)
    finally:
        for client in clients:client.close()
        if server:server.should_exit=True
        if thread:thread.join(30)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        cluster.stop()


if __name__=='__main__':main()
