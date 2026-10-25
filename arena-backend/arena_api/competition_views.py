"""Read models share the scheduler's qualifications and the published standings."""
from collections import Counter
import json
import time

from fastapi import APIRouter, Depends, Request
from arena.protocol import Fault
from arena_api.dependencies import admin_principal, team_principal

router = APIRouter(prefix='/api/v1', tags=['competition-views'])


def competition_teams(comp, ident, team_id=None):
    item = comp.get(ident)
    elo = item['config']['format'] == 'elo'
    now = time.time_ns()//1_000_000
    active = {r['team_id'] for r in comp.rows(
        'SELECT DISTINCT team_id FROM elo_ratings WHERE competition_id=?' if elo else
        'SELECT team_id FROM competition_entries WHERE competition_id=?', (ident,))}
    passes = comp.elo.book.latest_passes(ident, now, team_id) if elo else {}
    excluded = comp.elo.exclusions(ident) if elo else set()
    scores = {r['team_id']: r for r in comp.standings(ident, True)['items']}
    if elo:
        stage = 'ladder' if item['status']=='open' else 'official'
        for row in comp.rows('SELECT team_id,rating,games FROM elo_ratings WHERE competition_id=? AND stage=?', (ident,stage)):
            scores.setdefault(row['team_id'], dict(rating=row['rating'],played=row['games']))
    repos = {r['team_id'] for r in comp.rows('SELECT team_id FROM github_repositories')}
    teams = comp.rows('SELECT id,name FROM teams WHERE archived_ms IS NULL AND season_id=?'+
                      (' AND id=?' if team_id else '')+' ORDER BY name,id',
                      (comp.teams.season,)+((team_id,) if team_id else ()))
    result = []
    for team in teams:
        tid = team['id'];reasons = []
        try:
            comp.teams.eligible(tid)
        except Fault:
            reasons.append('team_size')
        if elo:
            if tid not in repos:reasons.append('repository')
            if tid not in passes:reasons.append('passed_bot')
        elif tid not in active:
            qualified = False
            for job in comp.store.list('job', tid, summary=True):
                if job.get('kind') != 'benchmark' or job['status'] != 'completed' or len(job.get('submission_ids', [])) != 1:continue
                try:
                    comp.qualification(item['config'], tid, job['submission_ids'][0], job['id'])
                    qualified = True;break
                except Fault as exc:
                    if exc.code not in ('qualification_required','not_found'):raise
            if not qualified:reasons.append('passed_bot')
        state = 'excluded' if tid in excluded else 'active' if tid in active else 'ineligible' if reasons else 'eligible'
        score = scores.get(tid, {})
        result.append(dict(team_id=tid, name=team['name'], state=state, reasons=reasons,
                           rank=score.get('rank'), rating=score.get('rating'), points=score.get('points'), played=score.get('played', 0)))
    return result


def team_competitions(comp, team):
    result = []
    for row in comp.rows("SELECT id FROM competitions WHERE archived_ms IS NULL AND status<>'draft' ORDER BY created_ms DESC,id"):
        item = comp.get(row['id'])
        states = competition_teams(comp, item['id'], team)
        if not states:continue
        progress = comp.elo.public(item['id']) if item['config']['format']=='elo' else None
        result.append(dict(id=item['id'], title=item['title'], format=item['config']['format'], status=item['status'],
                           submission_deadline=item['submission_deadline'], next_round_ms=progress['next_round_ms'] if progress else None,
                           **states[0]))
    return {'items': result}


@router.get('/team/competitions', summary='Các giải và trạng thái của chính đội đang đăng nhập')
def own_competitions(request: Request, auth=Depends(team_principal)):
    return team_competitions(request.app.state.competitions, auth['user']['team_id'])


@router.get('/admin/competitions/{ident}/summary', summary='Tổng hợp đội, điều kiện, đợt và hàng đợi theo giải')
def summary(ident: str, request: Request, auth=Depends(admin_principal)):
    comp = request.app.state.competitions
    item = comp.get(ident);elo = item['config']['format']=='elo'
    teams = competition_teams(comp, ident)
    states = Counter(t['state'] for t in teams)
    reasons = Counter(r for t in teams if t['state']=='ineligible' for r in t['reasons'])
    games = comp.rows('SELECT g.status,g.current_job_id FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=?' if elo else
                      'SELECT status,current_job_id FROM competition_fixtures WHERE competition_id=?', (ident,))
    fixtures = Counter(g['status'] for g in games)
    queue = Counter()
    jobs = {g['current_job_id'] for g in games if g['current_job_id']}
    jobs.update(r['job_id'] for r in comp.rows('SELECT job_id FROM github_checks WHERE purpose=? AND job_id IS NOT NULL',(ident,)))
    for ident_job in jobs:
        job = comp.one('SELECT data FROM job_summaries WHERE job=?', (ident_job,))
        if job:
            state = json.loads(job['data'])['status']
            if state not in ('completed','failed','aborted'):queue[state]+=1
    return dict(id=ident, title=item['title'], format=item['config']['format'], status=item['status'],
                submission_deadline=item['submission_deadline'], teams=teams,
                counts={k: states[k] for k in ('active','eligible','ineligible','excluded')}, reasons=dict(reasons),
                fixtures=dict(completed=fixtures['completed'], waiting=sum(v for k,v in fixtures.items() if k not in ('completed','attention')), attention=fixtures['attention']),
                queue=dict(queue), progress=comp.elo.public(ident) if elo else None)

def competition_filter(request, ident):
    if not ident:return None
    comp = request.app.state.competitions
    if comp.get(ident)['archived_ms']:
        raise Fault('not_found', 'Giải đã lưu trữ')
    return {t['team_id']: t for t in competition_teams(comp, ident)}
