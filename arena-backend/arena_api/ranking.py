"""One head-to-head pass within each original points tie, never recursive."""
import json
import hashlib

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

def pair_winner(rows, games, competition_id):
    a,b=rows
    def decided(winner,reason,detail=None):
        value=dict(winner=winner,loser=b['team_id'] if winner==a['team_id'] else a['team_id'],reason=reason)
        if detail is not None:value['detail']=detail
        return value
    if a['points']!=b['points']:return decided((a if a['points']>b['points'] else b)['team_id'],'hơn điểm')
    wins={r['team_id']:[] for r in rows};diff={r['team_id']:0 for r in rows}
    for game in games:
        if game['winner'] in ('red','blue'):wins[game[game['winner']+'_team_id']].append(game['plies'])
        for side,other in [('red','blue'),('blue','red')]:
            diff[game[side+'_team_id']]+=game['pieces'][side]-game['pieces'][other]
    if all(len(wins[r['team_id']])==1 for r in rows):
        x,y=wins[a['team_id']][0],wins[b['team_id']][0]
        if x!=y:return decided(a['team_id'] if x<y else b['team_id'],'thắng nhanh hơn',dict(winner_plies=min(x,y),loser_plies=max(x,y)))
    x,y=diff[a['team_id']],diff[b['team_id']]
    if x!=y:return decided(a['team_id'] if x>y else b['team_id'],'hơn hiệu số quân',dict(winner_diff=max(x,y),loser_diff=min(x,y)))
    winner=sorted(wins)[int(hashlib.sha256(competition_id.encode()).hexdigest()[:8],16)%2]
    return decided(winner,'bốc thăm')
