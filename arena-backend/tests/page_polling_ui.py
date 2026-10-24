"""Focused B acceptance: documented navigation, hidden tabs and live check progress."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import sync_playwright,expect
from grader_browser import PASSWORD,ROOT
from arena.conf import Secrets
from arena.config import benchmark_profile
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.publisher import Publisher
from arena_api.settings import Settings


def main():
    output=ROOT/'test-results/m1/g10b/ui';output.mkdir(parents=True,exist_ok=True)
    report={'pages':[],'errors':[]}
    with tempfile.TemporaryDirectory(prefix='g10b-ui-') as tmp:
        store=Store(tmp);Accounts(store);store.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        origin='http://127.0.0.1:'+str(sock.getsockname()[1])
        app=create_app(Settings(data=Path(tmp),origin=origin,secrets=Secrets({})),start_worker=False)
        server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        try:
            deadline=time.monotonic()+20
            while not server.started and time.monotonic()<deadline:time.sleep(.05)
            assert server.started
            issued=app.state.operations.create_team(dict(team='Polling fixture',email='fixture@vnu.test',phone='',confirmed=True,
                members=[dict(name='Fixture '+str(i),student_id='POLL'+str(i)) for i in range(4)]),'polling-fixture',PASSWORD,'test')
            team=issued['account']['team_id']
            with app.state.store.transaction():app.state.store.db.execute('UPDATE accounts SET must_change=0 WHERE id=?',(issued['account']['id'],))
            competition=app.state.competitions.create('test',dict(title='Polling ladder',format='elo',max_teams=20,match_ms=10000,
                runtime_id='sha256:'+'1'*64,submission_deadline=9_000_000_000_000),'polling-cup')
            app.state.competitions.transition('test',competition['id'],'open','polling-open')
            job=app.state.store.create('job',team,dict(kind='benchmark',status='building',submission_ids=[],profile=benchmark_profile().public()))
            sha='1'*40
            with app.state.store.transaction():
                db=app.state.store.db
                db.execute("INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms) VALUES(?,1,'fixture','repo',1,1)",(team,))
                db.execute("INSERT INTO github_commits(team_id,sha,received_ms,state) VALUES(?,?,1,'checking')",(team,sha))
                db.execute("INSERT INTO github_checks(team_id,sha,purpose,job_id,state) VALUES(?,?,'practice',?,'checking')",(team,sha,job['id']))
            publisher=Publisher(app.state.settings)
            try:publisher.publish()
            finally:publisher.close()
            contracts={}
            for line in (ROOT.parent/'docs/UI.md').read_text().splitlines():
                if line.startswith('| '):
                    cols=[v.strip() for v in line.split('|')]
                    if cols[1] in ('home','team','submissions','standings','schedule','queue'):contracts[cols[1]]=cols[2:4]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                for lang in ('vi','en'):
                    for theme in ('light','dark'):
                        context=browser.new_context(color_scheme=theme,viewport=dict(width=1440,height=1000))
                        context.add_init_script("localStorage.setItem('uet-arena-language',"+json.dumps(lang)+")")
                        assert context.request.post(origin+'/api/v1/auth/login',data=dict(username='polling-fixture',password=PASSWORD)).status==200
                        page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
                        calls=[];page.on('request',lambda r:calls.append(urlsplit(r.url).path+('?' +urlsplit(r.url).query if urlsplit(r.url).query else '')))
                        page.goto(origin+'/#/home');page.wait_for_load_state('networkidle')
                        for route in ('team','submissions','standings','schedule','queue','home'):
                            expected_api,expected_files=contracts[route]
                            path=('/api/v1'+expected_api if expected_api!='—' else '/snapshots/'+expected_files.split(', ')[0]).replace('{id}',competition['id'])
                            calls.clear()
                            with page.expect_response(lambda r:r.url==origin+path):page.goto(origin+'/#/'+route)
                            page.wait_for_timeout(150)
                            api=[x.removeprefix('/api/v1') for x in calls if x.startswith('/api/v1')]
                            files=[x.removeprefix('/snapshots/') for x in calls if x.startswith('/snapshots/') and x!='/snapshots/announcements.json']
                            def expected(value):return [] if value=='—' else [x.replace('{id}',competition['id']) for x in value.split(', ')]
                            assert api==expected(expected_api),(lang,theme,route,api)
                            assert sorted(files)==sorted(expected(expected_files)),(route,files)
                            assert len(api)<=2
                            report['pages'].append(dict(lang=lang,theme=theme,route=route,api=api))
                        with page.expect_response(lambda r:r.url==origin+'/api/v1/ui/page?view=team'):page.goto(origin+'/#/team')
                        expect(page.locator('#github-page')).to_be_visible()
                        page.evaluate("window.testHidden=true;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden});document.dispatchEvent(new Event('visibilitychange'))")
                        calls.clear();page.wait_for_timeout(10500 if (lang,theme)==('vi','light') else 500)
                        assert not [c for c in calls if c.startswith(('/api/v1','/snapshots/'))],calls
                        app.state.store.update(job['id'],status='validating')
                        app.state.store.event(job['id'],'progress',dict(completed=47))
                        page.evaluate("window.testHidden=false;document.dispatchEvent(new Event('visibilitychange'))")
                        expect(page.locator('#github-page')).to_contain_text('47/72',timeout=5000)
                        page.screenshot(path=str(output/f'{lang}-{theme}.png'),full_page=True)
                        context.close()
                browser.close()
            assert not report['errors'],report['errors']
            (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(dict(navigation_checks=len(report['pages']),hidden_resume_progress_checks=4,errors=report['errors'])))
        finally:
            server.should_exit=True;thread.join(10);sock.close()


if __name__=='__main__':main()
