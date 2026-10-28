"""One head-to-head pass within each original points tie, never recursive."""
import json

def head_to_head(scores, fixtures, scoring):
    for row in scores.values():row['head_to_head']=0
    for fixture in fixtures:
        red,blue=fixture['red_team_id'],fixture['blue_team_id']
        if scores[red]['points']!=scores[blue]['points']:continue
        result=json.loads(fixture['result']) if isinstance(fixture['result'],str) else fixture['result']
        for side,team in [('red',red),('blue',blue)]:
            outcome='win' if result['winner']==side else 'draw' if result['winner'] is None and result['reason']!='double_startup_failure' else 'loss'
            scores[team]['head_to_head']+=scoring[outcome]

def group_order(rows, online):
    return [dict(row,rank=i) for i,row in enumerate(sorted(rows,key=lambda r:(-r['points'],-r['head_to_head'],-r['wins'],online[r['team_id']])),1)]

def pair_winner(rows, groups, online):
    a,b=rows
    criteria=[('hơn điểm',lambda r:r['points']),('đứng cao hơn ở vòng bảng',lambda r:-groups[r['team_id']]['rank']),
              ('điểm vòng bảng',lambda r:groups[r['team_id']]['points']),('hạng online',lambda r:-online[r['team_id']])]
    for reason,key in criteria:
        if key(a)!=key(b):
            winner,loser=(a,b) if key(a)>key(b) else (b,a)
            return dict(winner=winner['team_id'],loser=loser['team_id'],reason=reason)
    raise ValueError('Finals require distinct online ranks')
