"""Reproducible 5-scenario comparison of the owner-approved 23 October Elo rules.

Only arena.elo performs pairing, expected scores and rating updates. No network/bots.
Official game counts exclude the shared 30-round ladder warmup.
"""
import argparse
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'arena-backend'))
from arena.elo import INITIAL, apply_round, expected, pair, top_group, general_pair, round_robin_schedule, round_robin_rank
from arena.protocol import Fault


LEGACY_GROUP_K = ((20,32),(60,16),(None,12))
LAWS = ("old", "new", "round_robin")


def play_round(ratings, strengths, rng, counts, changes, meetings, recent, mode, schedule=LEGACY_GROUP_K):
    if mode=='general':
        pairs,bye,radius=general_pair(ratings,meetings=meetings)
    else:
        pairs,bye=pair(ratings,meetings=meetings,recent=recent,official=mode=='old')
        radius=10
    assert bye is None
    games=[]
    for a,b in pairs:
        for leg,(red,blue) in enumerate(((a,b),(b,a))):
            p=expected(strengths[red],strengths[blue])
            draw=.8*p*(1-p)
            value=rng.random()
            score=1 if value<p-draw/2 else .5 if value<p+draw/2 else 0
            games.append(dict(id=f'{a}/{b}/{leg}',red=red,blue=blue,red_score=score,status='completed'))
        meetings[a,b]+=1
        recent[a].append(b); recent[b].append(a)
    result=apply_round(ratings,games,counts,changes,official=24 if mode=='old' else None if mode=='ladder' else schedule)
    return *result,len(games),radius


def run_trial(scenario,seed,existing=None):
    start=time.monotonic()
    rng=random.Random(seed)
    n=200 if scenario=='S5' else 1000
    names=[f'team-{i:04d}' for i in range(n)]
    if scenario=='S3':
        distributions=[(1300,150)]*600+[(1600,150)]*300+[(1900,200)]*100
        rng.shuffle(distributions)
    else:
        distributions=[(1500,400 if scenario=='S2' else 200)]*n
    strengths={team:rng.gauss(*d) for team,d in zip(names,distributions)}
    late=set(rng.sample(names,int(n*.3))) if scenario=='S4' else set()
    ladder_strength={team:value-rng.uniform(100,400) if team in late else value for team,value in strengths.items()}
    truth=set(sorted(names,key=lambda t:(-strengths[t],t))[:16])
    ratings=dict.fromkeys(names,INITIAL)
    counts,changes={},{}
    recent={t:deque(maxlen=3) for t in names}; meetings=Counter()
    for _ in range(30):
        ratings,counts,changes,_,_=play_round(ratings,ladder_strength,rng,counts,changes,meetings,recent,'ladder')
    warm=dict(ratings); random_state=rng.getstate(); results=dict(existing or {})
    for law in LAWS:
        if law in results:continue
        rng.setstate(random_state)
        ratings=dict(warm); counts={}; changes={}; total=0; widened=[]
        recent={t:deque(maxlen=3) for t in names}; meetings=Counter()
        group=None; general_top=None
        stages=[('old',30)] if law=='old' else [('general',20),('top',80)] if law=='new' else [('general',20)]
        for mode,rounds in stages:
            if mode=='top':
                group=top_group(ratings)
                recent={t:deque(maxlen=3) for t in group}; meetings=Counter()
            for number in range(rounds):
                active=ratings if group is None else {t:ratings[t] for t in group}
                try:
                    updated,counts,changes,games,radius=play_round(active,strengths,rng,counts,changes,meetings,recent,mode,schedule=True if law=='round_robin' else LEGACY_GROUP_K)
                except Fault as exc:
                    order=sorted(active,key=lambda t:(-active[t],t))
                    isolated=[]
                    for i,team in enumerate(order):
                        neighbors=[other for other in order[max(0,i-10):i+11] if other!=team]
                        if mode=='general' and all(meetings[tuple(sorted((team,other)))] for other in neighbors):
                            isolated.append(dict(team=team,rank=i+1,opponents=neighbors))
                    # Fault has controller-specific constructor arguments, not pickle args.
                    raise RuntimeError(json.dumps(dict(scenario=scenario,seed=seed,law=law,stage=mode,
                        round=number+1,detail=exc.detail,isolated=isolated))) from exc
                if mode=='general' and radius>10:
                    widened.append(dict(round=number+1,neighbor_ranks=radius))
                ratings.update(updated); total+=games
                if mode in ('old','general') and number==19:
                    general_top=set(top_group(ratings))
        if law=='round_robin':
            group=top_group(ratings)
            games=[]
            for i,(red,blue) in enumerate(round_robin_schedule(group)):
                p=expected(strengths[red],strengths[blue]);draw=.8*p*(1-p);value=rng.random()
                score=1 if value<p-draw/2 else .5 if value<p+draw/2 else 0
                games.append(dict(id=f'{i:08d}',red=red,blue=blue,red_score=score,status='completed'))
            total+=len(games)
            selected={r['team_id'] for r in round_robin_rank(group,games)[:16]}
        else:
            selected=set(top_group(ratings,16))
        excluded=set(names)-selected
        gap=max(strengths[t] for t in excluded)-min(strengths[t] for t in selected)
        results[law]=dict(games=total,widened_rounds=widened,widened_count=len(widened),max_rank_band=max([10]+[r['neighbor_ranks'] for r in widened]),wrong100=gap>=100,wrong150=gap>=150,
                          true16_selected=len(truth&selected),true16_in_general64=len(truth&general_top),
                          late_true16=len(truth&late),late_selected=len(truth&late&selected),
                          ontime_true16=len(truth-late),ontime_selected=len((truth-late)&selected))
    return dict(scenario=scenario,seed=seed,results=results,elapsed_s=round(time.monotonic()-start,3))


def summarize(rows):
    summary={}
    for scenario in sorted(set(r['scenario'] for r in rows)):
        summary[scenario]={}
        for law in LAWS:
            values=[r['results'][law] for r in rows if r['scenario']==scenario and law in r['results']]
            if not values:continue
            total=lambda key:sum(v[key] for v in values)
            late=total('late_true16'); ontime=total('ontime_true16')
            summary[scenario][law]=dict(seeds=len(values),games=sorted(set(v['games'] for v in values)),
                mean_widened_rounds=statistics.mean(v['widened_count'] for v in values),maximum_rank_band=max(v['max_rank_band'] for v in values),
                wrong100_pct=100*total('wrong100')/len(values),wrong150_pct=100*total('wrong150')/len(values),
                mean_true16_selected=statistics.mean(v['true16_selected'] for v in values),
                mean_true16_in_general64=statistics.mean(v['true16_in_general64'] for v in values),
                late_selection_pct=100*total('late_selected')/late if late else None,
                ontime_selection_pct=100*total('ontime_selected')/ontime,
                late_true16=late,ontime_true16=ontime)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds',type=int,default=100)
    parser.add_argument('--first-seed',type=int,default=20261023)
    parser.add_argument('--scenarios',default='S1,S2,S3,S4,S5')
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume-from',type=Path,help='Resume a saved run after checking numerical equivalence; versions stay in the report')
    args=parser.parse_args()
    scenarios=args.scenarios.split(',')
    if args.seeds<1 or args.workers<1 or not set(scenarios)<=set(('S1','S2','S3','S4','S5')):
        parser.error('Positive seeds/workers and S1..S5 required')
    started=time.monotonic(); rows=[]
    import networkx
    report=dict(model=dict(ladder_rounds=30,official_old='30 rounds, K24, top16',
                           official_new='20 general; top64 x80; K32 first20 /16 through60 /12 thereafter; top16',
                           official_round_robin='20 general unless <=64 teams; top64 double round robin points; general K32 first20 /16 thereafter; top16',
                           neighbor_ranks=10,draw_probability='.8*p*(1-p)',late_fraction=.3,late_deficit=[100,400],
                           pairing='deterministic nearest-first perfect matching, blossom fallback'),
                runtime=dict(python=platform.python_version(),networkx=networkx.__version__),
                code_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (Path(__file__).resolve(),ROOT/'arena-backend/arena/elo.py')},trials=rows)
    tasks={(s,seed) for s in scenarios for seed in range(args.first_seed,args.first_seed+args.seeds)}
    base_elapsed=0
    versions=[report['code_sha256']]
    prior_rows={}
    if args.resume_from:
        prior=json.loads(args.resume_from.read_text(encoding='utf-8'))
        prior_model=dict(prior['model']);current_model=dict(report['model'])
        # The historical comparators are unchanged; only the new law was added.
        prior_model.pop('official_round_robin',None);current_model.pop('official_round_robin',None)
        if prior_model!=current_model or prior['runtime']!=report['runtime']:
            parser.error('Resume comparator model/runtime differs')
        versions=prior.get('code_versions',[prior['code_sha256']])[:]
        if report['code_sha256'] not in versions:versions.append(report['code_sha256'])
        for row in prior['trials']:
            key=(row['scenario'],row['seed'])
            if key in prior_rows or key not in tasks:
                parser.error('Resume contains duplicate or unexpected seeds/scenarios')
            row=dict(row)
            row.setdefault('result_code_versions',{law:row.get('code_version',0) for law in row['results']})
            prior_rows[key]=row
        base_elapsed=prior['elapsed_s']
    version=versions.index(report['code_sha256'])
    report['code_versions']=versions
    report['resume_note']='Saved A/B comparator results retain their source versions. Round robin is a new independently seeded continuation of the same ladder; old B explicitly retains K32/16/12.'
    rows.extend(r for r in prior_rows.values() if set(LAWS)<=set(r['results']))
    tasks-={(r['scenario'],r['seed']) for r in rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        rows.sort(key=lambda r:(r['scenario'],r['seed']))
        report.update(summary=summarize(rows),elapsed_s=round(base_elapsed+time.monotonic()-started,3))
        temporary=args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report,indent=2),encoding='utf-8')
        temporary.replace(args.output)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(run_trial,s,seed,prior_rows.get((s,seed),{}).get('results')):(s,seed) for s,seed in sorted(tasks)}
        for future in as_completed(futures):
            try: row=future.result()
            except Exception:
                print('FAILED',futures[future],flush=True)
                for f in futures:f.cancel()
                raise
            old=prior_rows.get(futures[future],{})
            row['result_code_versions']=dict(old.get('result_code_versions',{}))
            for law in row['results']:row['result_code_versions'].setdefault(law,version)
            rows.append(row)
            save()
            print(json.dumps(row),flush=True)
    save()
    print(json.dumps(report['summary'],indent=2),flush=True)


if __name__=='__main__':main()
