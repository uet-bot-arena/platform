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
from arena_api.elo_rounds import RoundBook
import test_competitions as helpers
from arena.conf import Secrets
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings


def main():
    output=ROOT/'test-results/m1/g7b/ui';output.mkdir(parents=True,exist_ok=True)
    result=dict(checks=[],page_errors=[])
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
            issued=app.state.operations.create_team(dict(team='UI fixture',email='ui@example.test',phone='',confirmed=True,
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
            ladder_route='competitions/'+ladder['id']
            elo_route='competitions/'+item['id']
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                for scheme in ('light','dark'):
                    for username,route in (('organizer','operations'),('ui-fixture','practice'),('organizer',elo_route),('organizer',ladder_route),('ui-fixture','team')):
                        context=browser.new_context(color_scheme=scheme,reduced_motion='reduce',viewport=dict(width=1440,height=1000))
                        page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                        page.goto(origin+'/#/login');page.locator('[name=username]').fill(username)
                        page.locator('[name=login-password]').fill(PASSWORD);page.locator('#login-form button[type=submit]').click()
                        expect(page.locator('.sidebar')).to_be_visible();page.goto(origin+'/#/'+route)
                        if route=='operations':
                            panel=page.locator('#sample-bot-catalog');expect(panel).to_contain_text('không tính Elo')
                            expect(panel.locator('tbody tr')).to_have_count(1)
                            expect(panel.locator('input[type=file], [name=bot-level]')).to_have_count(0)
                        elif route==elo_route:
                            expect(page.locator('#elo-page').first).to_contain_text('vòng tròn')
                            page.locator('.elo-matrix summary').click()
                            expect(page.locator('.elo-matrix a')).to_have_count(24)
                        elif route==ladder_route:
                            expect(page.locator('#elo-page')).to_contain_text('Đếm ngược')
                            expect(page.locator('#elo-page')).to_contain_text('Tăng tuần này')
                        elif route=='team':
                            expect(page.locator('#people-page')).to_contain_text('SHA đang dùng')
                            expect(page.locator('#people-page')).to_contain_text('Đang kiểm tra')
                            expect(page.locator('#people-page svg[aria-label="Biểu đồ Elo theo đợt"]')).to_have_count(1)
                        else:
                            expect(page.locator('#practice-form')).to_be_visible()
                            expect(page.locator('[name=blue_submission_id] option').filter(has_text='Bot mẫu')).to_have_count(1)
                            page.locator('[name=match_ms]').select_option('10000')
                        for width in (1440,390):
                            page.set_viewport_size(dict(width=width,height=1000 if width==1440 else 844))
                            page.screenshot(path=str(output/f'{route.replace("/","-")}-{scheme}-{width}.png'),full_page=True)
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(scheme,route,width,'overflow')
                            background=page.locator('body').evaluate('e=>getComputedStyle(e).backgroundColor')
                            assert background==('rgb(20, 32, 25)' if scheme=='dark' else 'rgb(240, 244, 241)'),background
                            contrast=text_contrast(page)
                            failures=[x for x in contrast if x['ratio']<4.5]
                            assert not failures,(route,scheme,width,failures)
                            result['checks'].append(dict(route=route,scheme=scheme,width=width,contrast_min=min(x['ratio'] for x in contrast)))
                        context.close()
                browser.close()
            assert not result['page_errors'],result['page_errors']
            print(json.dumps(result,ensure_ascii=False),flush=True)
        finally:
            server.should_exit=True;thread.join(30)
            (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
