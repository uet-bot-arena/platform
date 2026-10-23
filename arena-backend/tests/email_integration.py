"""Real HTTP hub + separate mailer process + isolated Mailpit; browser light/dark/mobile."""
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

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
from grader_browser import text_contrast

PASSWORD = "email-browser-test-only-password"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(origin, path):
    until = time.monotonic() + 20
    while time.monotonic() < until:
        try:
            if httpx.get(origin + path, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    raise AssertionError("Test HTTP server did not start")


def main():
    output = ROOT / "test-results/m1/g5"
    output.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "browser": [], "page_errors": []}
    def passed(name):
        report["checks"].append(name)
        print("PASS", name, flush=True)
    with tempfile.TemporaryDirectory(prefix="email-m1-") as directory:
        root = Path(directory)
        smtp_port, web_port, hub_port = port(), port(), port()
        mailbox, origin = f"http://127.0.0.1:{web_port}", f"http://127.0.0.1:{hub_port}"
        data = root / "hub"
        key = secrets.token_urlsafe(32)
        private = root / "secrets.toml"
        private.write_text("verification_hmac_key = " + json.dumps(key) + "\n")
        private.chmod(0o600)
        config = root / "config.toml"
        config.write_text(fr'''[hub]
data_dir = {json.dumps(str(data))}
origin = {json.dumps(origin)}
port = {hub_port}
[[email.providers]]
name = "mailpit"
host = "127.0.0.1"
port = {smtp_port}
tls = "none"
sender = "no-reply@vnu.test"
daily_limit = 4
hourly_limit = 4
[accounts]
student_email = '^\d{{8}}@vnu\.test$'
staff_domains = ["vnu.test"]
''', encoding="utf-8")
        env = {**os.environ, "ARENA_CONFIG": str(config), "ARENA_SECRETS": str(private)}
        store = Store(data, recover_jobs=False)
        Accounts(store).bootstrap_admin("organizer", PASSWORD)
        store.db.close()
        settings = Settings(config_file=config, secrets=Secrets.load(private))
        app = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=hub_port, access_log=False, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        mailer = None
        mailpit = subprocess.Popen([str(Path.home() / ".local/bin/mailpit"), "--smtp", f"127.0.0.1:{smtp_port}",
                                   "--listen", f"127.0.0.1:{web_port}", "--database", str(root / "mailpit.db"),
                                   "--disable-version-check"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = httpx.Client(base_url=origin, timeout=30)
        try:
            wait_http(mailbox, "/api/v1/info")
            thread.start()
            wait_http(origin, "/api/v1/health/live")
            def enqueue(address):
                response = client.post("/api/v1/auth/email/request", json={"email": address})
                assert response.status_code == 202, response.text
                return response.json()["id"]
            def code_for(address):
                until = time.monotonic() + 30
                while time.monotonic() < until:
                    for message in httpx.get(mailbox + "/api/v1/messages").json()["messages"]:
                        if any(to["Address"] == address for to in message["To"]):
                            full = httpx.get(mailbox + "/api/v1/message/" + message["ID"]).json()
                            assert "Nếu bạn không đăng ký" in full["Text"] and "<strong>" in full["HTML"]
                            return re.search(r": (\d{6})$", message["Subject"]).group(1)
                    time.sleep(.1)
                raise AssertionError("Mailpit did not receive the requested test email")
            def start_mailer():
                return subprocess.Popen([sys.executable, "-m", "arena_api.mailer"], cwd=ROOT, env=env,
                                        stdout=subprocess.DEVNULL, stderr=open(output / "mailer-process.log", "a"))
            first = enqueue("22000001@vnu.test")
            assert httpx.get(mailbox + "/api/v1/messages").json()["total"] == 0
            passed("http_request_queues_without_sending_smtp")
            sentinel = app.state.store.create("job", "test", {"status": "playing", "report": {"guard": "unchanged"}})
            mailer = start_mailer()
            code = code_for("22000001@vnu.test")
            result = client.post("/api/v1/auth/email/verify", json={"request_id": first, "code": code})
            assert result.status_code == 200 and result.json()["verified_ms"], result.text
            assert "verification_token" not in client.get("/api/v1/auth/email/requests/" + first).text
            passed("separate_process_delivers_multipart_mailpit_code_and_http_verifies_it")
            mailer.terminate()
            mailer.wait(10)
            second = enqueue("22000002@vnu.test")
            mailer = start_mailer()
            code = code_for("22000002@vnu.test")
            assert client.post("/api/v1/auth/email/verify", json={"request_id": second, "code": code}).status_code == 200
            assert app.state.store.get(sentinel["id"])["status"] == "playing"
            passed("mailer_restart_preserves_queue_and_never_recovers_hub_jobs")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for index, scheme in enumerate(("light", "dark"), 3):
                    context = browser.new_context(color_scheme=scheme, reduced_motion="reduce", viewport={"width": 1440, "height": 1000})
                    page = context.new_page()
                    page.on("pageerror", lambda exc: report["page_errors"].append(str(exc)))
                    page.goto(origin + "/#/verify-email")
                    expect(page.locator("#main")).to_contain_text("Không thấy thư? Xem mục Spam")
                    address = f"2200000{index}@vnu.test"
                    page.locator("[name=email]").fill(address)
                    page.locator("#email-request-form button[type=submit]").click()
                    expect(page.locator("#email-code-form")).to_be_visible()
                    code = code_for(address)
                    page.locator("[name=email-code]").fill(code)
                    page.locator("#email-code-form button[type=submit]").click()
                    expect(page.locator("#email-state")).to_contain_text("Email đã được xác minh")
                    background = page.locator("body").evaluate("e=>getComputedStyle(e).backgroundColor")
                    assert background == ("rgb(20, 32, 25)" if scheme == "dark" else "rgb(240, 244, 241)"), background
                    auth_contrast = text_contrast(page)
                    assert min(item["ratio"] for item in auth_contrast) >= 4.5, auth_contrast
                    page.screenshot(path=str(output / f"{scheme}-verified.png"), full_page=True)
                    page.goto(origin + "/#/login")
                    page.locator("[name=username]").fill("organizer")
                    page.locator("[name=login-password]").fill(PASSWORD)
                    page.locator("#login-form button[type=submit]").click()
                    expect(page.locator(".sidebar")).to_be_visible()
                    page.get_by_role("link", name="Gửi email", exact=True).click()
                    expect(page.locator("#main h1")).to_have_text("Gửi email")
                    expect(page.locator("#main")).to_contain_text("mailpit")
                    background = page.locator("body").evaluate("e=>getComputedStyle(e).backgroundColor")
                    assert background == ("rgb(20, 32, 25)" if scheme == "dark" else "rgb(240, 244, 241)"), background
                    contrast = text_contrast(page)
                    assert min(item["ratio"] for item in contrast) >= 4.5, contrast
                    page.screenshot(path=str(output / f"{scheme}-admin.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "Mobile body overflow"
                    page.screenshot(path=str(output / f"{scheme}-mobile.png"), full_page=True)
                    assert key not in page.content()
                    report["browser"].append({"scheme": scheme, "contrast_min": min(item["ratio"] for item in contrast), "auth_contrast_min": min(item["ratio"] for item in auth_contrast)})
                    context.close()
                browser.close()
            assert not report["page_errors"], report["page_errors"]
            passed("browser_receives_verifies_and_admin_reads_quotas_light_dark_mobile")
            dropped = enqueue("22000005@vnu.test")
            with app.state.store.lock, app.state.store.db:
                app.state.store.db.execute("UPDATE email_outbox SET created_ms=0 WHERE id=?", (dropped,))
            assert client.get("/api/v1/auth/email/requests/" + dropped).json()["status"] == "dropped"
            passed("overdue_request_reports_dropped_without_email_delivery")
            print("ALL EMAIL INTEGRATION CHECKS PASSED", flush=True)
        finally:
            if mailer and mailer.poll() is None:
                mailer.terminate()
                mailer.wait(15)
            server.should_exit = True
            if thread.is_alive():
                thread.join(15)
            mailpit.terminate()
            mailpit.wait(10)
            client.close()
            (output / "integration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
