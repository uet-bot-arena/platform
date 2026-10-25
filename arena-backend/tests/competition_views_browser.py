"""Q browser acceptance on disposable data, using real API/qualification functions."""
from pathlib import Path
import json
import sys
import threading
import uuid

import uvicorn
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_competition_views import CompetitionViews
from email_integration import port,wait_http
from arena_api.publisher import Publisher


def main():
    output=ROOT.parents[1]/'platform-lab/m2-q-2026-10-25/browser';output.mkdir(parents=True,exist_ok=True)
    fixture=CompetitionViews('runTest');fixture.setUp();server=None;thread=None;errors=[];checks=[]
    try:
        fixture.configure();fixture.repositories()
        rid=fixture.book.start(fixture.ident,'ladder',1,1000,fixture.entries)
        fixture.complete_games(rid);fixture.book.finish(rid,2000)
        other=fixture.post(fixture.client,'/admin/competitions',dict(title='Local Round Robin',match_ms=60000),expected=201)
        with fixture.store.transaction():fixture.store.db.execute("UPDATE competitions SET status='open' WHERE id=?",(other['id'],))
        for tid,entry in list(fixture.entries.items())[:2]:
            fixture.comp.enter('fixture',tid,other['id'],dict(submission_id=entry['submission_id'],benchmark_id=entry['benchmark_id']),uuid.uuid4().hex)
        publisher=Publisher(fixture.settings)
        try:publisher.publish()
        finally:publisher.close()
        listen=port();origin=f'http://127.0.0.1:{listen}'
        object.__setattr__(fixture.settings,'origin',origin)
        fixture.settings.secrets._values['verification_hmac_key']='test-browser-download-key'
        server=uvicorn.Server(uvicorn.Config(fixture.app,host='127.0.0.1',port=listen,lifespan='off',access_log=False,log_level='warning'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start();wait_http(origin,'/api/v1/health/ready')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            admin=browser.new_context(viewport={'width':1440,'height':900},color_scheme='light')
            assert admin.request.post(origin+'/api/v1/auth/login',data=dict(username='organizer',password='test-only-admin-password')).ok
            page=admin.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/#/home');expect(page.locator('[name=competition-scope]')).to_be_visible()
            page.select_option('[name=competition-scope]',fixture.ident)
            expect(page).to_have_url(origin+'/#/home?competition='+fixture.ident)
            expect(page.locator('.stat-value').first).to_have_text('4')
            page.reload();expect(page.locator('[name=competition-scope]')).to_have_value(fixture.ident)
            page.screenshot(path=str(output/'admin-elo-light.png'),full_page=True)
            page.select_option('[name=competition-scope]',other['id']);expect(page.locator('.stat-value').first).to_have_text('2')
            checks+=['URL scope survives reload','Elo 4 and round robin 2 active teams']
            for route in ['admin','users']:
                page.goto(origin+'/#/'+route+'?competition='+fixture.ident)
                expect(page.locator('[name=competition-scope]')).to_have_value(fixture.ident)
                expect(page.locator('#main')).to_contain_text('Trong giải')
                search=page.locator('[name=admin-search]' if route=='admin' else '[name=people-search]');search.fill('elo-0')
                expect(page.locator('#main')).to_contain_text('Elo 0')
                page.screenshot(path=str(output/(route+'-filtered.png')),full_page=True)
            page.goto(origin+'/#/graders');expect(page.locator('.grader-package')).to_be_visible()
            text=page.locator('.grader-package').inner_text()
            assert all(v not in text for v in ['SHA-256:','/downloads/','install-grader.ps1 -HubUrl'])
            expect(page.locator('[data-action=copy-grader-controls]')).to_have_count(2)
            page.screenshot(path=str(output/'graders-light.png'),full_page=True)
            page.evaluate("localStorage.setItem('uet-arena-theme','dark')");page.reload()
            page.screenshot(path=str(output/'graders-dark.png'),full_page=True)
            checks+=['Both admin tables scoped and searchable','Grader panel only requirements and clipboard controls']
            student=browser.new_context(viewport={'width':1440,'height':900})
            assert student.request.post(origin+'/api/v1/auth/login',data=dict(username='elo-0',password='test-only-new-team-password')).ok
            team=student.new_page();team.on('pageerror',lambda e:errors.append(str(e)))
            for route in ['home','team']:
                team.goto(origin+'/#/'+route);expect(team.locator('#main')).to_contain_text('Giải của đội')
                expect(team.locator('#main')).to_contain_text('Round storage')
                team.screenshot(path=str(output/('team-'+route+'-light.png')),full_page=True)
            team.locator('[data-action=language]').click();expect(team.locator('#main')).to_contain_text('Team competitions')
            team.evaluate("localStorage.setItem('uet-arena-theme','dark')");team.reload();expect(team.locator('#main')).to_contain_text('Team competitions')
            team.screenshot(path=str(output/'team-dark-en.png'),full_page=True)
            checks+=['Team home and profile show competitions','New team panel in English and dark theme']
            assert not errors,errors
            browser.close()
        (output.parent/'q6-browser.json').write_text(json.dumps(dict(checks=checks,page_errors=errors),ensure_ascii=False,indent=2),encoding='utf-8')
        print('Q browser passed:',len(checks),'checks; zero page errors')
    finally:
        if server:server.should_exit=True
        if thread:thread.join(15)
        fixture.tearDown()


if __name__=='__main__':main()
