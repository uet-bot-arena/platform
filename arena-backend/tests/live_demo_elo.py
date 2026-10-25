"""M2-V/E, explicitly authorized four-repository production demo, resumable.

Run after V5. Credentials/checkpoints stay in ARENA_PRIVATE_DIR. No platform push.
"""
from contextlib import closing,ExitStack
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from live_e2e import PRIVATE, ROOT, api, login, organizer, require
from grader_cluster import windows_path
from arena.conf import Secrets
from arena_api.github_client import GitHubClient
sys.path.insert(0,str(ROOT.parent/'tools'))
from recompute_elo import recompute

HUB='https://uetbotarena.id.vn'
STATE=PRIVATE/'m2-v-demo-elo-checkpoint.json'
CREDENTIALS=PRIVATE/'demo-elo-teams.json'
OUTPUT=ROOT/'test-results/m2/verification'


def save(path,value):
    path.touch(mode=0o600,exist_ok=True);path.chmod(0o600)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def command(args):
    result=subprocess.run(args,cwd='/mnt/c',capture_output=True,text=True)
    require(result.returncode==0,args[0]+' failed; output withheld')
    return result.stdout.strip()


def push_comment(number,phase,state):
    key=str(number)+'-'+phase
    if key in state['pushes']:return state['pushes'][key]
    with tempfile.TemporaryDirectory(prefix='m2-v-demo-',dir=PRIVATE) as tmp:
        repo=Path(tmp)/'repo'
        command(['gh.exe','repo','clone',f'uet-bot-arena/demo-elo-{number}',windows_path(repo),'--','--depth=1'])
        def git(*args):return command(['git.exe','-C',windows_path(repo),*args])
        require(git('branch','--show-current')=='main' and not git('status','--porcelain'),'Clone must be clean main')
        file=repo/'bot/bot.cpp'
        with file.open('a',encoding='utf-8',newline='\n') as stream:stream.write('// M2-V '+phase+' '+str(time.time_ns())+'\n')
        git('add','bot/bot.cpp')
        require(git('diff','--cached','--numstat')=='1\t0\tbot/bot.cpp','Push must add exactly one comment line')
        git('commit','-m','Kiểm tra giải Elo qua GitHub')
        require('co-authored-by:' not in git('log','-1','--format=%B').lower(),'Unexpected co-author')
        sha=git('rev-parse','HEAD')
        # Persist before push: an uncertain network result must never duplicate a commit.
        state['pushes'][key]=sha;save(STATE,state)
        git('push','origin','main')
        return sha


def validate_scope(rows):
    expected={f'DEMO ELO {i}':f'demo-elo-{i}' for i in range(1,5)}
    for row in rows:
        require(row['owner']=='uet-bot-arena' and
            (expected.get(row['name'])==row['repo'] or row['name']=='THU-M2 A' and row['repo']=='bot-thu'),
            'Unexpected repository-linked team: '+row['name'])


def scope(admin):
    rows=[];offset=0
    while True:
        teams=api(admin,'GET',f'/admin/teams?limit=100&offset={offset}')['items']
        for team in teams:
            view=api(admin,'GET',f"/admin/teams/{team['id']}/github")
            if view.get('repo'):
                rows.append({'id':team['id'],'name':team['name'],'owner':view['repo']['owner'],'repo':view['repo']['name']})
        if len(teams)<100:break
        offset+=100
    validate_scope(rows)
    return rows


def setup(admin,state):
    github=GitHubClient(Secrets.load(PRIVATE/'secrets.toml',required=True))
    statuses=[]
    for i in range(1,5):
        repo=f'demo-elo-{i}';installation=github.installation('uet-bot-arena',repo)
        status,_,_=github.transport('GET',f'https://api.github.com/repos/uet-bot-arena/{repo}/tarball/main',
            {'Authorization':'Bearer '+github.token(installation,repo),'User-Agent':'uet-arena-e2e/1'},None,1024*1024)
        require(status==302,'App cannot download '+repo)
        statuses.append(status)
    state['app_tarball_statuses']=statuses;save(STATE,state)
    scope(admin)  # Refuse unexpected linked teams before creating demo data.
    saved=json.loads(CREDENTIALS.read_text()) if CREDENTIALS.exists() else {}
    for i in range(1,5):
        username=f'demo-elo-{i}'
        if username not in saved:
            issued=api(admin,'POST','/admin/teams',201,json={'username':username,'profile':{
                'team':f'DEMO ELO {i}','email':username+'@example.test','phone':'','confirmed':True,
                'members':[{'name':f'Demo Elo {i}.{k}','student_id':f'2697{i:02d}{k:02d}'} for k in range(1,5)]}})
            saved[username]=issued['temporary_password'];save(CREDENTIALS,saved)
            with closing(login(HUB,username,saved[username])) as client:
                password=secrets.token_urlsafe(18)
                api(client,'POST','/auth/password',json={'current_password':saved[username],'new_password':password})
                saved[username]=password;save(CREDENTIALS,saved)
        with closing(login(HUB,username,saved[username])) as client:
            team=api(client,'GET','/auth/me')['user']['team_id']
        view=api(admin,'GET',f'/admin/teams/{team}/github')
        if not view.get('repo'):api(admin,'POST',f'/admin/teams/{team}/github',201,json={'url':'https://github.com/uet-bot-arena/'+username})
        state.setdefault('teams',{})[str(i)]=team;save(STATE,state)
    rows=scope(admin);state['scope_before']=rows;save(STATE,state)
    other=next((r for r in rows if r['name']=='THU-M2 A'),None)
    if other is None and state.get('other_before'):
        other={'id':state['other_before']['id'],'name':'THU-M2 A'}
    require(other is not None,'Expected THU-M2 A link missing; inspect before proceeding')
    if 'other_before' not in state:
        team=api(admin,'GET',f"/admin/teams/{other['id']}")
        accounts=api(admin,'GET','/admin/accounts?limit=100')['items']
        accounts=[{k:a[k] for k in ('id','locked')} for a in accounts if a.get('team_id')==other['id']]
        require(accounts,'Missing THU-M2 A account state')
        require(not team['archived_ms'],'THU-M2 A already archived')
        state['other_before']={'id':other['id'],'revision':team['revision'],'accounts':accounts,'repo':'uet-bot-arena/bot-thu'};save(STATE,state)
    team=api(admin,'GET',f"/admin/teams/{other['id']}")
    if not team['archived_ms']:
        api(admin,'POST',f"/admin/teams/{other['id']}/archive",json={'expected_revision':team['revision']})
    state['other_archived']=True;save(STATE,state)
    if 'cid' not in state:
        now=int(api(admin,'GET','/time')['server_sent_unix_ms'])
        item=api(admin,'POST','/admin/competitions',201,headers={'Idempotency-Key':'m2-v-demo-elo-create'},json={'title':'Giải demo Elo qua GitHub','format':'elo',
            'grader_platform':'windows','max_teams':4,'submission_deadline':now+2*3600*1000,
            'ladder':{'round_hours':1},'official':{'finalists':2}})
        state.update(cid=item['id'],deadline_ms=item['submission_deadline']);save(STATE,state)
        opened=api(admin,'POST',f"/admin/competitions/{item['id']}/open")
        state['opened']=True;save(STATE,state)
    for i in range(1,5):push_comment(i,'before',state)
    state['setup_complete']=True;save(STATE,state)
    print('Demo opened:',state['cid'],'deadline',state['deadline_ms'],flush=True)


def tick(admin,state,clients):
    cid=state['cid'];now=int(api(admin,'GET','/time')['server_sent_unix_ms'])
    if now>state['deadline_ms'] and '1-after' not in state['pushes']:push_comment(1,'after',state)
    view=api(admin,'GET','/competitions/'+cid)
    for i in range(1,5):
        client=clients[i]
        github=api(client,'GET','/teams/me/github')
        elo=next((r for r in api(client,'GET','/team/elo')['items'] if r['competition_id']==cid),None)
        if elo and elo['history']:
            state.setdefault('ladder_final_ratings',{})[state['teams'][str(i)]]=elo['history'][-1]['rating']
        current=(elo or {}).get('current')
        if current and current['phase']=='ladder' and current['status']=='completed':
            state.setdefault('ladder_round_shas',{}).setdefault(current['id'],{})[str(i)]=current['sha']
            if current['sha']==state['pushes'][str(i)+'-before']:state.setdefault('ladder_shas',{})[str(i)]=current['sha']
        if i==1 and state['pushes'].get('1-after') in [r['sha'] for r in github['commits']]:state['late_observed']=True
        require(not any(d['state']=='needs_approval' for d in github['deadlines'] if d['competition_id']==cid),'Deadline fallback requires owner approval')
        if now>state['deadline_ms']:
            selected=next((d for d in github['deadlines'] if d['competition_id']==cid and d['state']=='locked'),None)
            if selected:
                require(selected['selected_sha']==state['pushes'][str(i)+'-before'],'Wrong SHA at deadline')
                state.setdefault('locked_shas',{})[str(i)]=selected['selected_sha']
    if view['status']!='completed':
        state['status']=view['status'];save(STATE,state)
        print('Demo',view['status'],'remaining seconds',max(0,(state['deadline_ms']-now)//1000),flush=True)
        return False
    schedule=api(admin,'GET',f'/competitions/{cid}/fixtures?limit=100')['items']
    official=[g for g in schedule if g.get('phase')=='round_robin']
    require(len(view['entries'])==4 and len(official)==12 and all(g['status']=='completed' for g in official),'Expected four teams / twelve completed official games')
    if not state.get('late_observed'):
        save(STATE,state);print('Official completed; waiting for late SHA intake',flush=True)
        return False
    expected_shas={str(i):state['pushes'][str(i)+'-before'] for i in range(1,5)}
    require(expected_shas in state.get('ladder_round_shas',{}).values() and state.get('late_observed'),'One whole ladder round with all four expected SHAs or late intake not verified')
    require(len(state.get('locked_shas',{}))==4,'Four deadline SHAs not verified')
    for stage in ('ladder','official'):
        exported=api(admin,'GET',f'/competitions/{cid}/elo/export?stage={stage}')
        actual=recompute(exported);require(exported['rounds'],'Missing '+stage+' rounds')
        if stage=='ladder':
            require(set(actual['ratings'])==set(state['teams'].values()),'Unexpected ladder participants')
            require(actual['ratings']==state.get('ladder_final_ratings'),'Recomputed ladder ratings mismatch')
            state['ladder_rounds']=len(exported['rounds'])
        else:
            standings=api(admin,'GET',f'/competitions/{cid}/standings')['items']
            require(actual['finalists']==view['config']['finalists'] and len(actual['finalists'])==2,'Finalists mismatch')
            for expected,got in zip(actual['standings'],standings):
                for key in ('team_id','points','wins','rating','rank'):require(expected[key]==got[key],'Recomputed standings mismatch: '+key)
            state['finalists']=actual['finalists']
        (OUTPUT/('e-'+stage+'-export.json')).write_text(json.dumps(exported,ensure_ascii=False,indent=2),encoding='utf-8')
    restore_other(admin,state)
    state.update(status='completed',other_restored=True,passed=True);save(STATE,state)
    (OUTPUT/'e-demo.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    return True


def restore_other(admin,state):
    cid=state['cid']
    view=api(admin,'GET','/competitions/'+cid)
    require(view['status']=='completed' and len(view['config'].get('finalists',[]))==2,'Restore only after official completion and two finalists')
    old=state['other_before'];team=api(admin,'GET',f"/admin/teams/{old['id']}")
    if team['archived_ms']:api(admin,'POST',f"/admin/teams/{old['id']}/restore",json={'expected_revision':team['revision']})
    for account in old['accounts']:api(admin,'POST',f"/admin/accounts/{account['id']}/lock",json={'locked':bool(account['locked'])})
    accounts={a['id']:a for a in api(admin,'GET','/admin/accounts?limit=100')['items']}
    require(all(bool(accounts[a['id']]['locked'])==bool(a['locked']) for a in old['accounts']),'Restored account lock state mismatch')
    smoke=json.loads((PRIVATE/'smoke-teams.json').read_text())
    with closing(login(HUB,'thu-m2-a',smoke['thu-m2-a'])) as client:
        require(api(client,'GET','/auth/me')['user']['team_id']==old['id'],'THU-M2 login mismatch')
        repo=api(client,'GET','/teams/me/github')['repo']
        require(repo['owner']+'/'+repo['name']==old['repo'],'THU-M2 repository changed')
    require(not api(admin,'GET',f"/admin/teams/{old['id']}")['archived_ms'],'THU-M2 remains archived')
    require(len(api(admin,'GET','/competitions/'+cid)['entries'])==4,'Demo changed after restore')
    state['other_restored']=True;save(STATE,state)


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    state=json.loads(STATE.read_text()) if STATE.exists() else {'pushes':{}}
    if state.get('passed'):
        print('Demo already verified:',state['cid']);return
    with closing(organizer(HUB)) as admin,ExitStack() as stack:
        try:
            if not state.get('setup_complete'):setup(admin,state)
            saved=json.loads(CREDENTIALS.read_text())
            clients={i:stack.enter_context(closing(login(HUB,f'demo-elo-{i}',saved[f'demo-elo-{i}']))) for i in range(1,5)}
            while not tick(admin,state,clients):time.sleep(30)
        finally:
            if state.get('cid') and state.get('other_archived') and not state.get('other_restored'):
                view=api(admin,'GET','/competitions/'+state['cid'])
                if view['status']=='completed' and len(view['config'].get('finalists',[]))==2:restore_other(admin,state)
    print('PASS demo Elo:',state['cid'],flush=True)


if __name__=='__main__':main()
