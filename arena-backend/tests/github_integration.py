"""Explicit real-GitHub acceptance: only uet-bot-arena/bot-thu, native Windows grader.

Writes test commits and final App statuses to the already-authorized private repo.
Changes only the App webhook URL via the M1 Quick Tunnel, then restores its old URL.
No email, repository setting changes, or platform pushes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena.conf import Secrets
from arena_api.github_client import GitHubClient
from arena_api.github_tunnel import tunnel
from grader_cluster import Cluster, PASSWORD, windows_path

OWNER, REPO = "uet-bot-arena", "bot-thu"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets-file", required=True)
    args = parser.parse_args()
    credentials = Secrets.load(args.secrets_file, required=True)
    github = GitHubClient(credentials)
    old_url = github.request("GET", "/app/hook/config", github.jwt())[2]["url"]
    cluster = Cluster()
    cluster.results["mode"] = "github"
    output = ROOT / "test-results/m1/g7"
    output.mkdir(parents=True, exist_ok=True)
    result = {"backend": "windows", "repo": OWNER + "/" + REPO, "poll_seconds": 30, "checks": []}
    repo = cluster.shared / "bot-thu"

    def command(argv):
        return subprocess.run(argv, cwd="/mnt/c", capture_output=True, text=True, check=True).stdout.strip()

    def git(*args):
        return command(["git.exe", "-C", windows_path(repo), *args])

    def push(label):
        # Comments change bot/ without changing the sample bot's legal behavior.
        with (repo / "bot/bot.cpp").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n// M1 GitHub acceptance " + label + " " + str(time.time_ns()) + "\n")
        git("add", "bot/bot.cpp", "bot/json.hpp")
        git("commit", "-m", "M1 GitHub acceptance: " + label)
        before = time.monotonic()
        git("push", "origin", "main")
        return git("rev-parse", "HEAD"), before

    def check(name, **data):
        result["checks"].append({"name": name, "passed": True, **data})
        print("PASS", name, json.dumps(data, ensure_ascii=False), flush=True)

    try:
        command(["gh.exe", "repo", "clone", OWNER + "/" + REPO, windows_path(repo), "--", "--depth=1"])
        assert git("branch", "--show-current") == "main"
        assert git("remote", "get-url", "origin") == "https://github.com/" + OWNER + "/" + REPO + ".git"
        (repo / "bot").mkdir(exist_ok=True)
        for name in ("bot.cpp", "json.hpp"):
            shutil.copyfile(ROOT / "sdk" / name, repo / "bot" / name)
        first, _ = push("initial native sample")
        config = cluster.data / "github.toml"
        config.write_text('[github]\npoll_seconds=30\n', encoding="utf-8")
        cluster.env.update(ARENA_CONFIG=str(config), ARENA_SECRETS=str(Path(args.secrets_file).resolve()))
        with tunnel(cluster.port, credentials) as url:
            cluster.env["ARENA_GITHUB_WEBHOOK_HOSTS"] = urlsplit(url).netloc
            cluster.start_hub()
            cluster.grader("github-windows", "windows", 0, slots=1)
            cluster.wait_online(["github-windows"])
            profile = {"team": "GitHub acceptance team", "email": "github-acceptance@example.test", "phone": "", "members": [
                {"name": f"Synthetic student {i}", "student_id": f"GITHUB{i}00"} for i in range(4)], "confirmed": True}
            issued = cluster.api(cluster.admin, "POST", "/admin/teams", expected=201,
                                 json={"profile": profile, "username": "github-test", "temporary_password": PASSWORD})
            team_id = issued["account"]["team_id"]
            import httpx
            team = httpx.Client(base_url=cluster.origin, timeout=60)
            login = cluster.api(team, "POST", "/auth/login", json={"username": "github-test", "password": PASSWORD})
            team.headers["X-CSRF-Token"] = login["csrf_token"]
            changed = cluster.api(team, "POST", "/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD + "-changed"})
            team.headers["X-CSRF-Token"] = changed["csrf_token"]
            cluster.api(cluster.admin, "POST", f"/admin/teams/{team_id}/github", expected=201,
                        json={"url": "https://github.com/" + OWNER + "/" + REPO})

            def state():
                return cluster.api(team, "GET", "/teams/me/github")

            def wait_commit(sha, timeout=180, passed=True):
                until = time.monotonic() + timeout
                while time.monotonic() < until:
                    row = next((r for r in state()["commits"] if r["sha"] == sha), None)
                    if row and (not passed or row["state"] in ("pass", "fail")):
                        if passed:
                            assert row["state"] == "pass", {"sha": sha, "error": row["error"], "checks": row["checks"]}
                        return row
                    time.sleep(0.2)
                raise AssertionError("Commit chưa được nhận/kiểm: " + sha)

            wait_commit(first)
            check("initial_pinned_source_passes_72_native_samples", sha=first)
            # Start immediately after a successful poll; the next scheduled poll is ~30s away.
            second, before = push("webhook")
            wait_commit(second, timeout=10, passed=False)
            elapsed = time.monotonic() - before
            assert elapsed <= 10, elapsed
            check("webhook_detects_push_within_10_seconds", sha=second, seconds=round(elapsed, 3))
            wait_commit(second)
            installation = github.installation(OWNER, REPO)
            until = time.monotonic() + 30
            while True:
                status = github.get(OWNER, REPO, installation, "/commits/" + second + "/status")[2]
                final = next((s for s in status["statuses"] if s["context"] == "uet-bot-arena/check"), None)
                if final or time.monotonic() >= until:
                    break
                time.sleep(1)
            assert final and final["state"] == "success"
            check("github_final_commit_status_success", sha=second)
            # This exact path is rejected by RequestBoundary; automatic poll must recover.
            github.request("PATCH", "/app/hook/config", github.jwt(), {"url": url + "/webhook-disabled-for-poll-test"})
            third, before = push("poll without webhook")
            wait_commit(third, timeout=35, passed=False)
            elapsed = time.monotonic() - before
            assert elapsed <= result["poll_seconds"], elapsed
            check("poll_recovers_without_webhook", sha=third, seconds=round(elapsed, 3))
            wait_commit(third)
            github.request("PATCH", "/app/hook/config", github.jwt(), {"url": url + "/api/v1/github/webhook"})
            deadline = time.time_ns() // 1_000_000 + 25_000
            competition = cluster.api(cluster.admin, "POST", "/admin/competitions", expected=201,
                                      json={"title": "Real GitHub deadline acceptance", "max_teams": 16, "match_ms": 60000,
                                            "grader_platform": "windows", "submission_deadline": deadline})
            ident = competition["id"]
            cluster.api(cluster.admin, "POST", f"/admin/competitions/{ident}/open")
            until = time.monotonic() + 120
            while time.monotonic() < until:
                view = state()
                choice = next((d for d in view["deadlines"] if d["competition_id"] == ident), None)
                if choice and choice["state"] in ("locked", "no_entry", "needs_approval"):
                    break
                time.sleep(0.5)
            assert choice and choice["state"] == "locked", choice
            assert choice["selected_sha"] == third and choice["source"] == "github_activity", choice
            assert choice["pushed_ms"] <= deadline
            check("deadline_pins_github_push_history", sha=third, pushed_ms=choice["pushed_ms"], deadline_ms=deadline)
            fourth, _ = push("after deadline")
            wait_commit(fourth, passed=False)
            after = next(d for d in state()["deadlines"] if d["competition_id"] == ident)
            assert after["selected_sha"] == third
            check("push_after_deadline_does_not_change_entry", later_sha=fourth, pinned_sha=third)
            result["ui_origin"] = cluster.origin
            # Browser verification is in the same process, with this real API state.
            from playwright.sync_api import sync_playwright, expect
            from grader_browser import text_contrast
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for scheme in ("light", "dark"):
                    context = browser.new_context(color_scheme=scheme, reduced_motion="reduce", viewport={"width":1440,"height":1000})
                    page = context.new_page()
                    page_errors = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.goto(cluster.origin + "/#/login")
                    page.locator('[name=username]').fill("github-test")
                    page.locator('[name=login-password]').fill(PASSWORD + "-changed")
                    page.locator('#login-form button[type=submit]').click()
                    expect(page.locator('.sidebar')).to_be_visible()
                    page.goto(cluster.origin + "/#/submissions")
                    expect(page.locator('#github-page')).to_be_visible()
                    expect(page.locator('#github-page')).to_contain_text(third[:7])
                    expect(page.locator('input[type=file]')).to_have_count(0)
                    ratios = text_contrast(page)
                    minimum = min(r["ratio"] for r in ratios)
                    assert minimum >= 4.5, sorted(ratios, key=lambda r: r["ratio"])[:5]
                    page.screenshot(path=str(output / f"{scheme}-github-desktop.png"), full_page=True)
                    page.set_viewport_size({"width":390,"height":844})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "mobile overflow"
                    page.screenshot(path=str(output / f"{scheme}-github-mobile.png"), full_page=True)
                    assert not page_errors, page_errors
                    check("github_ui_" + scheme, min_contrast=minimum, widths=[1440,390])
                    context.close()
                browser.close()
            team.close()
    finally:
        # Restore only the old URL, never secret/content_type/SSL or App permissions.
        try:
            github.request("PATCH", "/app/hook/config", github.jwt(), {"url": old_url})
        finally:
            cluster.stop()
            (output / "github-real.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
