"""Playwright + real API/Docker, isolated disposable accounts and data.

Optional test dependency: pip install playwright; python -m playwright install
--with-deps chromium. Never connects to .platform-state or a deployed server.
"""
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings

PASSWORD='browser-test-only-password'

def main():
    import uvicorn
    from playwright.sync_api import sync_playwright,expect
    os.environ['ARENA_WORKERS']='2'
    output=ROOT/'test-results/browser';output.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    def passed(name):checks.append(name);print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory(prefix='arena-browser-') as tmp:
        store=Store(tmp,recover_jobs=False);accounts=Accounts(store)
        accounts.bootstrap_admin('organizer',PASSWORD)
        # Synthetic team profiles; browser tests don't handle real credentials.
        from arena.service import Service
        from arena_api.competitions import Competitions
        from arena_api.operations import Operations
        service=Service(store,start_worker=False);comp=Competitions(store,service,accounts);ops=Operations(store,accounts,service,comp)
        for i in range(3):
            profile={'team':f'Browser Team {i}','email':f'browser{i}@example.test','phone':'','members':[{'name':f'Test student {n}','student_id':f'BROW{i}{n}00'} for n in range(3)],'confirmed':True}
            issued=ops.create_team(profile,f'browser{i}',PASSWORD,'browser-fixture')
            with store.lock,store.db:store.db.execute('UPDATE accounts SET must_change=0 WHERE id=?',(issued['account']['id'],))
        store.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(1024)
        origin='http://127.0.0.1:'+str(sock.getsockname()[1])
        app=create_app(Settings(data=Path(tmp),origin=origin))
        server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,limit_concurrency=1024,proxy_headers=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        try:
            until=time.monotonic()+60
            while not server.started and time.monotonic()<until:time.sleep(.05)
            assert server.started
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                contexts=[browser.new_context(viewport={'width':1440,'height':1000}) for _ in range(5)]
                pages=[c.new_page() for c in contexts]
                for page in pages:page.on('pageerror',lambda error:errors.append(str(error)))
                admin,*team_pages,guest=pages
                def login(page,name):
                    page.goto(origin+'/#/login');page.locator('[name=username]').fill(name);page.locator('[name=login-password]').fill(PASSWORD)
                    page.locator('#login-form button[type=submit]').click();expect(page.locator('.sidebar')).to_be_visible()
                def upload(page,runtime,version,broken=False):
                    page.goto(origin+'/#/submissions');page.locator('[name=runtime]').select_option(runtime)
                    page.locator('[name=version]').fill(version)
                    entry={'cpp17':'bot.cpp','c11':'bot.c','python3':'bot.py'}[runtime]
                    raw=io.BytesIO()
                    with zipfile.ZipFile(raw,'w',zipfile.ZIP_DEFLATED) as z:
                        z.writestr(entry,'def broken(:\n' if broken else (ROOT/'sdk'/entry).read_text())
                        if runtime=='c11':z.write(ROOT/'sdk/jsmn.h','jsmn.h')
                    page.locator('[name=source]').set_input_files({'name':'bot.zip','mimeType':'application/zip','buffer':raw.getvalue()})
                    page.locator('#submission-form button[type=submit]').click();page.wait_for_url('**/#/benchmark?submission=*')
                guest.goto(origin+'/#/home');expect(guest.locator('.sidebar')).to_be_visible()
                expect(guest.get_by_role('link',name='Đội & tài khoản',exact=True)).to_have_count(0)
                guest.goto(origin+'/#/admin');guest.wait_for_url('**/#/login')
                passed('guest navigation and direct admin URL blocked')
                login(admin,'organizer')
                admin.goto(origin+'/#/competitions')
                form=admin.locator('#competition-form');form.locator('[name=title]').fill('Browser Swiss 10x10')
                form.locator('[name=format]').select_option('swiss');form.locator('[name=rounds]').fill('2')
                form.locator('[name=max_teams]').fill('100');form.locator('[name=match_ms]').select_option('60000')
                form.locator('[name=custom-basic]').check();form.locator('[name=board-rows]').fill('10');form.locator('[name=board-cols]').fill('10')
                form.locator('[name=eliminate-types]').select_option('2');form.locator('button[type=submit]').click()
                admin.wait_for_url('**/#/competitions/*');cid=admin.url.rsplit('/',1)[1]
                admin.get_by_role('button',name='Mở đăng ký',exact=True).click()
                expect(admin.locator('.competition-summary').first).to_contain_text('Mở đăng ký')
                passed('admin creates Swiss with configurable 10x10 SDK v2 rules')
                for i,(page,runtime) in enumerate(zip(team_pages,('python3','c11','cpp17'))):
                    login(page,f'browser{i}')
                    page.goto(origin+'/#/admin');page.wait_for_url('**/#/home')
                    upload(page,runtime,'v1.0.0')
                    page.goto(origin+'/#/benchmark?competition='+cid)
                    page.locator('#benchmark-form button[type=submit]').click();page.wait_for_url('**/#/jobs/*')
                    expect(page.locator('#job-status .badge.success').first).to_have_text('Hoàn tất',timeout=180000)
                    expect(page.locator('#job-status')).to_contain_text('72')
                    page.goto(origin+'/#/competitions/'+cid)
                    page.locator('#entry-form [name=delegate]').check();page.locator('#entry-form button[type=submit]').click()
                    expect(page.locator('#entry-form')).to_contain_text('Thay bot dự giải')
                    passed(runtime+' upload, benchmark with pinned competition rules, qualified entry')
                admin.reload();admin.get_by_role('button',name='Chốt bot và tạo lịch',exact=True).click()
                admin.get_by_role('button',name='Xác nhận',exact=True).click()
                admin.get_by_role('button',name='Bắt đầu giải',exact=True).click()
                guest.goto(origin+'/#/competitions/'+cid)
                expect(guest.locator('.competition-summary').first).to_contain_text('đội')
                guest.get_by_role('link',name='Xem trận',exact=True).first.click()
                expect(guest.locator('#live-board .board-square')).to_have_count(100)
                expect(guest.locator('#live-board .board-piece[data-kind]:not([data-kind=""])').first).to_be_visible(timeout=120000)
                guest.screenshot(path=str(output/'live-10x10.png'),full_page=True)
                guest.get_by_role('button',name='Xoay bàn',exact=True).click();guest.get_by_role('button',name='Đầu trận',exact=True).click()
                passed('guest sees real 100-square board, live pieces, flip and replay controls')
                # Observe completion via genuine UI refresh, then open replay/standings.
                until=time.monotonic()+180
                while time.monotonic()<until:
                    admin.reload()
                    if 'Hoàn tất' in admin.locator('.competition-summary').first.inner_text():break
                    if 'cần xử lý' in admin.locator('.competition-summary').first.inner_text():
                        admin.screenshot(path=str(output/'attention.png'),full_page=True)
                        raise AssertionError('Official fixture needs attention; inspect diagnostics.json')
                    time.sleep(1)
                else:raise AssertionError('Swiss did not finish')
                guest.goto(origin+'/#/standings?competition='+cid);expect(guest.locator('#main')).to_contain_text('Buchholz')
                guest.screenshot(path=str(output/'swiss-standings.png'),full_page=True)
                guest.goto(origin+'/#/competitions/'+cid);guest.get_by_role('link',name='Replay',exact=True).first.click()
                expect(guest.locator('#live-board .board-square')).to_have_count(100)
                guest.get_by_role('button',name='Phát replay',exact=True).click()
                passed('full real Swiss tournament completes, Buchholz standings and replay work')
                # Organizer sample upload and real practice against it.
                admin.goto(origin+'/#/operations');form=admin.locator('#sample-bot-form')
                form.locator('[name=bot-name]').fill('Bot mẫu dễ');form.locator('[name=runtime]').select_option('python3')
                raw=io.BytesIO()
                with zipfile.ZipFile(raw,'w') as z:z.write(ROOT/'sdk/bot.py','bot.py')
                form.locator('[name=source]').set_input_files({'name':'sample.zip','mimeType':'application/zip','buffer':raw.getvalue()})
                form.locator('button[type=submit]').click();expect(admin.locator('#main')).to_contain_text('Bot mẫu dễ')
                page=team_pages[0];page.goto(origin+'/#/practice')
                option=page.locator('[name=blue_submission_id] option').filter(has_text='Bot mẫu dễ').get_attribute('value')
                page.locator('[name=blue_submission_id]').select_option(option);page.locator('[name=delegate]').check();page.locator('#practice-form button[type=submit]').click()
                page.wait_for_url('**/#/arena/practice/*');expect(page.locator('#live-board .board-square')).to_have_count(81)
                expect(page.locator('#match-status')).to_contain_text('Hoàn tất',timeout=180000)
                passed('organizer uploads sample bot and team practices against it')
                upload(page,'python3','v1.0.1',broken=True)
                page.locator('#benchmark-form button[type=submit]').click();page.wait_for_url('**/#/jobs/*')
                expect(page.locator('#job-status .badge.danger')).to_have_text('Lỗi',timeout=120000)
                expect(page.locator('#job-status')).to_contain_text('build_error')
                page.screenshot(path=str(output/'python-build-error.png'),full_page=True)
                passed('Python compilation failure is visible on UI')
                assert not errors,errors
                browser.close()
        except Exception:
            try:
                for i,page in enumerate(pages):page.screenshot(path=str(output/f'failure-{i}.png'),full_page=True)
            except Exception:pass
            raise
        finally:
            if hasattr(app.state,'store'):
                with app.state.store.lock:
                    jobs=app.state.store.list('job')
                    diagnostic={'jobs':[{'id':j['id'],'kind':j['kind'],'status':j['status'],'error':j.get('error'),
                                         'report_error':(j.get('report') or {}).get('error'),'official':j.get('official')} for j in jobs],
                                'scheduler_error':app.state.competitions.scheduler_error}
                    (output/'diagnostics.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2))
            server.should_exit=True;thread.join(timeout=180)
            (output/'results.json').write_text(json.dumps({'checks':checks,'page_errors':errors,'data_scope':'TemporaryDirectory, synthetic accounts, real Docker'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
