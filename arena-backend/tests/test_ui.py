"""Same-origin UI boundary and the small summaries used by the real screens."""
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient

from arena_api.app import create_app
from arena_api.arena_routes import public_job
from arena_api.settings import Settings
from test_web_api import APIHarness


class UIIntegration(APIHarness):
    def test_ui_and_public_rules_do_not_require_session(self):
        guest = self.other_client()
        english=guest.get('/api/v1/rules?lang=en')
        self.assertEqual(english.status_code,200)
        self.assertIn('10 seconds per bot',english.text)
        self.assertEqual(guest.get('/api/v1/rules?lang=../x').status_code,422)
        page = guest.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('/_ui/app.js', page.text)
        self.assertNotIn('src="/app.js"', page.text)
        self.assertIn("script-src 'self'", page.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])
        for asset in ("i18n.js", "en.js", "bot-guide.en.md", "bot-guide.js", "bot-guide.md", "bot-example.cpp", "ottv2-input.jsonl"):
            result = guest.get("/_ui/" + asset)
            self.assertEqual(result.status_code, 200, asset)
            self.assertTrue(result.content, asset)
        for path in ("/_ui/app.js", "/_ui/client.js", "/_ui/pages.js", "/_ui/board.js", "/_ui/patch.js", "/_ui/admin-ui.js", "/_ui/app.css", "/styles.css", "/assets/favicon.svg"):
            self.assertEqual(guest.get(path).status_code, 200, path)
        self.assertNotIn("@import", guest.get("/styles.css").text)
        self.assertEqual(guest.get("/api/v1/platform").json()["runtimes"], ["cpp17", "c11", "python3"])
        rules = guest.get("/api/v1/rules")
        self.assertEqual(rules.status_code, 200)
        self.assertIn("OTT", rules.text)
        self.assertIn('## 2. Bàn cờ', rules.text)
        self.assertIn('rules-v1', guest.get('/api/v1/experimental/guide').text)
        self.assertIn("text/plain", guest.get("/openapi.json").json()["paths"]["/api/v1/rules"]["get"]["responses"]["200"]["content"])

    def test_static_boundary_never_exposes_backend_or_team_source(self):
        guest = self.other_client()
        for path in ("/_ui/%2e%2e/%2e%2e/arena-backend/arena_api/accounts.py", "/.platform-state/arena.sqlite", "/sdk/bot.cpp", "/app.js", "/assets/starter-cpp/bot.cpp"):
            self.assertEqual(guest.get(path).status_code, 404, path)
        self.assertEqual(guest.get("/api/v1/admin/dashboard").status_code, 401)
        team, _ = self.create_team()
        self.assertEqual(team.get("/api/v1/admin/dashboard").status_code, 403)
        summary = self.client.get("/api/v1/admin/dashboard")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["teams"], 1)
        self.assertEqual(summary.json()["pending_registrations"], 0)
        self.assertNotIn("password", summary.text)

    def test_team_lists_do_not_decode_detailed_sample_and_build_history(self):
        import tracemalloc
        team,issued=self.create_team()
        owner=issued['account']['team_id'];store=self.app.state.store
        with store.transaction():
            for _ in range(300):
                saved=store.create('job',owner,dict(kind='benchmark',status='completed',submission_ids=[],
                    report=dict(passed=True,samples=[dict(wall_ms=1,detail='x'*128)]*72,artifacts=[dict(log='PRIVATE-BUILD-'+'x'*32768)])))
        tracemalloc.start()
        try:
            dashboard=team.get('/api/v1/dashboard');listing=team.get('/api/v1/jobs?limit=5&offset=2')
            _,peak=tracemalloc.get_traced_memory()
            self.assertEqual((dashboard.status_code,listing.status_code),(200,200))
            self.assertEqual(dashboard.json()['counts']['benchmarks'],300)
            self.assertEqual((listing.json()['total'],len(listing.json()['items'])),(300,5))
            self.assertTrue(all(j['benchmark_summary']['passed'] and j['benchmark_summary']['samples']==72 for j in listing.json()['items']))
            self.assertNotIn('PRIVATE-BUILD',listing.text+dashboard.text)
            self.assertLess(peak,8*1024**2,'List endpoints decoded full retained reports')
        finally:tracemalloc.stop()
        detail=team.get('/api/v1/jobs/'+saved['id'])
        self.assertEqual(len(detail.json()['report']['samples']),72)
        self.assertIn('PRIVATE-BUILD',detail.text)

    def test_job_list_summary_does_not_include_private_report(self):
        job = {"id": "a" * 32, "kind": "benchmark", "status": "completed", "report": {"passed": True,
               "samples": [{}] * 72, "suite_id": "ottv2-public-smoke-v1", "turn_wall_ms": {"samples": 72},
               "build_log": "private diagnostic"}}
        summary = public_job(job)
        self.assertEqual(summary["benchmark_summary"]["samples"], 72)
        self.assertTrue(summary["benchmark_summary"]["passed"])
        self.assertNotIn("report", summary)
        self.assertNotIn("private diagnostic", str(summary))
        self.assertIs(public_job(job, full=True), job)

    def test_missing_ui_keeps_api_docs_available(self):
        with TemporaryDirectory() as directory:
            app = create_app(Settings(data=Path(directory)/"db", ui_root=Path(directory)/"missing"), start_worker=False)
            with TestClient(app, base_url="http://127.0.0.1:4181", follow_redirects=False) as client:
                self.assertEqual(client.get("/").headers["location"], "/docs")
                self.assertEqual(client.get("/docs").status_code, 200)
