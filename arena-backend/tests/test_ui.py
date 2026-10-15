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
        page = guest.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('/_ui/app.js', page.text)
        self.assertNotIn('src="/app.js"', page.text)
        self.assertIn("script-src 'self'", page.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])
        for asset in ("bot-guide.js", "bot-guide.md", "bot-example.cpp", "ottv2-input.jsonl"):
            result = guest.get("/_ui/" + asset)
            self.assertEqual(result.status_code, 200, asset)
            self.assertTrue(result.content, asset)
        for path in ("/_ui/app.js", "/_ui/client.js", "/_ui/pages.js", "/_ui/board.js", "/_ui/patch.js", "/_ui/admin-ui.js", "/_ui/app.css", "/styles.css", "/assets/favicon.svg"):
            self.assertEqual(guest.get(path).status_code, 200, path)
        self.assertNotIn("@import", guest.get("/styles.css").text)
        self.assertEqual(guest.get("/api/v1/platform").json()["runtimes"], ["cpp17"])
        rules = guest.get("/api/v1/rules")
        self.assertEqual(rules.status_code, 200)
        self.assertIn("OTT", rules.text)
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
