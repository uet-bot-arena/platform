"""English/Vietnamese acceptance on the seeded real API, with private fixture accounts."""
import re
from pathlib import Path
from playwright.sync_api import expect
from grader_browser import ROOT,PASSWORD,text_contrast


def survey(browser,origin,result,output):
    for scheme in ('light','dark'):
        context=browser.new_context(color_scheme=scheme,reduced_motion='reduce',viewport=dict(width=1440,height=1000))
        context.add_init_script("if(!localStorage.getItem('uet-arena-language'))localStorage.setItem('uet-arena-language','en')")
        catalog=context.request.get(origin+'/snapshots/competitions.json').json()['items']
        elo=[item['id'] for item in catalog if item['config']['format']=='elo']
        detail_routes=['competitions/'+ident for ident in elo]
        ranking_routes=['standings?competition='+ident for ident in elo]
        boards=[]
        for ident in elo:
            fixtures=context.request.get(origin+'/snapshots/fixtures/'+ident+'.json').json()['items']
            played=next((f for f in fixtures if f['status']=='completed' and context.request.get(origin+'/snapshots/fixtures/'+ident+'/'+f['id']+'.json').json().get('attempt_id')),None)
            if played:boards.append('replay/'+ident+'/'+played['id'])
        page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
        for user,routes in [(None,['home','guide','limits','announcements','competitions','schedule','live','queue','standings','login','register','recover','verify-email','set-password','guest-register','guest-recover']),('organizer',['home','announcements','admin','users','graders','audit','rulesets','operations','competitions']),('ui-fixture',['home','team','submissions','matching','benchmark','practice','competitions'])]:
            routes=list(routes)+(detail_routes if user=='organizer' else ranking_routes+boards if user is None else [])
            if user:
                page.set_viewport_size(dict(width=1440,height=1000))
                page.goto(origin+'/#/login');expect(page.locator('#login-form')).to_be_visible()
                page.locator('[name=username]').fill(user);page.locator('[name=login-password]').fill(PASSWORD)
                calls=[]
                def signing(route):
                    calls.append(1)
                    expect(page.locator('#login-form [type=submit]')).to_be_disabled()
                    expect(page.locator('#login-form [type=submit]')).to_have_text('Signing in…')
                    page.locator('#login-form').evaluate('(form)=>form.requestSubmit()')
                    route.continue_()
                page.route('**/api/v1/auth/login',signing)
                page.locator('#login-form [type=submit]').click();expect(page.locator('.sidebar')).to_be_visible()
                page.unroute('**/api/v1/auth/login',signing);assert len(calls)==1,calls
                result['checks'].append(dict(route='login-busy-'+user,scheme=scheme,duplicate_requests=len(calls)))
            for route in routes:
                page.goto(origin+'/#/'+route)
                expect(page.locator('html')).to_have_attribute('lang','en')
                expect(page.locator('#main')).to_be_visible()
                expect(page.locator('#main')).not_to_contain_text('Loading data…')
                if route=='guide':
                    expect(page.locator('#main')).to_contain_text('OTTv2 rules (English)')
                    expect(page.locator('#main')).to_contain_text('Account, team and repository')
                if route.startswith('replay/'):
                    expect(page.locator('.board-piece[data-kind]').first).to_be_visible()
                    expect(page.locator('#main')).not_to_contain_text('Waiting for bot host…')
                assert not re.search(r'\b(Thua|Phe|Sau|Xem|Trang|Xong)\b',page.locator('#main').inner_text()),(user,route)
                for width in (1440,390):
                    page.set_viewport_size(dict(width=width,height=1000 if width==1440 else 844))
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(scheme,user,route,width)
                    contrast=text_contrast(page);assert all(x['ratio']>=4.5 for x in contrast),(scheme,user,route,width,contrast)
                    controls=page.locator('button, .sidebar nav, .field > span, .page-heading h1, thead th').all_inner_texts()
                    untranslated=[s for s in controls if re.search('[ĂăÂâĐđÊêÔôƠơƯưẠ-ỹ]',s) and s not in ('VI','EN')]
                    result.setdefault('english_text_review',[]).extend(dict(user=user,route=route,text=s) for s in untranslated)
                    slug=route.replace('/','-').replace('?','-').replace('=','-')
                    page.screenshot(path=str(output/f'en-{user or "guest"}-{slug}-{scheme}-{width}.png'),full_page=True)
                    result['checks'].append(dict(route='en-'+route,user=user,scheme=scheme,width=width,contrast_min=min(x['ratio'] for x in contrast)))
            if user:
                page.set_viewport_size(dict(width=1440,height=1000))
                page.locator('[data-action=logout]').click();expect(page.locator('#login-form')).to_be_visible()
        page.goto(origin+'/#/login');page.locator('[data-action=language]').click()
        expect(page.locator('html')).to_have_attribute('lang','vi');expect(page.locator('#login-form [type=submit]')).to_have_text('Đăng nhập')
        page.reload();expect(page.locator('html')).to_have_attribute('lang','vi')
        page.locator('[data-action=language]').click();expect(page.locator('html')).to_have_attribute('lang','en')
        expect(page.locator('#login-form [type=submit]')).to_have_text('Sign in')
        result['checks'].append(dict(route='language-toggle-and-reload',scheme=scheme))
        context.close()


if __name__=='__main__':
    from elo_ui import main
    main(ROOT/'test-results/m1/g12/i18n-ui',extended=True,english=True)
