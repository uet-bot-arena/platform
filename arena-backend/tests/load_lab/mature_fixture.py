"""Shared open-ladder fixture for route_cost and users_4000; never real accounts."""
import hashlib
import json
from pathlib import Path
import random
import shutil
import sqlite3
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def build(target):
    from large_m1 import prepare
    from season_github import GitHubFixture
    target = Path(target).resolve()
    assert target == Path.home() / 'g10-prof/fixture', target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    template = json.loads((ROOT/'test-results/m1/g10/season/native-job-template.json').read_text())
    github = GitHubFixture()
    started = time.monotonic()
    try:
        cookies, cid = prepare(target/'data', github, template)
        repositories = [{k:v for k,v in r.items() if not isinstance(v,bytes)} for r in github.repositories.values()]
    finally:
        github.close()
    db = sqlite3.connect(target/'data/arena.sqlite')
    db.row_factory = sqlite3.Row
    now = time.time_ns()//1_000_000
    active = dict(db.execute('SELECT * FROM elo_rounds WHERE competition_id=?',(cid,)).fetchone())
    snap = json.loads(active['snapshot'])
    teams = list(snap['ratings'])
    rng = random.Random(20261024)
    db.execute('PRAGMA foreign_keys=OFF')
    db.execute('DELETE FROM grader_leases')
    db.execute('DELETE FROM elo_attempts')
    db.execute("DELETE FROM events WHERE job IN (SELECT id FROM documents WHERE team=?)",('competition:'+cid,))
    db.execute("DELETE FROM documents WHERE team=?",('competition:'+cid,))
    db.execute('DELETE FROM job_queue')
    db.execute('DELETE FROM elo_ratings')
    db.execute('DELETE FROM github_deadlines')
    db.execute("UPDATE competitions SET status='open',submission_deadline=? WHERE id=?",(now+86400000,cid))
    for n in range(1,169):
        ratings = {t:round(1500+rng.uniform(-300,300),2) for t in teams}
        after = dict(ratings=ratings,played={t:2*n for t in teams},changes={t:0 for t in teams})
        snapshot = dict(snap,ratings={t:ratings[t]-5 for t in teams})
        start = now-(169-n)*7200000
        db.execute("INSERT INTO elo_rounds VALUES(?,?,?,?,?,?,'completed',?,NULL,?,?,?)",
                   (uuid.uuid4().hex,cid,'ladder',n,start,start+1800000,active['neighbor_ranks'],active['config'],json.dumps(snapshot),json.dumps(after)))
    snap.update(ratings=ratings,played=after['played'],changes=after['changes'])
    db.execute("UPDATE elo_rounds SET phase='ladder',number=169,started_ms=?,snapshot=? WHERE id=?",(now,json.dumps(snap),active['id']))
    db.execute("UPDATE elo_games SET status='pending',current_job_id=NULL WHERE round_id=?",(active['id'],))
    for team in teams:
        entry = snap['entries'][team]
        db.execute("INSERT INTO elo_ratings VALUES(?,?,'ladder',?,?,0,?)",(cid,team,ratings[team],336,entry['bot_hash']))
        db.execute("INSERT INTO github_checks(team_id,sha,purpose,job_id,state,status_sent,finished_ms) VALUES(?,?,?,?,'pass',1,?)",
                   (team,entry['github_sha'],cid,entry['benchmark_id'],now-1000))
    for team in teams[:40]:
        entry = snap['entries'][team]
        sha = hashlib.sha1(('checking-'+team).encode()).hexdigest()
        job = dict(template,id=uuid.uuid4().hex,team_id=team,status='queued',report=None,error=None,
                   submission_ids=[entry['submission_id']],requires=dict(platform='linux'),created_unix_ms=now)
        for key in ('official','lease_id','grader_id','fencing','worker','runtime','lease_until_ms'):
            job.pop(key,None)
        db.execute('INSERT INTO documents VALUES(?,?,?,?)',(job['id'],'job',team,json.dumps(job)))
        db.execute("INSERT INTO github_commits(team_id,sha,received_ms,pushed_ms,submission_id,bot_hash,state) VALUES(?,?,?,?,?,?,'checking')",
                   (team,sha,now,now,entry['submission_id'],entry['bot_hash']))
        db.execute("INSERT INTO github_checks(team_id,sha,purpose,job_id,state) VALUES(?,?,?,?,'checking')",(team,sha,cid,job['id']))
        db.execute('INSERT INTO github_observations(team_id,sha,observed_ms,pushed_ms) VALUES(?,?,?,?)',(team,sha,now,now))
    # The production migration backfills history when opening the old-schema fixture.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='elo_rating_history'").fetchone():
        db.execute("INSERT INTO elo_rating_history SELECT r.competition_id,r.id,r.phase,r.number,j.key,j.value,r.completed_ms FROM elo_rounds r,json_each(r.after_state,'$.ratings') j WHERE r.status='completed'")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM elo_rounds WHERE status='completed'").fetchone()[0]==168
    assert db.execute('SELECT COUNT(*) FROM elo_games').fetchone()[0]==1000
    assert db.execute("SELECT COUNT(*) FROM github_checks WHERE state='checking'").fetchone()[0]==40
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.close()
    (target/'meta.json').write_text(json.dumps(dict(cookies=cookies,cid=cid,repositories=repositories,created_ms=now)))
    print(f'Open ladder fixture built in {time.monotonic()-started:.1f}s',flush=True)
