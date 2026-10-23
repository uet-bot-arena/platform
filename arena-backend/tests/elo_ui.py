"""Light/dark desktop/mobile acceptance of the single starter and 10-second bank."""
import io
import uuid
from types import SimpleNamespace
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import zipfile

import uvicorn
from playwright.sync_api import expect,sync_playwright
from grader_browser import ROOT,PASSWORD,text_contrast
from arena import engine
from arena_api.elo_rounds import RoundBook
from arena_api.publisher import Publisher
import test_competitions as helpers
from arena.conf import Secrets
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings


def main(output=None,extended=False):
    output=output or ROOT/'test-results/m1/g8/ui';output.mkdir(parents=True,exist_ok=True)
    result=dict(checks=[],page_errors=[],contrast_failures=[])
    with tempfile.TemporaryDirectory(prefix='elo-ui-') as tmp:
        store=Store(tmp);Accounts(store).bootstrap_admin('organizer',PASSWORD);store.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        origin='http://127.0.0.1:'+str(sock.getsockname()[1])
        app=create_app(Settings(data=Path(tmp),origin=origin,secrets=Secrets({})),start_worker=False)
        server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        try:
            until=time.monotonic()+30
            while not server.started and time.monotonic()<until:time.sleep(.05)
            assert server.started
            issued=app.state.operations.create_team(dict(team=('UI <img src=x onerror=alert(1)>' if extended else 'UI fixture'),email='ui@example.test',phone='',confirmed=True,
                members=[dict(name='Fixture '+str(i),student_id='UI00000'+str(i)) for i in range(4)]),'ui-fixture',PASSWORD,'test')
            with app.state.store.lock,app.state.store.db:
                app.state.store.db.execute('UPDATE accounts SET must_change=0 WHERE id=?',(issued['account']['id'],))
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for name in ('bot.cpp','json.hpp'):z.write(ROOT/'sdk'/name,name)
            app.state.service.submit(issued['account']['team_id'],'v1.0.0','cpp17',raw.getvalue(),'ui-test-starter')
            # Synthetic standings only: hardware execution is measured by elo_native.py.
            comp=app.state.competitions
            item=comp.create('test',dict(title='Vòng tròn UI',max_teams=20,format='elo',submission_deadline=9_000_000_000_000,
                match_ms=60000,runtime_id='sha256:'+'1'*64),uuid.uuid4().hex)
            entries={}
            for i in range(4):
                account=issued if i==0 else app.state.operations.create_team(dict(team='UI đối thủ '+str(i),email=f'ui{i}@example.test',phone='',confirmed=True,
                    members=[dict(name=f'Fixture {i}-{j}',student_id=f'UI{i}0000{j}') for j in range(4)]),'ui-opponent-'+str(i),PASSWORD,'test')
                team=account['account']['team_id']
                body=helpers.CompetitionAPI.qualified(SimpleNamespace(store=app.state.store),team,item['config'])
                if extended:
                    from arena.config import benchmark_profile
                    saved=app.state.store.get(body['benchmark_id']);report=saved['report']
                    report.update(profile=benchmark_profile().public(),clock_scope='per_sample_500_ms')
                    app.state.store.update(saved['id'],profile=report['profile'],report=report)
                    with app.state.store.transaction():app.state.store.db.execute('UPDATE accounts SET must_change=0 WHERE team_id=?',(team,))
                entry=comp.qualification(item['config'],team,body['submission_id'],body['benchmark_id'])
                entries[team]=dict(entry,github_sha=f'{i+1:040x}',bot_hash=str(i))
            with app.state.store.transaction():
                app.state.store.db.execute("UPDATE competitions SET status='running' WHERE id=?",(item['id'],))
                for team,entry in entries.items():
                    app.state.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(item['id'],team,entry['submission_id'],entry['benchmark_id'],json.dumps(entry),1))
            book=RoundBook(comp);book.seed_official(item['id'],entries,0)
            rid=book.start(item['id'],'round_robin',1,time.time_ns()//1_000_000,entries)
            with app.state.store.transaction():
                for game in book.games(rid)[:4]:
                    app.state.store.db.execute("UPDATE elo_games SET status='completed',result=? WHERE id=?",(json.dumps(dict(winner='red',reason='base_capture')),game['id']))
            ladder=comp.create('test',dict(title='Ladder UI',max_teams=20,format='elo',submission_deadline=9_000_000_000_000,
                match_ms=60000,runtime_id='sha256:'+'1'*64),uuid.uuid4().hex)
            with app.state.store.transaction():app.state.store.db.execute("UPDATE competitions SET status='open' WHERE id=?",(ladder['id'],))
            for number in (1,2):
                previous=book.start(ladder['id'],'ladder',number,time.time_ns()//1_000_000-10000+number,entries)
                with app.state.store.transaction():
                    for game in book.games(previous):
                        app.state.store.db.execute("UPDATE elo_games SET status='completed',result=? WHERE id=?",(json.dumps(dict(winner='red',reason='base_capture')),game['id']))
                book.finish(previous,time.time_ns()//1_000_000-5000+number)
            book.start(ladder['id'],'ladder',3,time.time_ns()//1_000_000,entries)
            team=issued['account']['team_id'];entry=entries[team]
            with app.state.store.transaction():
                app.state.store.db.execute("INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms) VALUES(?,1,'fixture','fixture',1,1)",(team,))
                for i,state in ((1,'pass'),(2,'checking')):
                    sha=f'{i:040x}'
                    app.state.store.db.execute('INSERT INTO github_commits(team_id,sha,received_ms,submission_id,bot_hash,state) VALUES(?,?,?,?,?,?)',(team,sha,i,entry['submission_id'],entry['bot_hash'],state))
                    app.state.store.db.execute('INSERT INTO github_observations(team_id,sha,observed_ms) VALUES(?,?,?)',(team,sha,i))
                    app.state.store.db.execute('INSERT INTO github_checks(team_id,sha,purpose,job_id,state,finished_ms) VALUES(?,?,?,?,?,?)',(team,sha,ladder['id'],entry['benchmark_id'],state,1 if state=='pass' else None))
            legacy=None
            if extended:
                legacy=comp.create('test',dict(title='Biểu đồ điểm UI',max_teams=16,runtime_id='sha256:'+'1'*64,match_ms=60000),uuid.uuid4().hex)
                with app.state.store.transaction():
                    app.state.store.db.execute("UPDATE competitions SET status='completed' WHERE id=?",(legacy['id'],))
                    for t,e in entries.items():app.state.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(legacy['id'],t,e['submission_id'],e['benchmark_id'],json.dumps(e),1))
                    pairs=[(a,b) for a in entries for b in entries if a!=b]
                    for n,(a,b) in enumerate(pairs,1):app.state.store.db.execute("INSERT INTO competition_fixtures(id,competition_id,ordinal,red_team_id,blue_team_id,status,result) VALUES(?,?,?,?,?,'completed',?)",(uuid.uuid4().hex,legacy['id'],n,a,b,json.dumps(dict(winner='red',reason='base_capture'))))
            ladder_route='competitions/'+ladder['id']
            elo_route='competitions/'+item['id']
            guest_boards={}
            for scheme,game in zip(('light','dark'),book.games(rid)[4:6]):
                job=app.state.store.create('job','competition:'+item['id'],dict(kind='match',status='playing',profile=item['config']['profile'],report=None,submission_ids=[]))
                with app.state.store.transaction():app.state.store.db.execute("UPDATE elo_games SET status='queued',current_job_id=? WHERE id=?",(job['id'],game['id']))
                state=engine.initial_state();app.state.store.event(job['id'],'state',dict(state=state,counts=engine.counts(state),clocks=dict(red=60000,blue=60000)))
                guest_boards[scheme]=(game,job,state)
            publisher=Publisher(app.state.settings)
            try:publisher.publish()
            finally:publisher.close()
            publish_stop=threading.Event();publish_thread=None
            if extended:
                def publish_loop():
                    public=Publisher(app.state.settings)
                    try:
                        while not publish_stop.wait(1):public.publish()
                    finally:public.close()
                publish_thread=threading.Thread(target=publish_loop,daemon=True);publish_thread.start()
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                for scheme in ('light','dark'):
                    sessions={}
                    routes=[('organizer','operations'),('ui-fixture','practice'),('ui-fixture','benchmark'),('organizer','home'),('organizer',elo_route),('organizer',ladder_route),('ui-fixture','team')]
                    if extended:routes += [('organizer',r) for r in ('announcements','admin','users','graders','email','audit','rulesets','standings?competition='+legacy['id'])]+[('ui-fixture',r) for r in ('submissions','matching','jobs/'+entry['benchmark_id'])]
                    for username,route in routes:
                        context=browser.new_context(color_scheme=scheme,reduced_motion='reduce',viewport=dict(width=1440,height=1000))
                        page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                        if extended and username in sessions:
                            context.add_cookies(sessions[username]);page.goto(origin+'/#/'+route)
                        else:
                            page.goto(origin+'/#/login');page.locator('[name=username]').fill(username)
                            page.locator('[name=login-password]').fill(PASSWORD);page.locator('#login-form button[type=submit]').click()
                        expect(page.locator('.sidebar')).to_be_visible()
                        if extended:sessions[username]=context.cookies()
                        page.goto(origin+'/#/'+route)
                        if route=='operations':
                            panel=page.locator('#sample-bot-catalog');expect(panel).to_contain_text('không tính Elo')
                            expect(panel.locator('tbody tr')).to_have_count(1)
                            expect(panel.locator('input[type=file], [name=bot-level]')).to_have_count(0)
                        elif route==elo_route:
                            expect(page.locator('#elo-page').first).to_contain_text('vòng tròn')
                            page.locator('.elo-matrix summary').click()
                            expect(page.locator('.elo-matrix a')).to_have_count(24)
                            with page.expect_response(lambda response:'/standings' in response.url):
                                pass
                            page.evaluate('()=>new Promise(requestAnimationFrame)')
                            expect(page.locator('.elo-matrix')).to_have_attribute('open','')
                            page.locator('[data-action=elo-exclude]').first.click()
                            page.locator('#elo-exclude-form [name=reason]').fill('Private fixture reason <script>')
                            page.locator('#elo-exclude-form button[type=submit]').click()
                            expect(page.locator('#elo-page')).to_contain_text('BTC loại khỏi xếp hạng')
                            expect(page.locator('body')).not_to_contain_text('Private fixture reason')
                            page.locator('[data-action=elo-exclude][data-excluded=true]').click()
                            page.locator('#elo-exclude-form [name=reason]').fill('Fixture restored by BTC')
                            page.locator('#elo-exclude-form button[type=submit]').click()
                            expect(page.locator('#elo-page')).not_to_contain_text('BTC loại khỏi xếp hạng')
                            page.locator('.elo-matrix summary').click()
                            result['checks'].append(dict(route='exclude-restore',scheme=scheme,passed=True))
                        elif route==ladder_route:
                            expect(page.locator('#elo-page')).to_contain_text('Đếm ngược')
                            expect(page.locator('#elo-page')).to_contain_text('Tăng tuần này')
                        elif route=='home':
                            expect(page.locator('#main')).to_contain_text('Lưu trữ dữ liệu')
                            expect(page.locator('[data-action=retention-run]')).to_be_visible()
                        elif route=='benchmark':
                            expect(page.locator('#main')).to_contain_text('0,5 giây riêng')
                            expect(page.locator('#benchmark-form [name=match_ms]')).to_have_count(0)
                        elif route=='team':
                            expect(page.locator('#people-page')).to_contain_text('SHA đang dùng')
                            expect(page.locator('#people-page')).to_contain_text('Đang kiểm tra')
                            expect(page.locator('#people-page svg[aria-label="Biểu đồ Elo theo đợt"]')).to_have_count(1)
                        elif route=='practice':
                            expect(page.locator('#practice-form')).to_be_visible()
                            expect(page.locator('[name=blue_submission_id] option').filter(has_text='Bot mẫu')).to_have_count(1)
                            page.locator('[name=match_ms]').select_option('10000')
                        if extended and route=='announcements':
                            title='Thông báo '+scheme+' <svg/onload=alert(1)>'
                            page.locator('#announcement-form [name=title]').fill(title)
                            page.locator('#announcement-form [name=body]').fill('<img src=x onerror=alert(1)> Nội dung thử; không phải thông báo thật.')
                            started=time.monotonic();page.locator('#announcement-form button[type=submit]').click()
                            expect(page.locator('#announcements-banner')).to_contain_text(title,timeout=60000)
                            result['checks'].append(dict(route='notice-banner',scheme=scheme,seconds=round(time.monotonic()-started,3)))
                            expect(page.locator('[onload],[onerror]')).to_have_count(0)
                        if extended:
                            expect(page.locator('#main h1')).to_be_visible()
                            expect(page.locator('#main')).not_to_contain_text('Chưa tải được trang')
                        for width in (1440,390):
                            page.set_viewport_size(dict(width=width,height=1000 if width==1440 else 844))
                            page.screenshot(path=str(output/f'{route.replace("/","-").replace("?","-")}-{scheme}-{width}.png'),full_page=True)
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(scheme,route,width,'overflow')
                            background=page.locator('body').evaluate('e=>getComputedStyle(e).backgroundColor')
                            assert background==('rgb(20, 32, 25)' if scheme=='dark' else 'rgb(240, 244, 241)'),background
                            contrast=text_contrast(page)
                            failures=[x for x in contrast if x['ratio']<4.5]
                            if failures:result['contrast_failures'].append(dict(route=route,scheme=scheme,width=width,failures=failures))
                            if not extended:assert not failures,(route,scheme,width,failures)
                            result['checks'].append(dict(route=route,scheme=scheme,width=width,contrast_min=min(x['ratio'] for x in contrast)))
                        if extended:expect(page.locator('[onload],[onerror]')).to_have_count(0)
                        print('checked',scheme,route,flush=True)
                        context.close()
                publisher=Publisher(app.state.settings)
                try:publisher.publish()
                finally:publisher.close()
                for scheme in ('light','dark'):
                    context=browser.new_context(color_scheme=scheme,reduced_motion='reduce',viewport=dict(width=1440,height=1000))
                    page=context.new_page();requests=[]
                    page.on('request',lambda request:requests.append(request.url))
                    page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                    for route in (('home','guide','limits','announcements','competitions','schedule','live','queue','standings?competition='+item['id']) if extended else ('queue','standings?competition='+item['id'])):
                        page.goto(origin+'/#/'+route)
                        expect(page.locator('h1')).to_be_visible()
                        if route=='queue':expect(page.locator('#main')).to_contain_text('Đang chờ kiểm tra')
                        elif route.startswith('standings'):expect(page.locator('#elo-page')).to_contain_text('vòng tròn')
                        for width in (1440,390):
                            page.set_viewport_size(dict(width=width,height=1000 if width==1440 else 844))
                            page.screenshot(path=str(output/f'guest-{route.split("?")[0]}-{scheme}-{width}.png'),full_page=True)
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(scheme,route,width)
                            contrast=text_contrast(page);assert all(x['ratio']>=4.5 for x in contrast),(scheme,route,width,contrast)
                            result['checks'].append(dict(route='guest-'+route,scheme=scheme,width=width,contrast_min=min(x['ratio'] for x in contrast)))
                    if extended:
                        page.goto(origin+'/#/limits')
                        before=page.locator('html').get_attribute('data-theme')
                        page.locator('[data-action=theme]').click();page.reload()
                        after=page.locator('html').get_attribute('data-theme');assert before!=after
                        assert page.evaluate("localStorage.getItem('uet-arena-theme')")==after
                        page.evaluate('window.arenaTheme.system()')
                        if scheme=='light':
                            notice=app.state.store.create('announcement','organizer',dict(title='Thông báo từ phiên BTC khác',body='Thử nhịp nhận banner 30 giây.',revision=1,archived_ms=None))
                            started=time.monotonic()
                            expect(page.locator('#announcements-banner')).to_contain_text(notice['title'],timeout=60000)
                            result['checks'].append(dict(route='cross-session-banner',seconds=round(time.monotonic()-started,3)))
                        for route in ('login','register','recover','verify-email','set-password','guest-register','guest-recover'):
                            page.goto(origin+'/#/'+route);expect(page.locator('.real-auth-content h2')).to_be_visible()
                            for width in (1440,390):
                                page.set_viewport_size(dict(width=width,height=1000 if width==1440 else 844))
                                page.screenshot(path=str(output/f'auth-{route}-{scheme}-{width}.png'),full_page=True)
                                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(route,scheme,width)
                                contrast=text_contrast(page);failures=[x for x in contrast if x['ratio']<4.5]
                                if failures:result['contrast_failures'].append(dict(route=route,scheme=scheme,width=width,failures=failures))
                                result['checks'].append(dict(route=route,scheme=scheme,width=width,contrast_min=min(x['ratio'] for x in contrast)))
                    game,job,state=guest_boards[scheme]
                    page.goto(origin+'/#/arena/'+item['id']+'/'+game['id'])
                    expect(page.locator('#live-board .piece-symbol')).to_have_count(18)
                    if extended:expect(page.locator('#match-status')).to_contain_text('Đang đấu')
                    expect(page.locator('#replay-slider')).to_have_attribute('max','0')
                    next_state=engine.apply(state,engine.legal_moves(state)[0])
                    app.state.store.event(job['id'],'state',dict(state=next_state,counts=engine.counts(next_state),clocks=dict(red=59000,blue=60000)))
                    publisher=Publisher(app.state.settings)
                    try:publisher.publish()
                    finally:publisher.close()
                    expect(page.locator('#replay-slider')).to_have_attribute('max','1',timeout=8000)
                    result['checks'].append(dict(route='guest-featured-live-board',scheme=scheme,passed=True))
                    for width in (1440,390):
                        page.set_viewport_size(dict(width=width,height=1000 if width==1440 else 844))
                        page.screenshot(path=str(output/f'guest-board-{scheme}-{width}.png'),full_page=True)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(scheme,'guest-board',width)
                        if extended:
                            contrast=text_contrast(page);failures=[x for x in contrast if x['ratio']<4.5]
                            if failures:result['contrast_failures'].append(dict(route='guest-board',scheme=scheme,width=width,failures=failures))
                            result['checks'].append(dict(route='guest-board',scheme=scheme,width=width,contrast_min=min(x['ratio'] for x in contrast)))
                    assert any('/snapshots/featured.json' in u for u in requests),requests
                    assert not [u for u in requests if '/api/v1/competitions' in u],requests
                    assert any('/snapshots/' in u for u in requests),requests
                    result['checks'].append(dict(route='guest-static-only',scheme=scheme,passed=True))
                    context.close()
                browser.close()
            assert not result['page_errors'],result['page_errors']
            assert not result['contrast_failures'],result['contrast_failures']
            print(json.dumps(result,ensure_ascii=False),flush=True)
        finally:
            if 'publish_stop' in locals():publish_stop.set()
            if 'publish_thread' in locals() and publish_thread:publish_thread.join(10)
            server.should_exit=True;thread.join(30)
            (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
