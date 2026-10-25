"""Explicit M2-T acceptance against the public hub; never starts a local server.

Credentials stay in memory or the owner's private directory. No mail, deployment,
App configuration changes, or platform push. Part C makes ONE authorized bot-thu
comment commit; its private checkpoint prevents a second push on resumption.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
import uuid
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def private_dir():
    """Owner's credential directory: ARENA_PRIVATE_DIR, default ~/.uet-arena."""
    return Path(os.environ.get('ARENA_PRIVATE_DIR') or Path.home() / '.uet-arena')


PRIVATE = private_dir()
OUTPUT = ROOT / 'test-results/m2/live-e2e'
REPO = 'uet-bot-arena/bot-thu'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def api(client, method, path, expected=200, **kwargs):
    headers = dict(kwargs.pop('headers', {}))
    if method == 'POST':
        headers.setdefault('Idempotency-Key', uuid.uuid4().hex)
        if 'json' not in kwargs and 'content' not in kwargs:
            kwargs['json'] = {}
    response = client.request(method, '/api/v1' + path, headers=headers, **kwargs)
    # Do not include bodies/headers: auth responses can contain credentials.
    require(response.status_code == expected,
            f'{method} {path.split("?")[0]}: HTTP {response.status_code}, expected {expected}')
    return response.json()


def login(hub, username, password):
    client = httpx.Client(base_url=hub, headers={'User-Agent': 'uet-arena-e2e/1',
                                               'Origin': hub}, timeout=60)
    try:
        session = api(client, 'POST', '/auth/login', json={'username': username, 'password': password})
        client.headers['X-CSRF-Token'] = session['csrf_token']
        return client
    except Exception:
        client.close()
        raise


def organizer(hub):
    credentials = dict(line.split('=', 1) for line in
                       (PRIVATE / 'btc-organizer.txt').read_text().splitlines() if '=' in line)
    return login(hub, credentials['username'], credentials['password'])


def page_ready(page):
    page.wait_for_function("""() => {
      const m=document.querySelector('#main');
      return m && !m.textContent.includes('Đang tải dữ liệu…') &&
             !m.textContent.includes('Loading data…');
    }""")
    require(page.locator('#main .form-error.visible').count() == 0, 'UI shows a loading/form error')


def check_webhook_status(status):
    require(200 <= status < 300, 'C: webhook returned HTTP ' + str(status))


def part_a(hub, result):
    from playwright.sync_api import sync_playwright, expect
    student = tomllib.loads((PRIVATE / 'test-accounts.toml').read_text())['student']
    username = student.get('username') or student['email']
    errors, bad_http = [], []
    result.update(combinations=[], js_errors=errors, bad_http=bad_http)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for width in (1440, 390):
                for scheme in ('light', 'dark'):
                    for language in ('vi', 'en'):
                        label = f'A-{width}-{scheme}-{language}'
                        context = browser.new_context(viewport={'width': width, 'height': 1000},
                                                      color_scheme=scheme, reduced_motion='reduce')
                        context.add_init_script('localStorage.setItem("uet-arena-language",' +
                                                json.dumps(language) + ');')
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(type(error).__name__))
                        authenticated = [False]

                        def response_received(response):
                            path = urlsplit(response.url).path
                            if response.status >= 400 and not (
                                response.status == 401 and path == '/api/v1/auth/me'
                                and not authenticated[0]
                            ):
                                bad_http.append({'status': response.status, 'path': path})
                        page.on('response', response_received)
                        try:
                            page.goto(hub + '/#/login')
                            page.locator('[name=username]').fill(username)
                            page.locator('[name=login-password]').fill(student['password'])
                            page.locator('#login-form button[type=submit]').click()
                            expect(page.locator('[data-action=logout]')).to_have_count(1)
                            authenticated[0] = True
                            for route in ('home', 'team', 'matching', 'guide', 'standings', 'live'):
                                page.goto(hub + '/#/' + route)
                                page_ready(page)
                                require(page.url.endswith('/#/' + route), f'{label}: redirected from {route}')
                                require(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),
                                        f'{label}: horizontal overflow on {route}')
                                expected_icon = '.theme-moon' if scheme == 'light' else '.theme-sun'
                                expect(page.locator('[data-action=theme] ' + expected_icon)).to_be_visible()
                                require(page.locator('html').get_attribute('lang') == language,
                                        f'{label}: wrong language')
                                page.screenshot(path=str(OUTPUT / 'shots' / f'{label}-{route}.png'), full_page=True)
                            # Sidebar is inert on mobile until opened.
                            if width == 390:
                                page.locator('[data-action=menu].mobile-menu').click()
                            page.locator('[data-action=logout]').click()
                            page.wait_for_url('**/#/login')
                            authenticated[0] = False
                            for route in ('home', 'schedule', 'standings'):
                                page.goto(hub + '/#/' + route)
                                page_ready(page)
                                require(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),
                                        f'{label}: guest horizontal overflow on {route}')
                                page.screenshot(path=str(OUTPUT / 'shots' / f'{label}-guest-{route}.png'), full_page=True)
                            require(not errors, f'{label}: JavaScript errors')
                            require(not bad_http, f'{label}: unexpected HTTP responses')
                            result['combinations'].append(label)
                            print('PASS', label, flush=True)
                        except Exception:
                            # Never screenshot a filled login/password form.
                            if page.locator('input[type=password]').count() == 0:
                                page.screenshot(path=str(OUTPUT / 'shots' / f'{label}-failure.png'), full_page=True)
                            raise
                        finally:
                            context.close()
        finally:
            browser.close()


def part_b(hub, result):
    # M2-T conflicts with the current GitHub-only / non-guest official rules.
    # No synthetic GitHub observations, direct production SQL, or fake student
    # identities may be used to bypass them. Await the owner's policy decision.
    result['blockers'] = [
        'RoundBook.latest_passes only selects github_checks/github_observations',
        'Competitions.enter rejects manual entry when submission_deadline is set',
        'EloScheduler.entries excludes guest teams; THE-LE excludes guest finalists',
    ]
    raise AssertionError('B blocked: current rules do not support the guest ZIP demo in PLAN_M2')


def part_c(hub, result):
    from arena.conf import Secrets
    from arena_api.github_client import GitHubClient
    from playwright.sync_api import sync_playwright, expect
    from grader_cluster import windows_path

    saved = json.loads((PRIVATE / 'smoke-teams.json').read_text())
    with closing(organizer(hub)) as admin, closing(login(hub, 'thu-m2-a', saved['thu-m2-a'])) as team:
        team_id = api(team, 'GET', '/auth/me')['user']['team_id']
        view = api(admin, 'GET', f'/admin/teams/{team_id}/github')
        # The attach operation is explicitly authorized by M2-T, with audit.
        if not view.get('repo'):
            api(admin, 'POST', f'/admin/teams/{team_id}/github', 201,
                json={'url': 'https://github.com/' + REPO})
        else:
            require(view['repo']['owner'] + '/' + view['repo']['name'] == REPO,
                    'C: team is already attached to another repository')
        def github_http(method, url, headers, body, limit):
            return GitHubClient.http(method, url, {**headers, 'User-Agent': 'uet-arena-e2e/1'}, body, limit)
        github = GitHubClient(Secrets.load(PRIVATE / 'secrets.toml', required=True), transport=github_http)
        checkpoint = PRIVATE / 'm2-v-github-checkpoint.json'
        if checkpoint.exists():
            pushed = json.loads(checkpoint.read_text())
            require(pushed['hub'] == hub, 'C checkpoint belongs to another hub')
        else:
            def command(argv):
                proc = subprocess.run(argv, cwd='/mnt/c', capture_output=True, text=True)
                require(proc.returncode == 0, f'{argv[0]} {argv[1]} failed (output withheld)')
                return proc.stdout.strip()
            with tempfile.TemporaryDirectory(prefix='m2-t-bot-', dir=PRIVATE) as tmp:
                repo = Path(tmp) / 'bot-thu'
                command(['gh.exe', 'repo', 'clone', REPO, windows_path(repo), '--', '--depth=1'])
                def git(*args):
                    return command(['git.exe', '-C', windows_path(repo), *args])
                require(git('branch', '--show-current') == 'main', 'C requires main')
                require(not git('status', '--porcelain'), 'C clone is not clean')
                bot = repo / 'bot/bot.cpp'
                require(bot.is_file(), 'C expects existing bot/bot.cpp')
                with bot.open('a', encoding='utf-8', newline='\n') as handle:
                    handle.write('// M2-T live webhook acceptance ' + str(time.time_ns()) + '\n')
                git('add', 'bot/bot.cpp')
                require(git('diff', '--cached', '--numstat') == '1\t0\tbot/bot.cpp',
                        'C must add exactly one comment line')
                git('commit', '-m', 'Kiểm tra webhook trên web thật')
                require('co-authored-by:' not in git('log', '-1', '--format=%B').lower(),
                        'C: unexpected co-author trailer; commit was not pushed')
                sha = git('rev-parse', 'HEAD')
                pushed = {'hub': hub, 'sha': sha, 'started_ms': time.time_ns() // 1_000_000,
                          'push_attempted': True}
                # Persist BEFORE push: uncertain network outcomes must not cause another commit.
                checkpoint.write_text(json.dumps(pushed), encoding='utf-8')
                checkpoint.chmod(0o600)
                git('push', 'origin', 'main')
        result['sha'] = pushed['sha']
        until = time.monotonic() + 60
        delivery = None
        while time.monotonic() < until:
            deliveries = github.request('GET', '/app/hook/deliveries?per_page=30', github.jwt())[2]
            for candidate in deliveries:
                if candidate.get('event') != 'push':
                    continue
                detail = github.request('GET', '/app/hook/deliveries/' + str(candidate['id']), github.jwt())[2]
                payload = detail.get('request', {}).get('payload') or {}
                if payload.get('after') == pushed['sha']:
                    delivery = detail
                    break
            if delivery:
                break
            time.sleep(2)
        require(delivery is not None, 'C: no matching GitHub delivery within 60 seconds')
        delivered_ms = int(datetime.fromisoformat(delivery['delivered_at'].replace('Z', '+00:00')).timestamp() * 1000)
        result['delivery'] = {'status_code': delivery['status_code'],
                              'seconds': (delivered_ms - pushed['started_ms']) / 1000}
        require(result['delivery']['seconds'] <= 60, 'C: webhook exceeded 60 seconds')
        check_webhook_status(delivery['status_code'])
        until = time.monotonic() + 900
        commit = None
        while time.monotonic() < until:
            view = api(team, 'GET', '/teams/me/github')
            commit = next((c for c in view['commits'] if c['sha'] == pushed['sha']), None)
            if commit and commit['state'] in ('pass', 'fail'):
                break
            time.sleep(2)
        require(commit and commit['state'] == 'pass', 'C: pushed SHA did not pass checks')
        result['commit_state'] = commit['state']
        require(commit['checks'], 'C: no check jobs for this SHA')
        result['checks'] = []
        graders = api(admin, 'GET', '/admin/graders')['items']
        windows_workers = {g['name'] for g in graders if g['platform'] == 'windows'}
        for check in commit['checks']:
            job = api(admin, 'GET', '/admin/jobs/' + check['job_id'])
            report = job.get('report') or job.get('qualification') or job.get('check_summary') or {}
            samples = report.get('samples', report.get('sample_count', 0))
            samples = len(samples) if isinstance(samples, list) else samples
            require(job['status'] == 'completed' and samples == 72 and job.get('worker') in windows_workers,
                    'C: expected a completed 72-sample check on a real Windows grader')
            result['checks'].append({'job_id': job['id'], 'samples': samples, 'worker': job['worker']})
        installation = github.installation(*REPO.split('/'))
        status = github.get(*REPO.split('/'), installation, '/commits/' + pushed['sha'] + '/status')[2]
        require(any(s['context'] == 'uet-bot-arena/check' and s['state'] == 'success'
                    for s in status['statuses']), 'C: GitHub success status missing')
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(hub + '/#/login')
            page.locator('[name=username]').fill('thu-m2-a')
            page.locator('[name=login-password]').fill(saved['thu-m2-a'])
            page.locator('#login-form button[type=submit]').click()
            expect(page.locator('[data-action=logout]')).to_have_count(1)
            page.goto(hub + '/#/submissions')
            expect(page.locator('#github-page')).to_contain_text(pushed['sha'][:7])
            page.screenshot(path=str(OUTPUT / 'shots/C-github.png'), full_page=True)
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', required=True)
    parser.add_argument('--part', choices=['A', 'B', 'C'])
    args = parser.parse_args()
    require(args.hub == 'https://uetbotarena.id.vn', 'Only the authorized public hub is supported')
    (OUTPUT / 'shots').mkdir(parents=True, exist_ok=True)
    if not args.part:
        ledger = OUTPUT / 'full-runs.json'
        runs = json.loads(ledger.read_text()) if ledger.exists() else []
        require(len(runs) < 2, 'M2-T permits at most two full runs')
        runs.append(datetime.now(timezone.utc).isoformat())
        ledger.write_text(json.dumps(runs) + '\n', encoding='utf-8')
    result = {'started': datetime.now(timezone.utc).isoformat(), 'hub': args.hub, 'parts': {}}
    path = OUTPUT / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    for name in ([args.part] if args.part else ['A', 'B', 'C']):
        row = result['parts'][name] = {'status': 'RUNNING'}
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        try:
            globals()['part_' + name.lower()](args.hub, row)
            row['status'] = 'PASS'
        except AssertionError as error:
            row.update(status='FAIL', error=str(error))
            print('FAIL', name, str(error), flush=True)
        except Exception as error:
            # Third-party exception strings can contain sensitive request details.
            row.update(status='ERROR', error=type(error).__name__,
                       frames=[{'file': Path(f.filename).name, 'line': f.lineno}
                               for f in traceback.extract_tb(error.__traceback__)])
            print('ERROR', name, type(error).__name__, flush=True)
        finally:
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('RESULT', path, flush=True)
    return int(any(row['status'] != 'PASS' for row in result['parts'].values()))


if __name__ == '__main__':
    sys.exit(main())
