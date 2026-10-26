"""Real local email flow; registration layouts in both languages, themes and sizes."""
from pathlib import Path
import json,os,re,secrets,subprocess,sys,tempfile,threading,time
import httpx,uvicorn
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from arena.conf import Secrets
from arena_api.app import create_app
from arena_api.settings import Settings
from email_integration import port,wait_http

def main():
    out=ROOT/'test-results/m2/s/auth';out.mkdir(parents=True,exist_ok=True)
    report={'layouts':[],'errors':[],'doc_images':[]}
    with tempfile.TemporaryDirectory(prefix='arena-s-auth-') as directory:
        base=Path(directory);smtp,web,hub=port(),port(),port();origin=f'http://127.0.0.1:{hub}';mailbox=f'http://127.0.0.1:{web}'
        secret=base/'secrets.toml';secret.write_text('verification_hmac_key='+json.dumps(secrets.token_urlsafe(32)));secret.chmod(0o600)
        config=base/'config.toml';config.write_text(f'''[hub]
data_dir={json.dumps(str(base/'data'))}
origin={json.dumps(origin)}
port={hub}
[[email.providers]]
name="mailpit"
host="127.0.0.1"
port={smtp}
tls="none"
sender="no-reply@uetbotarena.id.vn"
daily_limit=10000
hourly_limit=10000
''')
        env={**os.environ,'ARENA_CONFIG':str(config),'ARENA_SECRETS':str(secret)}
        app=create_app(Settings(config_file=config,secrets=Secrets.load(secret)),start_worker=False)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=hub,access_log=False,log_level='warning'))
        thread=threading.Thread(target=server.run,daemon=True)
        mailpit=subprocess.Popen([str(Path.home()/'.local/bin/mailpit'),'--smtp',f'127.0.0.1:{smtp}','--listen',f'127.0.0.1:{web}','--database',str(base/'mail.db'),'--disable-version-check'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        mailer=None;log=(out/'services.log').open('w')
        try:
            wait_http(mailbox,'/api/v1/info');thread.start();wait_http(origin,'/api/v1/health/ready')
            mailer=subprocess.Popen([sys.executable,'-m','arena_api.mailer'],cwd=ROOT,env=env,stdout=log,stderr=log)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);serial=0
                for width in [1440,390]:
                    for theme in ['light','dark']:
                        for lang in ['vi','en']:
                            ctx=browser.new_context(viewport=dict(width=width,height=1000),device_scale_factor=2,color_scheme=theme)
                            ctx.add_init_script("localStorage.setItem('uet-arena-language',"+json.dumps(lang)+");localStorage.setItem('uet-arena-theme',"+json.dumps(theme)+")")
                            page=ctx.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
                            page.goto(origin+'/#/login');page.wait_for_selector('#login-form')
                            login_size=page.locator('.real-auth-content>h2').evaluate('e=>getComputedStyle(e).fontSize')
                            def shot(state):
                                expect(page.locator('#people-auth')).to_be_visible();page.evaluate('document.fonts.ready')
                                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(state,width,'overflow')
                                heading=page.locator('#people-auth>h2')
                                assert heading.evaluate('e=>getComputedStyle(e).fontSize')==login_size
                                assert heading.evaluate('e=>getComputedStyle(e).marginBottom')=='12px'
                                assert page.locator('#people-auth>p').evaluate('e=>getComputedStyle(e).marginBottom')=='24px'
                                links=page.locator('.people-auth-links');assert links.evaluate('e=>getComputedStyle(e).gap')=='6px'
                                assert links.evaluate('e=>getComputedStyle(e).marginTop')=='24px'
                                if state=='code':assert page.locator('.email-sent-to').evaluate('e=>getComputedStyle(e).marginTop')=='16px'
                                page.evaluate("document.getElementById('toast').style.display='none'")
                                mask=[page.locator('input[type=password]'),page.locator('input[name=email]:visible'),page.locator('input[name=email-code]'),page.locator('.email-sent-to strong')]
                                name=f'{width}-{theme}-{lang}-{state}.png';page.screenshot(path=str(out/name),full_page=True,mask=mask,mask_color='#cbd5e1')
                                report['layouts'].append(dict(width=width,theme=theme,language=lang,state=state,screenshot=name))
                                if (width,theme,lang)==(1440,'light','vi') and state!='recover':
                                    target=page.locator('#people-auth');box=target.bounding_box();assert box
                                    button=page.locator('#people-auth button[type=submit]').first
                                    button.evaluate("e=>{e.style.outline='3px solid #d22323';e.style.outlineOffset='3px'}")
                                    name='doc-'+state+'.png'
                                    page.screenshot(path=str(out/name),clip=dict(x=box['x']-16,y=box['y']-16,width=box['width']+32,height=box['height']+32),mask=mask,mask_color='#cbd5e1')
                                    button.evaluate("e=>{e.style.outline='';e.style.outlineOffset=''}")
                                    report['doc_images'].append(dict(state=state,file=name,css_width=box['width']+32,css_height=box['height']+32,scale=2))
                            page.goto(origin+'/#/register');page.wait_for_selector('#email-request-form');serial+=1;email=f'260088{serial:02}@vnu.edu.vn'
                            page.locator('[name=email]').fill(email);shot('email')
                            page.locator('#email-request-form button[type=submit]').click();expect(page.locator('#email-code-form')).to_be_visible()
                            deadline=time.monotonic()+30;code=None
                            while not code:
                                for message in httpx.get(mailbox+'/api/v1/messages').json()['messages']:
                                    if any(x['Address']==email for x in message['To']):code=re.search(r': (\d{6})$',message['Subject']).group(1);break
                                assert time.monotonic()<deadline,'Local Mailpit did not receive code';time.sleep(.1)
                            shot('code');page.locator('[name=email-code]').fill(code);page.locator('#email-code-form button[type=submit]').click()
                            expect(page.locator('#personal-password-form')).to_be_visible();shot('password')
                            page.goto(origin+'/#/recover');page.wait_for_selector('#email-request-form');shot('recover');ctx.close()
                browser.close()
            assert len(report['layouts'])==32 and not report['errors'],report['errors']
            assert len({r['css_width'] for r in report['doc_images']})==1
            (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print('32 auth layouts passed; no horizontal overflow/page errors; 3 same-width document crops at 2x.')
        finally:
            if mailer and mailer.poll() is None:mailer.terminate();mailer.wait(15)
            server.should_exit=True
            if thread.is_alive():thread.join(15)
            if mailpit.poll() is None:mailpit.terminate();mailpit.wait(10)
            log.close()

if __name__=='__main__':main()
