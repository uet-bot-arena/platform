"""Recompute the ordered JSON results exported by the hub; no source or replay needed."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'arena-backend'))
from arena.elo import apply_round,rank,round_robin_rank,select
from arena.protocol import strict_json


def recompute(record):
    ratings=dict(record['initial_ratings'])
    if not ratings or any(type(r) not in (int,float) or not math.isfinite(r) for r in ratings.values()):
        raise ValueError('Initial ratings must be finite numbers')
    ladder,official,changes={},{},{}
    seen=set(); started=False; official_games=[]; standings=None
    for row in record['rounds']:
        if row['id'] in seen or row['phase'] not in ('ladder','general','top','round_robin'):
            raise ValueError('Duplicate round or unknown phase')
        seen.add(row['id'])
        games=row['games']
        if row['phase']=='ladder':
            if started:
                raise ValueError('Ladder cannot follow official rounds in the export')
            for team in row.get('changed_teams',[]):
                if team not in ratings:
                    raise ValueError('Unknown changed team')
                changes[team]=10
            ratings,ladder,changes=apply_round(ratings,games,ladder,changes)
        elif row['phase']=='round_robin':
            if standings is not None:raise ValueError('Duplicate round-robin phase')
            started=True
            group={t:ratings[t] for t in row['teams']}
            standings=round_robin_rank(group,games)
        else:
            if standings is not None:raise ValueError('Elo games cannot follow round robin')
            started=True
            ratings,official,_=apply_round(ratings,games,official,official=record.get('official_k',True))
            official_games.extend(games)
    standings=standings if standings is not None else rank(ratings,official_games)
    excluded=set(record.get('excluded_teams',[]))
    previous=None;number=0;position=0
    for row in standings:
        if row['team_id'] in excluded:
            row.update(excluded=True,rank=None)
            continue
        position+=1;original=row['rank']
        if original!=previous:number=position
        row['rank']=number;previous=original
    result=dict(ratings=ratings,ladder_played=ladder,official_played=official,change_remaining=changes,standings=standings)
    if 'finalist_count' in record:
        result['finalists']=select([r for r in standings if not r.get('excluded')],record['finalist_count'])
    return result



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=recompute(strict_json(args.input.read_bytes()))
    value=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.output:
        args.output.write_text(value,encoding='utf-8')
    else:
        print(value,end='')


if __name__=='__main__':main()
