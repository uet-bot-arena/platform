"""Real two-team, two-leg competition through HTTP + Docker; no worker mocks."""
import json
import io
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import uuid
import zipfile

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import engine, sandbox
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings


def main():
    image = sandbox.image_id('uet-arena-worker:clock-v2')
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    report = {"image_id": image, "started_ms": time.time_ns() // 1_000_000, "checks": []}

    def checked(name, **data):
        report["checks"].append({"name": name, "passed": True, **data})
        print("PASS", name, flush=True)

    with tempfile.TemporaryDirectory(prefix="competition-integration-", dir=output) as directory:
        store = Store(directory, recover_jobs=False)
        Accounts(store).bootstrap_admin("organizer", "integration-only-admin-password")
        store.db.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        settings = Settings(data=Path(directory), origin=f"http://127.0.0.1:{sock.getsockname()[1]}")
        app = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning", proxy_headers=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        admin, a, b = [httpx.Client(base_url=settings.origin, timeout=90) for _ in range(3)]
        try:
            end = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < end:
                time.sleep(0.05)
            assert server.started, "Uvicorn did not start"

            def request(client, method, path, expected=200, **kwargs):
                if method == "POST":
                    kwargs["headers"] = {"Idempotency-Key": uuid.uuid4().hex, **kwargs.get("headers", {})}
                    if "json" not in kwargs and "content" not in kwargs:
                        kwargs["json"] = {}
                response = client.request(method, "/api/v1" + path, **kwargs)
                assert response.status_code == expected, (path, response.status_code, response.text[:1500])
                return response

            def login(client, username, password):
                data = request(client, "POST", "/auth/login", json={"username": username, "password": password}).json()
                client.headers["X-CSRF-Token"] = data["csrf_token"]

            login(admin, "organizer", "integration-only-admin-password")
            teams, sources, benchmarks = [], [], []
            for index, client in enumerate((a, b)):
                registration = request(client, "POST", "/registrations", expected=201, json={
                    "team": f"Competition Team {index}", "email": f"private-{index}@example.test", "members": [
                        {"name": f"Student {member}", "student_id": f"2602000{member}"} for member in range(3)], "confirmed": True}).json()
                credential = request(admin, "POST", f"/admin/registrations/{registration['id']}/approve", expected=201, json={"username": f"competition-{index}"}).json()
                teams.append(credential["account"]["team_id"])
                login(client, f"competition-{index}", credential["temporary_password"])
                changed = request(client, "POST", "/auth/password", json={"current_password": credential["temporary_password"], "new_password": f"integration-only-team-{index}-password"}).json()
                client.headers["X-CSRF-Token"] = changed["csrf_token"]
                starter = request(client, "GET", "/starter.zip").content
                sources.append(request(client, "POST", "/submissions", expected=201, content=starter,
                    headers={"Content-Type": "application/zip", "X-Submission-Version": "v1.0.0"}).json())

            def wait_job(client, ident):
                end = time.monotonic() + 180
                while time.monotonic() < end:
                    result = request(client, "GET", "/jobs/" + ident).json()
                    if result["status"] in ("completed", "failed", "aborted"):
                        assert result["status"] == "completed", result.get("error")
                        return result
                    time.sleep(0.1)
                raise AssertionError("Benchmark did not complete")

            for client, source in zip((a, b), sources):
                queued = request(client, "POST", "/benchmarks", expected=202, json={"submission_id": source["id"]}).json()
                benchmarks.append(wait_job(client, queued["id"]))
            assert all(j["report"]["passed"] and len(j["report"]["samples"]) == 72 for j in benchmarks)
            checked("two_independent_teams_upload_and_qualify_native_cpp", samples_per_team=72)

            key = uuid.uuid4().hex
            comp = request(admin, "POST", "/admin/competitions", expected=201, json={"title": "Real OTTv2 two-leg cup", "max_teams": 2}, headers={"Idempotency-Key": key}).json()
            duplicate = request(admin, "POST", "/admin/competitions", expected=201, json={"title": "Real OTTv2 two-leg cup", "max_teams": 2}, headers={"Idempotency-Key": key}).json()
            assert comp == duplicate
            ident = comp["id"]
            request(a, "GET", "/competitions/" + ident, expected=404)
            request(admin, "POST", f"/admin/competitions/{ident}/open")
            entry_a = {"submission_id": sources[0]["id"], "benchmark_id": benchmarks[0]["id"], "delegate_timeout_claims": True}
            request(b, "POST", f"/competitions/{ident}/entry", expected=404, json=entry_a)
            for client, source, benchmark in zip((a, b), sources, benchmarks):
                request(client, "POST", f"/competitions/{ident}/entry", json={"submission_id": source["id"], "benchmark_id": benchmark["id"], "delegate_timeout_claims": True})
            lock_key = uuid.uuid4().hex
            locked = request(admin, "POST", f"/admin/competitions/{ident}/lock", headers={"Idempotency-Key": lock_key}).json()
            assert locked["fixture_count"] == 2
            assert request(admin, "POST", f"/admin/competitions/{ident}/lock", headers={"Idempotency-Key": lock_key}).json() == locked
            request(a, "POST", f"/competitions/{ident}/entry", expected=409, json=entry_a)
            checked("qualified_entries_lock_idempotently_and_versions_cannot_change")

            schedule = request(a, "GET", f"/competitions/{ident}/fixtures").json()["items"]
            assert (schedule[0]["red_team_id"], schedule[0]["blue_team_id"]) == (schedule[1]["blue_team_id"], schedule[1]["red_team_id"])
            assert set((schedule[0]["red_team_id"], schedule[0]["blue_team_id"])) == set(teams)
            request(admin, "POST", f"/admin/competitions/{ident}/start")
            fixture = schedule[0]["id"]
            path = f"/competitions/{ident}/fixtures/{fixture}/events"
            end = time.monotonic() + 30
            while time.monotonic() < end:
                if request(a, "GET", path).json()["attempt_id"]:
                    break
                time.sleep(0.05)
            # Read a live stream while the worker is running; no page reload.
            with a.stream("GET", "/api/v1" + path, headers={"Accept": "text/event-stream"}) as response:
                assert response.status_code == 200
                wire = "\n".join(response.iter_lines())
            assert '"board"' in wire and "event: arena" in wire
            assert "source_path" not in wire and "artifact_sha256" not in wire and '"artifacts"' not in wire
            checked("cross_team_live_sse_updates_board_and_excludes_private_worker_data")

            end = time.monotonic() + 180
            while time.monotonic() < end:
                view = request(a, "GET", "/competitions/" + ident).json()
                assert view["fixture_counts"]["attention"] == 0, view
                if view["status"] == "completed":
                    break
                time.sleep(0.1)
            assert view["status"] == "completed", view
            finished = request(a, "GET", f"/competitions/{ident}/fixtures").json()["items"]
            assert all(f["status"] == "completed" for f in finished)
            assert all(f["result"] == {"winner": "blue", "reason": "base_capture"} for f in finished)
            for f in finished:
                attempts = request(admin, "GET", f"/admin/competitions/{ident}/fixtures/{f['id']}/attempts").json()["items"]
                assert len(attempts) == 1 and attempts[0]["status"] == "completed"
                job = request(admin, "GET", f"/admin/competitions/{ident}/fixtures/{f['id']}/attempts/1").json()
                assert job["report"]["eligible_for_tournament"] is True
                assert job["report"]["final_state"]["ply"] == 30
                expected = [next(s for s in sources if s["team_id"] == f[side + "_team_id"])["id"] for side in engine.SIDES]
                assert job["submission_ids"] == expected
                for client in (a, b):
                    request(client, "GET", "/jobs/" + job["id"], expected=404)
                    request(client, "GET", f"/admin/competitions/{ident}/fixtures/{f['id']}/attempts/1", expected=403)
                replay = request(a, "GET", f"/competitions/{ident}/fixtures/{f['id']}/events").json()
                states = [e["data"]["state"] for e in replay["items"] if e["type"] == "state"]
                assert states[0] == engine.initial_state() and states[-1] == job["report"]["final_state"]
                cursor = replay["items"][len(replay["items"]) // 2]["seq"]
                resumed = request(a, "GET", f"/competitions/{ident}/fixtures/{f['id']}/events",
                    headers={"Accept": "text/event-stream", "Last-Event-ID": job["id"] + ":" + str(cursor)}).text
                assert f"id: {job['id']}:{cursor}\n" not in resumed
            checked("both_official_legs_execute_exact_locked_binaries_and_replay_safely", plies_per_leg=30)

            standings = request(a, "GET", f"/competitions/{ident}/standings").json()
            assert not standings["provisional"]
            assert [(s["rank"], s["wins"], s["losses"], s["played"]) for s in standings["items"]] == [(1, 1, 1, 2)] * 2
            assert standings["ranking"] == "points_shared_rank"
            assert standings["scoring"] == {"win": 3, "draw": 1, "loss": 0}
            assert [s["points"] for s in standings["items"]] == [3, 3]
            for _ in range(3):
                assert request(a, "GET", f"/competitions/{ident}/standings").json() == standings
            request(admin, "POST", f"/admin/competitions/{ident}/fixtures/{fixture}/retry", expected=409)
            shared = request(a, "GET", "/competitions/" + ident).text
            assert "@example.test" not in shared and "student_id" not in shared
            report["standings"] = standings
            checked("points_standings_shared_rank_no_double_count_no_completed_retry")

            # These bots qualify through the real 72-case benchmark, then run
            # unchanged binaries in official fixtures. No DB result injection.
            starter_code = (ROOT / 'sdk' / 'bot.cpp').read_text()
            repeat_function = '''
json choose_repeat(const json& state, const json& legal) {
    const bool red = state.at("to_move") == "red";
    const std::string from[] = {red ? "a2" : "b9", red ? "b3" : "a9"};
    const std::string to[] = {red ? "b3" : "a9", red ? "a2" : "b9"};
    for (int i=0;i<2;i++) for (const auto& move : legal)
        if (move.at("from")==from[i] && move.at("to")==to[i]) return move;
    return legal.at(0);
}
'''
            repeat_code = starter_code.replace('int main() {', repeat_function + '\nint main() {').replace('choose_move(m.at("payload").at("state"), legal)', 'choose_repeat(m.at("payload").at("state"), legal)')
            init_line = 'match = m.at("match_id");'
            single_failure = starter_code.replace('#include <string>', '#include <string>\n#include <unistd.h>').replace(init_line,
                'if (m.at("payload").contains("match") && m.at("payload").at("match").at("kind")=="match" && m.at("payload").at("side")=="blue") usleep(2300000);\n' + init_line)
            double_failure = starter_code.replace(init_line,
                'if (m.at("payload").contains("match") && m.at("payload").at("match").at("kind")=="match") return 42;\n' + init_line)

            for version, code, reason in [('v2.0.0',repeat_code,'threefold_repetition'), ('v2.0.1',single_failure,'startup_failure'), ('v2.0.2',double_failure,'double_startup_failure')]:
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
                    zipped.writestr('bot.cpp', code)
                qualified = []
                for client in (a,b):
                    source = request(client, 'POST', '/submissions', expected=201, content=archive.getvalue(), headers={'Content-Type':'application/zip','X-Submission-Version':version}).json()
                    benchmark = request(client, 'POST', '/benchmarks', expected=202, json={'submission_id':source['id']}).json()
                    benchmark = wait_job(client, benchmark['id'])
                    qualified.append({'submission_id':source['id'],'benchmark_id':benchmark['id'],'delegate_timeout_claims':True})
                special = request(admin,'POST','/admin/competitions',expected=201,json={'title':'Adjudication '+reason,'max_teams':2}).json()
                sid = special['id']
                assert special['config']['adjudication']['id']=='ottv2-match-v2'
                request(admin,'POST',f'/admin/competitions/{sid}/open')
                for client,entry in zip((a,b),qualified):
                    request(client,'POST',f'/competitions/{sid}/entry',json=entry)
                request(admin,'POST',f'/admin/competitions/{sid}/lock')
                request(admin,'POST',f'/admin/competitions/{sid}/start')
                end = time.monotonic()+180
                while time.monotonic()<end:
                    view = request(a,'GET',f'/competitions/{sid}').json()
                    assert view['fixture_counts']['attention']==0, view
                    if view['status']=='completed': break
                    time.sleep(.1)
                assert view['status']=='completed', view
                fixtures = request(a,'GET',f'/competitions/{sid}/fixtures').json()['items']
                winner = 'red' if reason=='startup_failure' else None
                assert all(f['result']=={'winner':winner,'reason':reason} for f in fixtures), fixtures
                scores = request(a,'GET',f'/competitions/{sid}/standings').json()
                expected_scores = (2,0,2,0) if reason=='threefold_repetition' else (2,1,0,1) if reason=='startup_failure' else (2,0,0,2)
                assert [(r['played'],r['wins'],r['draws'],r['losses']) for r in scores['items']]==[expected_scores]*2, scores
                expected_points = 3 * expected_scores[1] + expected_scores[2]
                assert [r['points'] for r in scores['items']] == [expected_points] * 2, scores
                assert scores['ranking'] == 'points_shared_rank' and scores['scoring'] == {'win':3,'draw':1,'loss':0}
                replay = request(a,'GET',f"/competitions/{sid}/fixtures/{fixtures[0]['id']}/events").json()
                states = [e['data']['state'] for e in replay['items'] if e['type']=='state']
                assert states[-1]['result']=={'winner':winner,'reason':reason}
                assert states[-1]['ply']==(8 if reason=='threefold_repetition' else 0)
                checked('official_'+reason+'_completes_both_legs_with_correct_standings',scores=expected_scores)
            report["passed"] = True
        finally:
            for client in (admin, a, b):
                client.close()
            server.should_exit = True
            thread.join(timeout=360)
            sock.close()
            assert not thread.is_alive(), "API failed to stop worker/scheduler and release lease"
    report["completed_ms"] = time.time_ns() // 1_000_000
    (output / "competition-latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ALL COMPETITION INTEGRATION CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
