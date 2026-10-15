"""Competition HTTP and state-machine tests; synthetic benchmark records only here.

Real qualification, cross-team execution and SSE are separately exercised by
competition_integration.py with Uvicorn, uploaded C++, and Docker.
"""
import copy
import json
import sqlite3
import uuid
from unittest.mock import patch

from arena import engine
from arena.config import BUILD_FLAGS
from arena.protocol import Fault
from arena.service import Service
from arena.store import Store
from arena_api.accounts import Accounts, now_ms
from arena_api.competitions import Competitions
from test_web_api import APIHarness


class CompetitionAPI(APIHarness):
    def setUp(self):
        super().setUp()
        self.comp = self.app.state.competitions
        self.store = self.app.state.store
        self.image_patch = patch("arena.sandbox.image_id", return_value="sha256:" + "1" * 64)
        self.image_patch.start()
        self.addCleanup(self.image_patch.stop)

    def post(self, client, path, body=None, key=None, expected=200):
        response = client.post("/api/v1" + path, json=body or {}, headers={"Idempotency-Key": key or uuid.uuid4().hex})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def create_comp(self, max_teams=16):
        result = self.post(self.client, "/admin/competitions", {"title": "OTTv2 integration cup", "max_teams": max_teams}, expected=201)
        self.post(self.client, f"/admin/competitions/{result['id']}/open")
        return result

    def qualified(self, team, config):
        # These are unit fixtures, never reported as actual timing measurements.
        submission = self.store.create("submission", team, {"version": "v1.0.0", "runtime": "cpp17", "source_path": "/synthetic-only",
            "source": {"zip_sha256": "2" * 64, "files": {"bot.cpp": "3" * 64}}, "artifact": None})
        artifact = {"artifact_sha256": "4" * 64, "source": submission["source"], "image_id": config["image_id"],
                    "flags": BUILD_FLAGS, "sdk_sha256": config["sdk_sha256"]}
        benchmark = self.store.create("job", team, {"kind": "benchmark", "status": "completed", "submission_ids": [submission["id"]],
            "report": {"passed": True, "samples": [{"wall_ms": 1}] * 72, "turn_wall_ms": {"samples": 72},
                "profile": config["profile"], "image_id": config["image_id"], "engine_sha256": config["engine_sha256"],
                "suite_id": config["suite_id"], "suite_sha256": config["suite_sha256"], "artifacts": [artifact]}})
        return {"submission_id": submission["id"], "benchmark_id": benchmark["id"], "delegate_timeout_claims": True}

    def prepared(self):
        comp = self.create_comp()
        a, ca = self.create_team()
        b, cb = self.create_team("Team Two", "team-two")
        bodies = [self.qualified(c["account"]["team_id"], comp["config"]) for c in (ca, cb)]
        for client, body in zip((a, b), bodies):
            self.post(client, f"/competitions/{comp['id']}/entry", body)
        locked = self.post(self.client, f"/admin/competitions/{comp['id']}/lock")
        return locked, (a, b), (ca, cb), bodies

    def launch(self, comp):
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        self.comp.tick()
        return self.take_job()

    def take_job(self):
        ident = self.app.state.service.pending.get_nowait()
        self.app.state.service.pending.task_done()
        return self.store.get(ident)

    def finish(self, job, winner="red", **extra):
        result = {"winner": winner, "reason": "base_capture"}
        report = {"eligible_for_tournament": True, "result": result, "final_state": {"result": result},
                  "image_id": job["image_id"], "engine_sha256": job["engine_sha256"], "profile": job["profile"],
                  "adjudication": job["official"]["adjudication"], **extra}
        self.store.update(job["id"], status="completed", report=report)
        self.store.event(job["id"], "completed", {"result": result})
        self.comp.reconcile()

    def test_lock_and_cross_team_dispatch_preserve_ownership(self):
        comp, clients, credentials, bodies = self.prepared()
        self.assertEqual(comp["fixture_count"], 2)
        changed = copy.deepcopy(bodies[0])
        changed["delegate_timeout_claims"] = False
        self.post(clients[0], f"/competitions/{comp['id']}/entry", changed, expected=422)
        self.post(clients[0], f"/competitions/{comp['id']}/entry", bodies[0], expected=409)
        job = self.launch(comp)
        self.assertEqual(job["team_id"], "competition:" + comp["id"])
        self.assertEqual(set(e["team_id"] for e in job["official"]["entries"]), {c["account"]["team_id"] for c in credentials})
        for client in clients:
            self.assertEqual(client.get("/api/v1/jobs/" + job["id"]).status_code, 404)
            self.assertEqual(client.get("/api/v1/jobs/" + job["id"] + "/events").status_code, 404)
        self.post(clients[0], "/matches", {"red_submission_id": bodies[0]["submission_id"], "blue_submission_id": bodies[1]["submission_id"], "delegate_timeout_claims": True}, expected=404)
        fixtures = self.comp.schedule(comp["id"])["items"]
        self.assertEqual((fixtures[0]["red_team_id"], fixtures[0]["blue_team_id"]), (fixtures[1]["blue_team_id"], fixtures[1]["red_team_id"]))

    def test_watch_progress_is_public_metadata_and_tracks_next_fixture(self):
        comp, clients, credentials, bodies = self.prepared()
        path = '/api/v1/competitions/' + comp['id'] + '/watch'
        self.assertEqual(self.other_client().get(path).status_code, 200)
        locked = clients[0].get(path).json()
        self.assertEqual(locked['status'], 'locked')
        self.assertEqual([f['status'] for f in locked['fixtures']], ['pending', 'pending'])
        first = self.launch(comp)
        running = clients[1].get(path).json()
        self.assertEqual([f['status'] for f in running['fixtures']], ['queued', 'pending'])
        self.finish(first)
        self.comp.tick()
        next_job = self.take_job()
        next_state = clients[0].get(path).json()
        self.assertEqual([f['status'] for f in next_state['fixtures']], ['completed', 'queued'])
        for fixture in next_state['fixtures']:
            self.assertEqual(set(fixture), {'id', 'competition_id', 'ordinal', 'red_team_id', 'blue_team_id', 'status', 'result'})
        self.finish(next_job)
        completed = clients[0].get(path, headers={'Accept': 'text/event-stream'})
        self.assertEqual(completed.status_code, 200)
        self.assertIn('text/event-stream', completed.headers['content-type'])
        payload = json.loads(completed.text.split('data: ', 1)[1].split('\n', 1)[0])
        self.assertEqual(payload['status'], 'completed')
        self.assertEqual([f['status'] for f in payload['fixtures']], ['completed', 'completed'])

    def test_watch_rejects_draft_for_team_and_missing_competition(self):
        team, _ = self.create_team()
        comp = self.post(self.client, '/admin/competitions', {'title': 'Draft watch cup'}, expected=201)
        path = '/api/v1/competitions/' + comp['id'] + '/watch'
        self.assertEqual(team.get(path).status_code, 404)
        self.assertEqual(self.client.get(path).json()['status'], 'draft')
        self.assertEqual(team.get('/api/v1/competitions/missing/watch').status_code, 404)

    def test_guests_view_public_competition_replay_and_sse_without_private_bot_data(self):
        comp, _, _, bodies = self.prepared()
        guest = self.other_client()
        job = self.launch(comp)
        fixture = job['official']['fixture_id']
        self.store.event(job['id'], 'state', {'state': engine.initial_state(), 'counts': {}, 'private_log': 'SECRET-LOG'})
        self.store.event(job['id'], 'progress', {'private_metric': 'SECRET-METRIC'})
        self.finish(job)
        root = '/api/v1/competitions/' + comp['id']
        for path in ('/api/v1/competitions', root, root+'/fixtures', root+'/standings', root+'/watch'):
            result = guest.get(path)
            self.assertEqual(result.status_code, 200, path)
            self.assertNotIn('Set-Cookie', result.headers)
            for private in ('captain@example.test', 'student_id', 'source_path', 'benchmark_id', 'password_hash'):
                self.assertNotIn(private, result.text)
        path = root + '/fixtures/' + fixture + '/events'
        result = guest.get(path).json()
        self.assertEqual([e['type'] for e in result['items']], ['state', 'completed'])
        self.assertNotIn('SECRET', json.dumps(result))
        resumed = guest.get(path, headers={'Last-Event-ID':job['id']+':'+str(result['items'][0]['seq'])}).json()
        self.assertEqual([e['type'] for e in resumed['items']], ['completed'])
        stream = guest.get(path, headers={'Accept':'text/event-stream'})
        self.assertEqual(stream.status_code, 200)
        self.assertIn('event: arena', stream.text)
        self.assertNotIn('auth_expired', stream.text)
        self.assertNotIn('SECRET', stream.text)
        for private in ('/api/v1/jobs/'+job['id'], '/api/v1/jobs/'+job['id']+'/events',
                        '/api/v1/submissions', '/api/v1/teams/me',
                        '/api/v1/admin/competitions/'+comp['id']+'/fixtures/'+fixture+'/attempts'):
            self.assertEqual(guest.get(private).status_code, 401, private)

    def test_guests_cannot_mutate_even_with_known_identifiers_and_fake_csrf(self):
        comp = self.create_comp()
        guest = self.other_client()
        for path, body in [('/competitions/'+comp['id']+'/entry', {'submission_id':'a'*32,'benchmark_id':'b'*32,'delegate_timeout_claims':True}),
                           ('/competitions/'+comp['id']+'/withdraw', {}),
                           ('/admin/competitions/'+comp['id']+'/start', {}),
                           ('/benchmarks', {'submission_id':'a'*32})]:
            result = guest.post('/api/v1'+path, json=body, headers={'Idempotency-Key':'guest-denied-key','X-CSRF-Token':'fake'})
            self.assertEqual(result.status_code, 401, result.text)

    def test_draft_and_archived_competitions_hidden_from_all_guest_reads(self):
        comp, _, _, _ = self.prepared()
        fixture = self.comp.schedule(comp['id'])['items'][0]['id']
        guest = self.other_client()
        for updates in ("status='draft'", "status='locked',archived_ms=123"):
            with self.store.lock, self.store.db:
                self.store.db.execute('UPDATE competitions SET '+updates+' WHERE id=?', (comp['id'],))
            self.assertEqual(guest.get('/api/v1/competitions').json()['items'], [])
            for suffix in ('', '/fixtures', '/standings', '/watch', '/fixtures/'+fixture+'/events'):
                path = '/api/v1/competitions/'+comp['id']+suffix
                self.assertEqual(guest.get(path).status_code, 404, path)
                self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_invalid_or_revoked_cookie_only_falls_back_to_guest_on_public_reads(self):
        comp = self.create_comp()
        guest = self.other_client()
        guest.cookies.set('arena_web_session', 'expired-cookie')
        self.assertEqual(guest.get('/api/v1/competitions/'+comp['id']).status_code, 200)
        self.assertEqual(guest.get('/api/v1/auth/me').status_code, 401)
        self.assertEqual(guest.get('/api/v1/jobs').status_code, 401)
        schema = guest.get('/openapi.json').json()
        self.assertEqual(schema['paths']['/api/v1/competitions/{ident}/watch']['get']['security'], [{},{'cookieAuth':[]}])
        self.assertEqual(schema['paths']['/api/v1/competitions/{ident}/entry']['post']['security'], [{'cookieAuth':[], 'csrfAuth':[]}])

    def test_open_stream_rechecks_visibility_and_drops_revoked_admin_access(self):
        comp = self.post(self.client, '/admin/competitions', {'title':'Private draft stream'}, expected=201)
        path = '/api/v1/competitions/'+comp['id']+'/watch'
        # The initial dependency sees admin, but a later stream iteration has
        # lost admin authority. No draft metadata can be streamed after that.
        with patch('arena_api.competition_routes.spectator_admin', return_value=False):
            result = self.client.get(path, headers={'Accept':'text/event-stream'})
        self.assertEqual(result.status_code, 200)
        self.assertIn('event: unavailable', result.text)
        self.assertNotIn('event: competition', result.text)

    def test_qualification_rejects_incomplete_or_different_provenance(self):
        comp = self.create_comp()
        team, credential = self.create_team()
        body = self.qualified(credential["account"]["team_id"], comp["config"])
        original = self.store.get(body["benchmark_id"])["report"]
        mutations = [{"passed": False}, {"samples": []}, {"image_id": "sha256:" + "0" * 64},
                     {"engine_sha256": "other"}, {"suite_sha256": "other"}, {"error": {"code": "bad"}},
                     {"profile": {**original["profile"], "turn_ms": 30000}}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.store.update(body["benchmark_id"], report={**original, **mutation})
                self.post(team, f"/competitions/{comp['id']}/entry", body, expected=409)
        self.store.update(body["benchmark_id"], report=original)
        self.post(team, f"/competitions/{comp['id']}/entry", body)

    def test_qualification_cannot_be_borrowed_from_another_team(self):
        comp = self.create_comp()
        a, ca = self.create_team()
        b, _ = self.create_team("Team Two", "team-two")
        body = self.qualified(ca["account"]["team_id"], comp["config"])
        self.post(b, f"/competitions/{comp['id']}/entry", body, expected=404)
        self.post(a, f"/competitions/{comp['id']}/entry", body)
        self.post(self.client, f"/admin/competitions/{comp['id']}/lock", expected=409)
        self.assertEqual(self.comp.schedule(comp["id"])["total"], 0)

    def test_results_count_once_and_equal_wins_share_rank(self):
        comp, _, _, _ = self.prepared()
        first = self.launch(comp)
        self.finish(first, "red")
        self.comp.tick()
        second = self.take_job()
        self.finish(second, "red")
        before = self.client.get("/api/v1/admin/audit").json()
        for _ in range(4):
            self.comp.reconcile()
        after = self.client.get("/api/v1/admin/audit").json()
        self.assertEqual(before, after)
        standings = self.comp.standings(comp["id"])
        self.assertFalse(standings["provisional"])
        self.assertEqual([(s["rank"], s["wins"], s["losses"], s["played"]) for s in standings["items"]], [(1, 1, 1, 2)] * 2)
        self.assertEqual(self.comp.view(comp["id"])["status"], "completed")
        fixture = second["official"]["fixture_id"]
        self.post(self.client, f"/admin/competitions/{comp['id']}/fixtures/{fixture}/retry", expected=409)

    def test_draws_and_double_startup_failures_complete_without_wins(self):
        comp, _, _, _ = self.prepared()
        first = self.launch(comp)
        self.finish(first, winner=None, result={'winner':None,'reason':'threefold_repetition'}, final_state={'result':{'winner':None,'reason':'threefold_repetition'}})
        self.comp.tick()
        second = self.take_job()
        self.finish(second, winner=None, result={'winner':None,'reason':'double_startup_failure'}, final_state={'result':{'winner':None,'reason':'double_startup_failure'}})
        standings = self.comp.standings(comp['id'])
        self.assertFalse(standings['provisional'])
        self.assertEqual([(r['rank'],r['played'],r['wins'],r['draws'],r['losses']) for r in standings['items']], [(1,2,0,1,1)]*2)
        self.assertEqual([r['points'] for r in standings['items']], [1, 1])
        self.comp.reconcile()
        self.assertEqual(self.comp.standings(comp['id']), standings)

    def test_points_rank_draws_and_wins_equally_at_three_points(self):
        comp = self.create_comp(max_teams=4)
        teams = []
        for i in range(4):
            client, credential = self.create_team('Points Team '+str(i), 'points-team-'+str(i))
            team = credential['account']['team_id']; teams.append(team)
            self.post(client, '/competitions/'+comp['id']+'/entry', self.qualified(team, comp['config']))
        self.post(self.client, '/admin/competitions/'+comp['id']+'/lock')
        initial = self.comp.standings(comp['id'])
        self.assertEqual(initial['scoring'], {'win':3,'draw':1,'loss':0})
        self.assertEqual([(r['rank'],r['points']) for r in initial['items']], [(1,0)]*4)
        rows = self.comp.schedule(comp['id'])['items']
        used = set()
        def result(a, b, winner=None, status='completed'):
            fixture = next(f for f in rows if f['id'] not in used and {f['red_team_id'],f['blue_team_id']}=={a,b})
            used.add(fixture['id'])
            side = None if winner is None else 'red' if fixture['red_team_id']==winner else 'blue'
            value = {'winner':side,'reason':'threefold_repetition' if side is None else 'base_capture'}
            with self.store.db:
                self.store.db.execute('UPDATE competition_fixtures SET status=?,result=? WHERE id=?', (status,json.dumps(value),fixture['id']))
        a,b,c,d = teams
        result(a,c);result(a,c);result(a,d)  # Three draws = three points.
        result(b,d,b);result(b,c,c)         # One win + one loss = three points.
        result(b,d,b,status='attention')   # Unscored errors never earn points.
        scores = self.comp.standings(comp['id'])
        by_team = {r['team_id']:r for r in scores['items']}
        self.assertEqual(scores['ranking'], 'points_shared_rank')
        self.assertEqual([(r['points'],r['rank']) for r in scores['items']], [(5,1),(3,2),(3,2),(1,4)])
        self.assertEqual((by_team[a]['wins'],by_team[a]['draws'],by_team[a]['rank']), (0,3,2))
        self.assertEqual((by_team[b]['wins'],by_team[b]['losses'],by_team[b]['rank']), (1,1,2))
        self.assertEqual(by_team[b]['point_breakdown'], {'wins':3,'draws':0,'losses':0})
        self.assertEqual(self.comp.standings(comp['id']), scores)

    def test_points_migration_preserves_pinned_runtime_and_results_once(self):
        from arena_api.migrations import migrate
        comp, _, _, _ = self.prepared()
        job = self.launch(comp); self.finish(job)
        before = self.comp.schedule(comp['id'], True)['items']
        original = self.comp.get(comp['id'])
        old_config = {k:v for k,v in original['config'].items() if k!='scoring'}
        old_config['ranking'] = 'wins_shared_rank'
        with self.store.db:
            self.store.db.execute('UPDATE competitions SET config=? WHERE id=?', (json.dumps(old_config),comp['id']))
            self.store.db.execute('DELETE FROM schema_migrations WHERE version=4')
        migrate(self.store)
        migrated = self.comp.get(comp['id'])
        self.assertEqual(migrated['config'], {**old_config,'ranking':'points_shared_rank','scoring':{'win':3,'draw':1,'loss':0}})
        self.assertEqual(migrated['revision'], original['revision']+1)
        self.assertEqual(self.comp.schedule(comp['id'],True)['items'], before)
        audits = self.comp.rows("SELECT * FROM audit WHERE action='competition.points_ranking_migrated' AND target=?", (comp['id'],))
        self.assertEqual(len(audits), 1)
        migrate(self.store)
        self.assertEqual(self.comp.get(comp['id']), migrated)
        self.assertEqual(self.comp.rows("SELECT * FROM audit WHERE action='competition.points_ranking_migrated' AND target=?", (comp['id'],)), audits)

    def test_official_dispatch_uses_reserved_slot_before_practice(self):
        comp, _, _, _ = self.prepared()
        for i in range(15):
            self.app.state.service.pending.put_nowait('practice-unit-' + str(i))
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        self.comp.tick()
        job = self.take_job()
        self.assertIn('official', job)
        self.assertEqual(self.app.state.service.pending.qsize(), 15)
        self.finish(job)
        # Worker hook reconciles and dispatches before taking the next queued job.
        self.app.state.service.before_next()
        self.assertIn('official', self.take_job())
        for i in range(15):
            self.assertEqual(self.app.state.service.pending.get_nowait(), 'practice-unit-' + str(i))
            self.app.state.service.pending.task_done()

    def test_legacy_policy_rejects_draw_and_new_reports_require_policy_match(self):
        from arena.adjudication import LEGACY
        comp, _, _, _ = self.prepared()
        config = {**comp['config']}
        config.pop('adjudication')
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE competitions SET config=? WHERE id=?', (json.dumps(config),comp['id']))
        first = self.launch(comp)
        self.assertEqual(first['official']['adjudication'], LEGACY)
        self.finish(first, winner=None, result={'winner':None,'reason':'ply_limit'}, final_state={'result':{'winner':None,'reason':'ply_limit'}})
        self.assertEqual(self.comp.view(comp['id'])['fixture_counts']['attention'], 1)
        self.assertTrue(all(r['played']==0 for r in self.comp.standings(comp['id'])['items']))
        self.comp.tick()
        second = self.take_job()
        # Reports produced before this upgrade omitted the policy field.
        result = {'winner':'red','reason':'base_capture'}
        self.store.update(second['id'],status='completed',report={'eligible_for_tournament':True,'result':result,'final_state':{'result':result},
            'image_id':second['image_id'],'engine_sha256':second['engine_sha256'],'profile':second['profile']})
        self.comp.reconcile()
        self.assertEqual(self.comp.view(comp['id'])['fixture_counts']['completed'],1)

    def test_failed_attempt_is_unscored_retry_keeps_history_and_old_result_cannot_count(self):
        comp, _, _, _ = self.prepared()
        first = self.launch(comp)
        self.store.update(first["id"], status="failed", error={"code": "worker_error", "detail": "private diagnostic"})
        self.comp.reconcile()
        self.assertTrue(all(s["played"] == 0 for s in self.comp.standings(comp["id"])["items"]))
        fixture = first["official"]["fixture_id"]
        self.assertEqual(self.comp.attempts(comp["id"], fixture)["items"][0]["error_code"], "worker_error")
        path = f"/admin/competitions/{comp['id']}/fixtures/{fixture}/retry"
        one = self.post(self.client, path, key="retry-exactly-once")
        self.assertEqual(one, self.post(self.client, path, key="retry-exactly-once"))
        self.comp.tick()
        second = self.take_job()
        self.assertNotEqual(first["id"], second["id"])
        self.finish(first)  # A stale attempt's completion must never earn points.
        self.assertTrue(all(s["played"] == 0 for s in self.comp.standings(comp["id"])["items"]))
        self.finish(second)
        self.assertEqual(sum(s["wins"] for s in self.comp.standings(comp["id"])["items"]), 1)
        self.assertEqual(len(self.comp.attempts(comp["id"], fixture)["items"]), 2)

    def test_pause_and_queue_capacity_do_not_lose_or_duplicate_fixtures(self):
        comp, _, _, _ = self.prepared()
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        for i in range(16):
            self.app.state.service.pending.put_nowait("practice-unit-" + str(i))
        self.comp.tick()
        self.assertEqual(self.comp.view(comp["id"])["fixture_counts"]["pending"], 2)
        for _ in range(16):
            self.app.state.service.pending.get_nowait()
            self.app.state.service.pending.task_done()
        self.post(self.client, f"/admin/competitions/{comp['id']}/pause")
        self.comp.tick()
        self.assertTrue(self.app.state.service.pending.empty())
        self.post(self.client, f"/admin/competitions/{comp['id']}/start")
        self.comp.tick()
        for _ in range(5):
            self.comp.tick()
        self.assertEqual(self.app.state.service.pending.qsize(), 1)
        self.assertEqual(self.comp.view(comp["id"])["fixture_counts"], {"pending": 1, "queued": 1, "completed": 0, "attention": 0})

    def test_completed_report_with_invalid_provenance_never_scores(self):
        comp, _, _, _ = self.prepared()
        job = self.launch(comp)
        mutations = [{"eligible_for_tournament": False}, {"image_id": "different"},
                     {"final_state": {"result": None}}, {"error": {"code": "worker_error"}}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.finish(job, **mutation)
                self.assertTrue(all(s["played"] == 0 for s in self.comp.standings(comp["id"])["items"]))
                fixture = job["official"]["fixture_id"]
                self.assertEqual(self.comp.attempts(comp["id"], fixture)["items"][-1]["status"], "attention")
                self.post(self.client, f"/admin/competitions/{comp['id']}/fixtures/{fixture}/retry")
                self.comp.tick()
                job = self.take_job()
        self.finish(job)
        self.assertEqual(sum(s["wins"] for s in self.comp.standings(comp["id"])["items"]), 1)

    def test_restart_pauses_competition_and_marks_inflight_unscored(self):
        comp, _, _, _ = self.prepared()
        self.launch(comp)
        restarted = Store(self.settings.data)
        try:
            accounts = Accounts(restarted)
            manager = Competitions(restarted, Service(restarted, start_worker=False), accounts)
            view = manager.view(comp["id"])
            self.assertEqual(view["status"], "paused")
            self.assertEqual(view["fixture_counts"]["attention"], 1)
            self.assertEqual(view["fixture_counts"]["pending"], 1)
            self.assertTrue(all(s["played"] == 0 for s in manager.standings(comp["id"])["items"]))
        finally:
            restarted.db.close()

    def test_safe_replay_excludes_build_logs_benchmark_metrics_and_paths(self):
        comp, clients, _, _ = self.prepared()
        job = self.launch(comp)
        self.store.event(job["id"], "build", {"log": "SECRET source snippet", "source_path": "/private/path"})
        self.store.event(job["id"], "state", {"state": engine.initial_state(), "counts": {}, "stderr": "SECRET"})
        self.store.event(job["id"], "turn_started", {"side": "red", "budget_ms": 15000, "estimated_started_unix_ms": 123, "clock_uncertainty_ms": 1, "source": "SECRET"})
        self.finish(job)
        path = f"/api/v1/competitions/{comp['id']}/fixtures/{job['official']['fixture_id']}/events"
        response = clients[0].get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e["type"] for e in response.json()["items"]], ["state", "turn_started", "completed"])
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("source_path", response.text)
        wire = clients[0].get(path, headers={"Accept": "text/event-stream", "Last-Event-ID": job["id"] + ":3"}).text
        self.assertIn("id: " + job["id"] + ":4", wire)
        self.assertNotIn("id: " + job["id"] + ":3", wire)
        self.assertNotIn("SECRET", wire)
        old_cursor = "0" * 32 + ":999"
        self.assertEqual(len(clients[0].get(path, headers={"Last-Event-ID": old_cursor}).json()["items"]), 3)
        self.assertEqual(clients[0].get(path, headers={"Last-Event-ID": "-1"}).status_code, 400)
        self.assertEqual(clients[0].get(f"/api/v1/admin/competitions/{comp['id']}/fixtures/{job['official']['fixture_id']}/attempts").status_code, 403)

    def test_official_worker_refuses_changed_binary_or_runtime(self):
        comp, _, _, _ = self.prepared()
        job = self.launch(comp)
        with patch.object(self.app.state.service, "ensure_artifact", return_value=(None, {"artifact_sha256": "different"})), patch("arena.sandbox.Worker") as worker:
            with self.assertRaises(Fault) as failure:
                self.app.state.service.execute(job)
            self.assertEqual(failure.exception.code, "provenance_mismatch")
            worker.assert_not_called()
        job["official"]["engine_sha256"] = "different"
        with self.assertRaises(Fault) as failure:
            self.app.state.service.execute(job)
        self.assertEqual(failure.exception.code, "provenance_mismatch")

    def test_admin_mutations_are_idempotent_and_drafts_private(self):
        team, _ = self.create_team()
        body = {"title": "Idempotent cup"}
        first = self.post(self.client, "/admin/competitions", body, key="create-the-same-cup", expected=201)
        second = self.post(self.client, "/admin/competitions", body, key="create-the-same-cup", expected=201)
        self.assertEqual(first, second)
        with patch("arena.sandbox.image_id", side_effect=Fault("worker_unavailable", "offline")):
            self.assertEqual(first, self.post(self.client, "/admin/competitions", body, key="create-the-same-cup", expected=201))
        self.post(self.client, "/admin/competitions", {"title": "A different cup"}, key="create-the-same-cup", expected=409)
        self.assertEqual(team.get("/api/v1/competitions").json()["items"], [])
        self.assertEqual(team.get("/api/v1/competitions/" + first["id"]).status_code, 404)
        self.post(team, "/admin/competitions", body, expected=403)
        response = self.client.post("/api/v1/admin/competitions", json=body, headers={"X-CSRF-Token": "wrong", "Idempotency-Key": "csrf-test-only"})
        self.assertEqual(response.status_code, 403)

    def test_entry_withdraw_replace_and_admin_remove_only_before_lock(self):
        comp = self.create_comp()
        team, credentials = self.create_team()
        body = self.qualified(credentials["account"]["team_id"], comp["config"])
        path = f"/competitions/{comp['id']}"
        self.post(team, path + "/entry", body)
        self.post(team, path + "/withdraw", key="withdraw-once")
        self.post(team, path + "/withdraw", key="withdraw-once")
        self.assertEqual(self.comp.view(comp["id"])["entries"], [])
        self.post(team, path + "/entry", body)
        self.post(self.client, f"/admin/competitions/{comp['id']}/entries/{credentials['account']['team_id']}/remove")
        self.assertEqual(self.comp.view(comp["id"])["entries"], [])

    def seed_entries(self, comp, count):
        with self.store.lock, self.store.db:
            for index in range(count):
                team = uuid.uuid4().hex
                self.store.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,?,?,?,?)", (team, f"Seed {index}", f"seed-{index}", "private@example.test", "", "[]", now_ms()))
                self.store.db.execute("INSERT INTO accounts VALUES(?,?,?,'team',?,0,0,0,?)", (uuid.uuid4().hex, f"seed-{index}", "unit-only-unused-hash", team, now_ms()))
        for (team,) in self.store.db.execute("SELECT id FROM teams").fetchall():
            body = self.qualified(team, comp["config"])
            self.comp.enter("unit-seed-" + team, team, comp["id"], body, uuid.uuid4().hex)

    def test_odd_team_schedule_has_byes_and_lock_rolls_back_atomically(self):
        comp = self.create_comp()
        self.seed_entries(comp, 3)
        with self.store.lock, self.store.db:
            self.store.db.execute("CREATE TRIGGER unit_lock_failure BEFORE INSERT ON competition_fixtures WHEN NEW.ordinal=2 BEGIN SELECT RAISE(ABORT,'injected write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.comp.lock("unit-admin", comp["id"], "atomic-lock-key")
        self.assertEqual(self.comp.view(comp["id"])["status"], "open")
        self.assertEqual(self.comp.schedule(comp["id"])["total"], 0)
        with self.store.lock, self.store.db:
            self.store.db.execute("DROP TRIGGER unit_lock_failure")
        result = self.comp.lock("unit-admin", comp["id"], "atomic-lock-key")
        self.assertEqual(result["fixture_count"], 6)
        pairs = self.store.db.execute("SELECT red_team_id,blue_team_id FROM competition_fixtures").fetchall()
        self.assertEqual(len(set(pairs)), 6)
        for red, blue in pairs:
            self.assertNotEqual(red, blue)
            self.assertIn((blue, red), pairs)

    def test_full_32_team_schedule_is_complete_balanced_and_durable(self):
        comp = self.create_comp(max_teams=32)
        # Seed approved accounts without 32 expensive password hashes; the
        # normal registration/credential guards have separate HTTP coverage.
        self.seed_entries(comp, 32)
        locked = self.post(self.client, f"/admin/competitions/{comp['id']}/lock", key="lock-complete-schedule")
        self.assertEqual(locked["fixture_count"], 32 * 31)
        again = self.post(self.client, f"/admin/competitions/{comp['id']}/lock", key="lock-complete-schedule")
        self.assertEqual(again, locked)
        rows = self.store.db.execute("SELECT red_team_id,blue_team_id FROM competition_fixtures").fetchall()
        self.assertEqual(len(set(rows)), 32 * 31)
        teams = {r[0] for r in rows}
        for team in teams:
            self.assertEqual(sum(r[0] == team for r in rows), 31)
            self.assertEqual(sum(r[1] == team for r in rows), 31)
        self.launch(locked)
        self.assertEqual(self.comp.view(comp["id"])["fixture_counts"]["pending"], 991)
