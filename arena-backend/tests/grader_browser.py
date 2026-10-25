"""Real admin UI: grader creation/revocation, light/dark and mobile screenshots, text contrast.

The hub runs without any sandbox backend or grader process. All accounts/tokens are disposable.
"""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arena.conf import Secrets
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings

PASSWORD = "grader-browser-test-password"


def text_contrast(page):
    return page.locator("body").evaluate("""root=>{
                      const rgb=s=>(s.match(/[\\d.]+/g)||[]).map(Number);
                      const luminance=c=>c.slice(0,3).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((s,x,i)=>s+x*[.2126,.7152,.0722][i],0);
                      const out=[];
                      for(const e of root.querySelectorAll('*')){
                        if(!e.getClientRects().length||!Array.from(e.childNodes).some(n=>n.nodeType===3&&n.textContent.trim()))continue;
                        const style=getComputedStyle(e),fg=rgb(e.localName==='text'?style.fill:style.color); let parent=e,bg;
                        while(parent){bg=rgb(getComputedStyle(parent).backgroundColor);if(bg.length===3||bg[3]>=.99)break;parent=parent.parentElement;}
                        if(!parent)continue;
                        const a=luminance(fg),b=luminance(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                        out.push({text:e.textContent.trim().slice(0,70),ratio:Math.round(ratio*100)/100});
                      }
                      return out;
                    }""")


def main():
    output = ROOT / "test-results/m1/graders-ui"
    output.mkdir(parents=True, exist_ok=True)
    results = {"checks": [], "console_errors": []}
    with tempfile.TemporaryDirectory(prefix="graders-browser-") as tmp:
        store = Store(tmp, recover_jobs=False)
        Accounts(store).bootstrap_admin("organizer", PASSWORD)
        store.db.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        app = create_app(Settings(data=Path(tmp), origin=origin, secrets=Secrets({})))
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            until = time.monotonic() + 20
            while not server.started and time.monotonic() < until:
                time.sleep(0.05)
            assert server.started
            service = app.state.service
            for name, platform, backend in (("PC phòng 301", "windows", "windows"), ("WSL phòng 302", "linux", "bwrap")):
                item = service.create_grader(name, "browser-test")
                service.hello(service.authenticate(item["token"]), {"platform": platform, "backend": backend,
                              "runtime_id": "sha256:" + "1" * 64, "slots": 2, "version": "0.7.0-m1"})
                if platform == "linux":
                    with app.state.store.lock, app.state.store.db:
                        app.state.store.db.execute("UPDATE graders SET last_seen_ms=0 WHERE id=?", (item["id"],))
            item = service.create_grader("Máy đã thu hồi", "browser-test")
            service.revoke_grader(item["id"], "browser-test")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for scheme in ("light", "dark"):
                    context = browser.new_context(color_scheme=scheme, reduced_motion="reduce",
                                                  viewport={"width": 1600, "height": 1050})
                    page = context.new_page()
                    page.on("pageerror", lambda error: results["console_errors"].append(str(error)))
                    page.goto(origin + "/#/login")
                    page.locator("[name=username]").fill("organizer")
                    page.locator("[name=login-password]").fill(PASSWORD)
                    page.locator("#login-form button[type=submit]").click()
                    expect(page.locator(".sidebar")).to_be_visible()
                    page.goto(origin + "/#/graders")
                    expect(page.locator("#main h1")).to_have_text("Máy chấm")
                    expect(page.locator("#main")).to_contain_text("Windows · AppContainer")
                    expect(page.locator("#main")).to_contain_text("Linux · bubblewrap")
                    expect(page.locator("#main")).to_contain_text("Đã thu hồi")
                    expect(page.locator("#main")).to_contain_text("Offline")
                    page.screenshot(path=str(output / f"{scheme}-desktop.png"), full_page=True)
                    background = page.locator("body").evaluate("e=>getComputedStyle(e).backgroundColor")
                    assert background == ("rgb(17, 20, 24)" if scheme == "dark" else "rgb(240, 244, 241)"), background
                    # Compute rendered text contrast against the nearest opaque ancestor.
                    contrast = text_contrast(page)
                    failures = [item for item in contrast if item["ratio"] < 4.5]
                    assert not failures, (scheme, failures)
                    results["checks"].append({"scheme": scheme, "contrast_min": min(x["ratio"] for x in contrast), "text_elements": len(contrast)})
                    form = page.locator("#grader-form")
                    form.locator("[name=name]").fill("Máy thử " + scheme)
                    form.locator("button[type=submit]").click()
                    expect(page.locator("#grader-token")).to_be_visible()
                    expect(page.locator("#grader-token")).to_contain_text("uag_")
                    page.get_by_role("button", name="Đóng", exact=True).last.click()
                    page.get_by_role("button", name="Cập nhật", exact=True).click()
                    row = page.locator("tr").filter(has_text="Máy thử " + scheme)
                    expect(row).to_contain_text("Chưa kết nối")
                    page.once("dialog", lambda dialog: dialog.accept())
                    row.get_by_role("button", name="Thu hồi", exact=True).click()
                    expect(row).to_contain_text("Đã thu hồi")
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right <= 0")
                    page.screenshot(path=str(output / f"{scheme}-mobile.png"), full_page=True)
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "page overflow"
                    context.close()
                assert not results["console_errors"], results["console_errors"]
                browser.close()
            print("ALL GRADER BROWSER CHECKS PASSED", json.dumps(results, ensure_ascii=False), flush=True)
        finally:
            server.should_exit = True
            thread.join(15)
            (output / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
