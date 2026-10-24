"""Per-route CPU cost on the gói 10 1,000-team fixture (in-process, no load generator).

Usage (WSL): .venv/bin/python tests/load_lab/route_cost.py [build|routes|tick]
Fixture cached at ~/g10-prof/fixture; each run works on a fresh copy.
"""
import json, os, shutil, sqlite3, sys, time, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'tests/load_lab')]
os.chdir(ROOT)
BASE = Path.home() / 'g10-prof'
FIX = BASE / 'fixture'
WORK = BASE / 'work'
mode = sys.argv[1] if len(sys.argv) > 1 else 'routes'

if mode == 'build':
    from mature_fixture import build
    build(FIX)
    sys.exit()

if WORK.exists(): shutil.rmtree(WORK)
shutil.copytree(FIX / 'data', WORK)
with sqlite3.connect(WORK/'arena.sqlite') as db:
    db.execute('UPDATE sessions SET expires_ms=?',(time.time_ns()//1_000_000+14*86400000,))
os.sync()
meta = json.loads((FIX / 'meta.json').read_text())
cookies, cid = meta['cookies'], meta['cid']
os.environ.update(ARENA_CONFIG='none', ARENA_SECRETS='none', ARENA_API_DATA=str(WORK),
                  ARENA_API_ORIGIN='http://127.0.0.1:4181', ARENA_HASH_WORKERS='1')
from arena_api.app import create_app
from arena_api.settings import Settings
from fastapi.testclient import TestClient

app = create_app(Settings(), start_worker=False)
from route_metrics import instrument
N = int(os.environ.get('N', '60'))
with TestClient(app, base_url='http://127.0.0.1:4181') as c:
    meter, commits = instrument(app.state.store)
    if mode in ('capture', 'compare'):
        def stable(value):
            if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k!='server_ms'}
            if isinstance(value,list):return [stable(v) for v in value]
            return value
        actual={}
        for i in (0,100,999):
            for r in ('/team/elo', f'/competitions/{cid}', f'/competitions/{cid}/standings'):
                response=c.get('/api/v1'+r,headers={'Cookie':cookies[i]})
                assert response.status_code==200,(r,response.text[:200])
                actual[str(i)+r]=stable(response.json())
        baseline=BASE/'route-json-before.json'
        if mode=='capture':baseline.write_text(json.dumps(actual));print('Captured JSON before A4',flush=True)
        else:
            assert actual==json.loads(baseline.read_text()), 'Route JSON changed'
            print('Route JSON matches before A4 (excluding server_ms)',flush=True)
    elif mode == 'routes':
        routes = ['/teams/me', '/dashboard', '/submissions?limit=50', '/jobs?limit=50', '/competitions',
                  '/team/elo', '/teams/me/github', f'/competitions/{cid}', f'/competitions/{cid}/standings',
                  '/health/ready']
        out = {}
        cold = None
        for r in routes:
            if r.endswith('/standings'):
                app.state.competitions.elo.standings_cache.clear()
                t=time.process_time()
                assert c.get('/api/v1'+r,headers={'Cookie':cookies[0]}).status_code==200
                cold=(time.process_time()-t)*1000
            for i in range(3): c.get('/api/v1' + r, headers={'Cookie': cookies[i]})
            cpu = []; size = 0
            for i in range(N):
                t = time.process_time()
                resp = c.get('/api/v1' + r, headers={'Cookie': cookies[(i * 7) % len(cookies)]})
                cpu.append(time.process_time() - t)
                assert resp.status_code == 200, (r, resp.status_code, resp.text[:200])
                size = len(resp.content)
            out[r] = dict(cpu_ms_median=round(statistics.median(cpu) * 1000, 2), cpu_ms_p95=round(sorted(cpu)[int(N * .95) - 1] * 1000, 2), bytes=size)
            print(r, out[r], flush=True)
        (BASE / 'routes.json').write_text(json.dumps(out, indent=1))
        baseline=out['/health/ready']['cpu_ms_median']
        budgets={r:(8 if r=='/team/elo' else 5) for r in routes if r not in ('/health/ready',f'/competitions/{cid}',f'/competitions/{cid}/standings')}
        checks=[dict(name=r,cpu_ms=round(out[r]['cpu_ms_median']-baseline,3),budget_ms=b) for r,b in budgets.items()]
        checks.append(dict(name='standings cold',cpu_ms=round(cold-baseline,3),budget_ms=100))
        gh=app.state.github; gh.clock=lambda: -1
        gh.tick()  # Initial open-competition backfill is work, not an idle tick.
        cpu=[]
        for _ in range(60):
            t=time.process_time(); gh.tick(); cpu.append((time.process_time()-t)*1000)
        checks.append(dict(name='github.tick idle',cpu_ms=statistics.median(cpu),budget_ms=5))
        db=app.state.store.db
        with app.state.store.transaction():db.execute('CREATE TABLE IF NOT EXISTS lab_commit(n INTEGER)')
        elapsed=[]
        for i in range(100):
            t=time.perf_counter()
            with app.state.store.transaction():db.execute('INSERT INTO lab_commit VALUES(?)',(i,))
            elapsed.append((time.perf_counter()-t)*1000)
        checks.append(dict(name='SQLite commit',cpu_ms=statistics.median(elapsed),budget_ms=.5))
        informational={r:round(out[r]['cpu_ms_median']-baseline,3) for r in (f'/competitions/{cid}',f'/competitions/{cid}/standings')}
        informational.update(lock_max_without_gc_ms=meter.longest_ms,gc_gen2_max_ms=meter.gc_gen2_max_ms,
            lock_top5=sorted(meter.sites.items(),key=lambda item:-item[1])[:5])
        print('A5 (median CPU minus median health; commit/lock are wall time):',flush=True)
        for check in checks:print(check,'PASS' if check['cpu_ms']<=check['budget_ms'] else 'FAIL',flush=True)
        print('Informational only:',informational,flush=True)
        result=dict(checks=checks,informational=informational,commits=commits[0],health_ms=baseline)
        (BASE/'budget.json').write_text(json.dumps(result,indent=2))
        assert all(c['cpu_ms']<=c['budget_ms'] for c in checks), 'A5 budget exceeded'
        print('VmRSS', [l for l in open('/proc/self/status') if l.startswith(('VmRSS', 'VmHWM'))], flush=True)
    elif mode in ('mix', 'board'):
        routes = ['/teams/me', '/dashboard', '/submissions?limit=50', '/jobs?limit=50', '/competitions', '/team/elo', '/teams/me/github'] if mode == 'mix' else [f'/competitions/{cid}', f'/competitions/{cid}/standings']
        t = time.process_time()
        for i in range(N):
            for r in routes:
                assert c.get('/api/v1' + r, headers={'Cookie': cookies[(i * 7) % len(cookies)]}).status_code == 200
        print(mode, 'cpu ms per view', round((time.process_time() - t) / N * 1000, 2), flush=True)
    elif mode == 'ghsql':
        import collections
        gh = app.state.github; acc = app.state.accounts; rec = collections.defaultdict(lambda: [0.0, 0])
        orig_rows, orig_one = acc.rows, acc.one
        def timed(fn):
            def w(sql, args=()):
                t = time.process_time()
                try: return fn(sql, args)
                finally:
                    r = rec[' '.join(sql.split())[:150]]; r[0] += time.process_time() - t; r[1] += 1
            return w
        acc.rows, acc.one = timed(orig_rows), timed(orig_one)
        gh.clock = lambda: -1
        for i in range(10): gh.tick()
        for k, (t, n) in sorted(rec.items(), key=lambda kv: -kv[1][0])[:8]:
            print(f'{t/10*1000:7.2f} ms/tick  calls/tick {n/10:6.1f}  {k}')
    elif mode == 'tick':
        comp = app.state.competitions; gh = app.state.github; svc = app.state.service
        def bench(name, fn, n=30):
            cpu = []
            for i in range(n):
                t = time.process_time(); fn(); cpu.append(time.process_time() - t)
            print(f'{name}: cpu ms mean {statistics.mean(cpu)*1000:.2f} max {max(cpu)*1000:.2f}', flush=True)
        bench('competitions.tick (scheduler, runs every 0.2 s AND before every try_lease)', comp.tick)
        bench('elo.reconcile', comp.elo.reconcile)
        bench('elo.public(cid) (inside /competitions/{id})', lambda: comp.elo.public(cid))
        bench('elo.standings(cid)', lambda: comp.elo.standings(cid))
        # github tick with no repo due: push clock far back so nothing is due
        gh.clock = lambda: -1
        bench('github.tick with 0 due repos (status scan etc.)', gh.tick, 10)
        import cProfile, pstats
        for name, fn in [('view', lambda: comp.view(cid)), ('standings', lambda: comp.standings(cid)), ('team_view', lambda: comp.elo.team_view(app.state.store.db.execute("select id from teams limit 1").fetchone()[0]))]:
            pr = cProfile.Profile(); pr.enable()
            for i in range(30): fn()
            pr.disable(); print('=====', name); pstats.Stats(pr).sort_stats('tottime').print_stats(8)
        import sqlite3
        db = sqlite3.connect(WORK / 'arena.sqlite')
        print('github_commits', db.execute('select count(*) from github_commits').fetchone(), 'documents', db.execute('select count(*) from documents').fetchone(),
              'elo_games', db.execute('select count(*), status from elo_games group by status').fetchall())

meter.close()
