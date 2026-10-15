"""Anonymous spectators + real official games on a fresh, isolated CLI server.

No production port/data, worker mocks, daemon restarts or image deletion.
Each live spectator opens TWO SSE connections, as the Arena UI does.
"""
import asyncio
from collections import Counter
from datetime import datetime
import json
import tempfile
import time
import traceback
from zoneinfo import ZoneInfo

import httpx
from load_resilience import Harness, ROOT, OUTPUT, percentile
from arena import sandbox

GUEST = {"Cookie": ""}


class Guests(Harness):
    async def reads(self, client, name, concurrency, paths, count=240, overload=False):
        latencies, codes, errors = [], Counter(), Counter()
        sent, start = 0, time.perf_counter()
        async def worker():
            nonlocal sent
            while sent < count:
                ordinal = sent
                sent += 1
                before = time.perf_counter()
                try:
                    response = await client.get(self.origin+'/api/v1'+paths[ordinal % len(paths)], headers=GUEST)
                    codes[str(response.status_code)] += 1
                    if response.status_code not in ((200,503) if overload else (200,)):
                        errors['HTTP '+str(response.status_code)] += 1
                    if response.status_code == 200:
                        if not response.headers.get('X-Request-ID') or response.headers.get('Set-Cookie'):
                            errors['request metadata or unexpected session'] += 1
                        response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    errors[type(exc).__name__] += 1
                latencies.append((time.perf_counter()-before)*1000)
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed = time.perf_counter()-start
        row = {'name':name,'clients':concurrency,'requests':sent,'seconds':round(elapsed,3),
               'requests_per_second':round(sent/elapsed,2),'p50_ms':percentile(latencies,.5),
               'p95_ms':percentile(latencies,.95),'p99_ms':percentile(latencies,.99),
               'statuses':dict(codes),'unexpected_errors':dict(errors),'expected_overload_503':overload}
        self.report['load'].append(row)
        print('LOAD', json.dumps(row), flush=True)
        assert not errors, row
        self.resources(name)
        return row

    async def viewers(self, client, cid, fid, viewers, api_clients, overload=False):
        """Consume both streams continuously; do not just hold idle TCP sockets."""
        connected, codes, state_counts, event_types = 0, Counter(), [], Counter()
        async def stream(path, board):
            nonlocal connected
            state_index = len(state_counts) if board else None
            if board:
                state_counts.append(0)
            async with client.stream('GET', self.origin+'/api/v1'+path,
                                     headers={**GUEST,'Accept':'text/event-stream'}) as response:
                codes[str(response.status_code)] += 1
                connected += 1
                assert response.status_code in ((200,503) if overload else (200,)), response.status_code
                assert not response.headers.get('Set-Cookie')
                if response.status_code != 200:
                    return
                async for line in response.aiter_lines():
                    if line.startswith('event: '):
                        kind = line[7:]
                        assert kind not in ('auth_expired','unavailable'), kind
                        event_types[kind] += 1
                    elif line.startswith('data: '):
                        data = json.loads(line[6:])
                        if board and data.get('type') == 'state':
                            assert set(data['data']) <= {'state','counts'}
                            state_counts[state_index] += 1
                        for secret in ('source_path','build_log','private_metric','cpu_usage_usec','stderr'):
                            assert secret not in line, secret
        root = '/competitions/'+cid
        tasks = [asyncio.create_task(stream(path,board)) for _ in range(viewers)
                 for path,board in [(root+'/watch',False),(root+'/fixtures/'+fid+'/events',True)]]
        try:
            async with asyncio.timeout(15):
                while connected < viewers*2:
                    for task in tasks:
                        if task.done() and task.exception():
                            raise task.exception()
                    await asyncio.sleep(.02)
            # At 12 viewers, 24 streams + 4 HTTP clients remain below limit 32.
            await self.reads(client, f'{viewers} guest Arena viewers + API', api_clients,
                             [root, root+'/fixtures', root+'/standings'], count=120, overload=overload)
            await asyncio.sleep(2)
            self.resources(str(viewers)+' guest viewers / '+str(viewers*2)+' SSE')
            if overload:
                assert codes['503'] > 0, codes
        finally:
            for task in tasks:
                task.cancel()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                raise outcome
        # Streams cancelled while open still prove actual board events via type.
        if not overload:
            assert event_types['arena'] > 0 and event_types['competition'] >= viewers, event_types
            assert len(state_counts)==viewers and min(state_counts)>=2, state_counts
        self.checked(f'{viewers}_anonymous_viewers_two_streams_each', viewers=viewers,
                     sse_connections=viewers*2, statuses=dict(codes), events=dict(event_types),
                     board_states_per_connection=state_counts)

    async def execute_guest(self):
        self.seed()
        self.report['environment']['spectator_connections'] = 'two SSE per Arena viewer (board + competition follow)'
        async with httpx.AsyncClient(timeout=40, limits=httpx.Limits(max_connections=80,max_keepalive_connections=0),
                                     trust_env=False) as client:
            await self.start(client)
            admin = await self.login(client,'load-admin')
            self.auth = [await self.login(client,'load-team-'+str(i)) for i in range(2)]
            client.cookies.clear()
            # Same real protocol/engine/budgets; sleep only in official matches
            # so enough moves occur while parallel spectator clients are open.
            source = (ROOT/'sdk/bot.cpp').read_text(encoding='utf-8')
            source = source.replace('#include <string>', '#include <string>\n#include <unistd.h>').replace(
                'std::string line, match;', 'std::string line, match; bool slow_match = false;').replace(
                'match = m.at("match_id");', 'match = m.at("match_id"); slow_match = m.at("payload").at("match").at("kind") == "match";').replace(
                'const auto legal =', 'if (slow_match) usleep(300000); const auto legal =')
            qualified = []
            for auth in self.auth:
                sub = await self.upload(client,auth,'v1.0.0',source)
                job = await self.enqueue(client,auth,sub)
                done = await self.wait_job(client,auth,job['id'])
                assert done['status']=='completed' and done['report']['passed'] and len(done['report']['samples'])==72, done
                qualified.append((sub,done))
            self.checked('two_real_cpp_bots_qualified_72_samples_each')
            comp = (await self.request(client,'POST','/admin/competitions',admin,expected=201,
                                      json={'title':'Isolated guest spectator load cup','max_teams':2})).json()
            cid = comp['id']; root = '/competitions/'+cid
            await self.request(client,'GET',root,GUEST,expected=404)
            await self.request(client,'POST','/admin/competitions/'+cid+'/open',admin)
            for auth,(sub,job) in zip(self.auth,qualified):
                await self.request(client,'POST',root+'/entry',auth,json={'submission_id':sub['id'],
                    'benchmark_id':job['id'],'delegate_timeout_claims':True})
            await self.request(client,'POST','/admin/competitions/'+cid+'/lock',admin)
            fixtures = (await self.request(client,'GET',root+'/fixtures',GUEST)).json()['items']
            fid = fixtures[0]['id']; events = root+'/fixtures/'+fid+'/events'
            paths = ['/competitions',root,root+'/fixtures',root+'/standings',root+'/watch',events]
            # Guessed IDs / headers never grant access to private work or writes.
            for path in ['/teams/me','/submissions','/jobs','/jobs/'+qualified[0][1]['id'],
                         '/jobs/'+qualified[0][1]['id']+'/events','/admin/metrics']:
                await self.request(client,'GET',path,GUEST,expected=401)
            await self.request(client,'POST',root+'/withdraw',GUEST,expected=401)
            await self.request(client,'GET',root,{'Cookie':'arena_web_session=expired-or-fake'})
            self.checked('guest_visibility_no_session_private_reads_and_mutations_rejected')
            for concurrency in (1,8,24):
                await self.reads(client,'anonymous public reads',concurrency,paths)
            overload = await self.reads(client,'anonymous HTTP admission overload',48,paths,count=192,overload=True)
            assert overload['statuses'].get('503',0)>0, overload
            await self.reads(client,'anonymous recovery after HTTP overload',4,paths,count=80)
            self.checked('public_http_overload_is_bounded_503_then_recovers')
            await self.request(client,'POST','/admin/competitions/'+cid+'/start',admin)
            async with asyncio.timeout(30):
                while True:
                    replay = (await self.request(client,'GET',events,GUEST)).json()
                    if any(e['type']=='state' for e in replay['items']):
                        break
                    await asyncio.sleep(.1)
            await self.viewers(client,cid,fid,8,8)
            await self.viewers(client,cid,fid,12,4)
            await self.viewers(client,cid,fid,16,4,overload=True)
            await self.reads(client,'recovery after guest SSE overload',4,paths,count=80)
            # Verify an anonymous watcher observes next fixture and completion.
            statuses = []
            async with asyncio.timeout(150):
                async with client.stream('GET',self.origin+'/api/v1'+root+'/watch',headers={**GUEST,'Accept':'text/event-stream'}) as feed:
                    assert feed.status_code == 200
                    async for line in feed.aiter_lines():
                        if line.startswith('data: '):
                            state = json.loads(line[6:])
                            statuses.append([f['status'] for f in state['fixtures']])
                            if state['status']=='completed':
                                break
            assert any(s[1]=='queued' for s in statuses) and statuses[-1]==['completed','completed'], statuses
            standings = (await self.request(client,'GET',root+'/standings',GUEST)).json()
            assert all(s['played']==2 and s['points']==3 for s in standings['items']), standings
            replay = (await self.request(client,'GET',events,GUEST)).json()
            assert any(e['type']=='completed' for e in replay['items']), replay
            cursor = next(e['seq'] for e in replay['items'] if e['type']=='state')
            resumed = (await self.request(client,'GET',events,GUEST,
                       headers={'Last-Event-ID':replay['attempt_id']+':'+str(cursor)})).json()
            assert resumed['items'] and all(e['seq']>cursor for e in resumed['items'])
            self.checked('guest_progress_next_fixture_replay_resume_and_points_survive_load',
                         progress_states=statuses,points=[s['points'] for s in standings['items']])
            await self.reads(client,'completed replay and standings',12,paths)
            # Hide a completed cup while a new SSE viewer connects. It must be
            # inaccessible on every public route, but visible to the organizer.
            await self.request(client,'POST','/admin/competitions/'+cid+'/archive',admin,
                               json={'expected_revision':(await self.request(client,'GET',root,admin)).json()['revision']})
            for path in paths[1:]:
                await self.request(client,'GET',path,GUEST,expected=404)
            assert not (await self.request(client,'GET','/competitions',GUEST)).json()['items']
            await self.request(client,'GET',root,admin)
            self.checked('archived_cup_disappears_from_every_guest_endpoint_admin_retains_access')
            # Hide a public, empty cup while a guest is already watching it.
            empty = (await self.request(client,'POST','/admin/competitions',admin,expected=201,
                                       json={'title':'Visibility stream test cup','max_teams':2})).json()
            opened = (await self.request(client,'POST','/admin/competitions/'+empty['id']+'/open',admin)).json()
            watch = '/competitions/'+empty['id']+'/watch'
            async with client.stream('GET',self.origin+'/api/v1'+watch,headers={**GUEST,'Accept':'text/event-stream'}) as feed:
                assert feed.status_code == 200
                lines = feed.aiter_lines()
                async for line in lines:
                    if line.startswith('data: '):
                        assert json.loads(line[6:])['status']=='open'
                        break
                await self.request(client,'POST','/admin/competitions/'+empty['id']+'/archive',admin,
                                   json={'expected_revision':opened['revision']})
                async with asyncio.timeout(5):
                    async for line in lines:
                        if line=='event: unavailable':
                            break
                    else:
                        raise AssertionError('Hidden cup stream did not close with unavailable')
            self.checked('hiding_public_cup_closes_already_connected_guest_sse')
            self.report['passed'] = True


def write_report(report):
    lines = ['# Guest spectator — local load test','',
             'Started: '+report['started_local'], 'Completed: '+report['completed_local'],'',
             'Result: '+('PASS' if report.get('passed') else 'FAIL'),'',
             'Fresh database, two real C++ bots, Docker worker, normal CLI limit 32. '
             'Anonymous Arena viewer = board SSE + competition-follow SSE. No real teams/data modified.','',
             '| Phase | HTTP clients | Requests | P95 ms | HTTP status counts |',
             '|---|---:|---:|---:|---|']
    for row in report['load']:
        lines.append(f"| {row['name']} | {row['clients']} | {row['requests']} | {row['p95_ms']} | {json.dumps(row['statuses'])} |")
    lines += ['', '## Checks','']+[f"- PASS: {row['name']} — {json.dumps({k:v for k,v in row.items() if k not in ('name','passed')})}" for row in report['checks']]
    lines += ['', '## Limits','',
              'Loopback HTTP, closed-loop clients, small fixture data, short run, no browser rendering. '
              'No production capacity/SLO claim. At 16 Arena viewers, 32 SSE connections meet/exceed the CLI admission limit; '
              '503 is expected. Successful HTTP-only request rates do not represent live spectator capacity.', '',
              'Resources: '+json.dumps(report['resources']), '', 'Cleanup: '+json.dumps(report.get('cleanup'))]
    for key in ('failure','cleanup_failure'):
        if report.get(key):
            lines += ['', key+': '+json.dumps(report[key])]
    (OUTPUT/'guest-load-report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    OUTPUT.mkdir(exist_ok=True)
    image = sandbox.image_id('uet-arena-worker:clock-v2')
    with tempfile.TemporaryDirectory(prefix='guest-load-',dir=OUTPUT) as directory:
        harness = Guests(directory,image,report_prefix='guest-load')
        try:
            asyncio.run(harness.execute_guest())
        except Exception as exc:
            harness.report['passed'] = False
            harness.report['failure'] = {'type':type(exc).__name__,'detail':str(exc)[:4000]}
            traceback.print_exc()
        finally:
            try:
                harness.cleanup()
            except Exception as exc:
                harness.report['passed'] = False
                harness.report['cleanup_failure'] = {'type':type(exc).__name__,'detail':str(exc)[:2000]}
                traceback.print_exc()
            harness.report['completed_local'] = datetime.now(ZoneInfo('Asia/Bangkok')).isoformat()
            (OUTPUT/'guest-load-latest.json').write_text(json.dumps(harness.report,indent=2),encoding='utf-8')
            write_report(harness.report)
        assert harness.report.get('passed'), harness.report.get('failure')
    print('ALL GUEST SPECTATOR LOAD CHECKS PASSED',flush=True)


if __name__=='__main__':
    main()
