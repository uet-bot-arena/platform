"""Four real browser registrations through Mailpit, one team and native bot submission."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena.conf import Secrets
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings
from email_integration import port, wait_http
from grader_browser import text_contrast
from grader_harness import start_grader, stop_grader

PASSWORD = "people-browser-test-only-password"


def main():
    output = ROOT / "test-results/m1/g6/browser"
    output.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "browser": [], "page_errors": []}
    def passed(name):
        report["checks"].append(name)
        print("PASS", name, flush=True)
    with tempfile.TemporaryDirectory(prefix="people-integration-") as directory:
        root = Path(directory)
        smtp, web, hub = port(), port(), port()
        origin, mailbox = f"http://127.0.0.1:{hub}", f"http://127.0.0.1:{web}"
        data = root / "hub"
        private = root / "secrets.toml"
        private.write_text("verification_hmac_key=" + json.dumps(secrets.token_urlsafe(32)))
        private.chmod(0o600)
        config = root / "config.toml"
        config.write_text(fr'''[hub]
data_dir={json.dumps(str(data))}
origin={json.dumps(origin)}
port={hub}
[[email.providers]]
name="mailpit"
host="127.0.0.1"
port={smtp}
tls="none"
sender="no-reply@vnu.test"
daily_limit=10000
hourly_limit=10000
[accounts]
student_email='^\d{{8}}@vnu\.test$'
staff_domains=["vnu.test"]
''')
        env = {**os.environ, "ARENA_CONFIG": str(config), "ARENA_SECRETS": str(private)}
        store = Store(data, recover_jobs=False)
        Accounts(store).bootstrap_admin("organizer", PASSWORD)
        store.db.close()
        app = create_app(Settings(config_file=config, secrets=Secrets.load(private)))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=hub, access_log=False, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        mailpit = subprocess.Popen([str(Path.home()/".local/bin/mailpit"), "--smtp", f"127.0.0.1:{smtp}", "--listen", f"127.0.0.1:{web}", "--database", str(root/"mailpit.db"), "--disable-version-check"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mailer, grader, publisher = None, None, None
        mailer_log = open(output/"mailer.log", "w")
        try:
            wait_http(mailbox, "/api/v1/info")
            thread.start()
            wait_http(origin, "/api/v1/health/live")
            mailer = subprocess.Popen([sys.executable, "-m", "arena_api.mailer"], cwd=ROOT, env=env, stdout=mailer_log, stderr=mailer_log)
            publisher = subprocess.Popen([sys.executable, "-m", "arena_api.publisher"], cwd=ROOT, env=env, stdout=mailer_log, stderr=mailer_log)
            wait_http(origin, "/snapshots/announcements.json")
            grader = start_grader(app, origin, "bwrap", slots=2, name="personal integration bwrap")
            def code_for(email):
                until = time.monotonic()+30
                while time.monotonic()<until:
                    for msg in httpx.get(mailbox+"/api/v1/messages").json()["messages"]:
                        if any(to["Address"]==email for to in msg["To"]):
                            return re.search(r": (\d{6})$", msg["Subject"]).group(1)
                    time.sleep(.1)
                raise AssertionError("Missing Mailpit code")
            def contrast(page):
                rows = text_contrast(page)
                assert rows and min(row["ratio"] for row in rows)>=4.5, rows
                return min(row["ratio"] for row in rows)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for offset, scheme in enumerate(("light", "dark")):
                    contexts, pages, emails, minima = [], [], [], []
                    for i in range(4):
                        context = browser.new_context(color_scheme=scheme, reduced_motion="reduce", viewport={"width":1440,"height":1100})
                        contexts.append(context)
                        page = context.new_page()
                        pages.append(page)
                        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                        address = f"26000{offset}{i:02d}@vnu.test"
                        emails.append(address)
                        page.goto(origin+"/#/register")
                        page.locator("[name=email]").fill(address)
                        page.locator("#email-request-form button[type=submit]").click()
                        expect(page.locator("#email-code-form")).to_be_visible()
                        page.locator("[name=email-code]").fill(code_for(address))
                        page.locator("#email-code-form button[type=submit]").click()
                        expect(page.locator("#personal-password-form")).to_be_visible()
                        if i==0:
                            minima.append(contrast(page))
                            page.screenshot(path=str(output/f"{scheme}-password.png"), full_page=True)
                        page.locator("[name=display-name]").fill(f"Thành viên {i+1} {scheme}")
                        page.locator("[name=new-password]").fill(PASSWORD)
                        page.locator("[name=confirm-password]").fill(PASSWORD)
                        page.locator("#personal-password-form button[type=submit]").click()
                        expect(page.locator("#main h2")).to_have_text("Đã tạo tài khoản")
                        page.goto(origin+"/#/login")
                        page.locator("[name=username]").fill(address)
                        page.locator("[name=login-password]").fill(PASSWORD)
                        page.locator("#login-form button[type=submit]").click()
                        expect(page.locator(".sidebar")).to_be_visible()
                        page.goto(origin+"/#/team")
                        expect(page.locator("#people-page")).to_be_visible()
                        if i==0:
                            page.locator("#personal-team-create [name=team-name]").fill("Đội bốn người "+scheme)
                            page.locator("#personal-team-create button[type=submit]").click()
                            expect(page.locator("#people-page")).to_contain_text("1/4 thành viên")
                            own = page.request.get(origin+"/api/v1/users/me").json()
                            code = own["team"]["invite_code"]
                        else:
                            page.goto(origin+"/#/team?invite="+code)
                            expect(page.locator("#personal-team-join [name=invite-code]")).to_have_value(code)
                            page.locator("#personal-team-join button[type=submit]").click()
                            expect(page.locator("#people-page")).to_contain_text(f"{i+1}/4 thành viên")
                    passed(scheme+"_four_email_registrations_and_invite_team")
                    owner = pages[0]
                    owner.goto(origin+"/#/team")
                    owner.get_by_role("button", name="Cập nhật dữ liệu", exact=True).click()
                    expect(owner.locator("#people-page")).to_contain_text("4/4 thành viên")
                    expect(owner.locator("#people-page")).to_contain_text("Đã tắt")
                    background = owner.locator("body").evaluate("e=>getComputedStyle(e).backgroundColor")
                    assert background == ("rgb(17, 20, 24)" if scheme=="dark" else "rgb(240, 244, 241)"), background
                    minima.append(contrast(owner))
                    owner.screenshot(path=str(output/f"{scheme}-team.png"), full_page=True)
                    owner.set_viewport_size({"width":390,"height":844})
                    assert owner.evaluate("document.documentElement.scrollWidth<=innerWidth"), "mobile overflow"
                    owner.screenshot(path=str(output/f"{scheme}-team-mobile.png"), full_page=True)
                    member = pages[1]
                    member.goto(origin+"/#/matching")
                    expect(member.locator("#main h1")).to_have_text("Ghép đội")
                    for email in emails:
                        assert email not in member.locator("#main").inner_text()
                    minima.append(contrast(member))
                    member.screenshot(path=str(output/f"{scheme}-matching.png"), full_page=True)
                    member.goto(origin+"/#/submissions")
                    expect(member.locator("#github-page")).to_be_visible()
                    expect(member.locator('input[type=file]')).to_have_count(0)
                    starter = member.request.get(origin+"/api/v1/starter.zip")
                    assert starter.status==200
                    # This test covers Mailpit/rosters/native benchmark; GitHub has a dedicated E2E.
                    team_id = member.request.get(origin+"/api/v1/auth/me").json()["user"]["team_id"]
                    app.state.service.submit(team_id, f"v1.0.{offset}", "cpp17", starter.body(), "people-fixture-"+scheme)
                    submissions = owner.request.get(origin+"/api/v1/submissions").json()["items"]
                    assert len(submissions)==1, submissions
                    csrf = member.request.get(origin+"/api/v1/auth/me").json()["csrf_token"]
                    result = member.request.post(origin+"/api/v1/benchmarks", data={"submission_id":submissions[0]["id"],"match_ms":60000}, headers={"X-CSRF-Token":csrf,"Idempotency-Key":uuid.uuid4().hex})
                    assert result.status==202, result.text()
                    job = result.json()
                    until = time.monotonic()+180
                    while time.monotonic()<until:
                        job = owner.request.get(origin+"/api/v1/jobs/"+job["id"]).json()
                        if job["status"] in ("completed","failed","aborted"):
                            break
                        time.sleep(.25)
                    assert job["status"]=="completed" and job["report"]["passed"], job
                    assert job["report"]["backend"]=="bwrap", job["report"]
                    passed(scheme+"_member_upload_shared_with_captain_and_native_benchmark_72")
                    admin_context = browser.new_context(color_scheme=scheme, reduced_motion="reduce", viewport={"width":1440,"height":1100})
                    admin = admin_context.new_page()
                    admin.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                    admin.goto(origin+"/#/login")
                    admin.locator("[name=username]").fill("organizer")
                    admin.locator("[name=login-password]").fill(PASSWORD)
                    admin.locator("#login-form button[type=submit]").click()
                    expect(admin.locator(".sidebar")).to_be_visible()
                    admin.goto(origin+"/#/users")
                    expect(admin.locator("#people-admin")).to_contain_text(emails[0])
                    minima.append(contrast(admin))
                    admin.screenshot(path=str(output/f"{scheme}-admin.png"), full_page=True)
                    admin.set_viewport_size({"width":390,"height":844})
                    assert admin.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    assert admin.locator(".sidebar").evaluate("e=>e.getBoundingClientRect().right<=0"), "mobile menu must be closed"
                    admin.screenshot(path=str(output/f"{scheme}-admin-mobile.png"), full_page=True)
                    admin.locator("[name=team-deadline]").fill("2000-01-01T00:00")
                    admin.locator("#team-deadline-form button[type=submit]").click()
                    expect(admin.locator("#team-deadline-form button[type=submit]")).to_be_enabled()
                    owner.goto(origin+"/#/team")
                    owner.get_by_role("button", name="Cập nhật dữ liệu", exact=True).click()
                    expect(owner.locator("#people-page")).to_contain_text("Đã chốt")
                    current = owner.request.get(origin+"/api/v1/users/me").json()["team"]
                    target = next(p["id"] for p in current["roster"] if p["display_name"]==f"Thành viên 4 {scheme}")
                    for action in ("remove", "add"):
                        admin.locator("#admin-membership-form [name=team-id]").select_option(current["id"])
                        admin.locator("#admin-membership-form [name=user-id]").select_option(target)
                        admin.locator("#admin-membership-form [name=member-action]").select_option(action)
                        with admin.expect_response(lambda r: r.url.endswith("/membership") and r.request.method=="POST") as response:
                            admin.locator("#admin-membership-form button[type=submit]").click()
                        assert response.value.status==200, response.value.text()
                        expect(admin.locator("#admin-membership-form button[type=submit]")).to_be_enabled()
                    assert len(owner.request.get(origin+"/api/v1/users/me").json()["team"]["roster"])==4
                    # Re-open formation for the next isolated four-person browser group.
                    admin.locator("[name=team-deadline]").fill("")
                    admin.locator("#team-deadline-form button[type=submit]").click()
                    expect(admin.locator("#team-deadline-form button[type=submit]")).to_be_enabled()
                    passed(scheme+"_admin_deadline_locks_team_and_mobile_ui")
                    report["browser"].append({"scheme":scheme,"contrast_min":min(minima)})
                    admin_context.close()
                    for context in contexts:
                        context.close()
                browser.close()
            assert not report["page_errors"], report["page_errors"]
            print("ALL PEOPLE INTEGRATION CHECKS PASSED", flush=True)
        finally:
            if grader:
                stop_grader(grader)
            if mailer and mailer.poll() is None:
                mailer.terminate()
                mailer.wait(15)
            if publisher and publisher.poll() is None:
                publisher.terminate()
                publisher.wait(15)
            mailer_log.close()
            server.should_exit = True
            if thread.is_alive():
                thread.join(30)
                assert not thread.is_alive(), "hub did not stop"
            mailpit.terminate()
            mailpit.wait(10)
            (output/"integration.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
