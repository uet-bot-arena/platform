"""C1 functional checks against the installed disposable hub, never a load run."""
import gzip
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arena import engine
from arena.config import match_profile
from arena.store import Store
from arena_api.accounts import Accounts

DATA=Path('/var/lib/uet-arena/data')
BASE='http://127.0.0.1:4181'
checks=[]


def get(path,headers=None):
    try:
        with urlopen(Request(BASE+path,headers=headers or {}),timeout=5) as response:
            return response.status,response.headers,response.read()
    except HTTPError as response:return response.code,response.headers,response.read()


def check(condition,name):
    assert condition,name
    checks.append(name)


def seed_stream():
    store=Store(DATA,recover_jobs=False);Accounts(store)
    db=store.db
    cid,fixture=uuid.uuid4().hex,uuid.uuid4().hex
    config=dict(format='round_robin',profile=match_profile(10000).public(),max_teams=2)
    entries=[]
    with store.transaction():
        for _ in range(2):
            team=cid+'-'+str(len(entries))
            db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,'fixture@vnu.test','','[]',1)",(team,team,team))
            source=store.create('submission',team,dict(version='nginx-test',runtime='cpp17'))
            check=store.create('job',team,dict(kind='benchmark',status='completed',report=None))
            entries.append((team,source['id'],check['id'],json.dumps(dict(version='nginx-test')),1))
    job=store.create('job','competition:'+cid,dict(kind='match',status='playing',submission_ids=[],profile=config['profile'],report=None))
    with store.transaction():
        db.execute("INSERT INTO competitions(id,title,status,config,created_ms) VALUES(?,'nginx SSE fixture','paused',?,?)",(cid,json.dumps(config),time.time_ns()//1_000_000))
        for entry in entries:db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(cid,*entry))
        db.execute("INSERT INTO competition_fixtures(id,competition_id,ordinal,red_team_id,blue_team_id,status,current_job_id) VALUES(?,?,1,?,?,'queued',?)",(fixture,cid,entries[0][0],entries[1][0],job['id']))
    store.event(job['id'],'state',dict(state=engine.initial_state()))
    store.event(job['id'],'progress',dict(private_marker='must-not-be-public'))
    store.event(job['id'],'state',dict(state=engine.initial_state()))
    store.db.close()
    return cid,fixture,job['id']


def first_frame(path,headers):
    started=time.monotonic()
    with urlopen(Request(BASE+path,headers=headers),timeout=5) as response:
        buffer=[]
        while line:=response.readline().decode():
            buffer.append(line)
            if line.startswith('data: ') and '"seq":' in line:
                return ''.join(buffer),time.monotonic()-started
    raise AssertionError('No state frame')


def main():
    cid,fixture,job=seed_stream()
    try:
        path=f'/api/v1/competitions/{cid}/fixtures/{fixture}/events'
        first,delay=first_frame(path,{'Accept':'text/event-stream'})
        check(f'id: {job}:1' in first,'SSE emits first state through nginx without buffering')
        resumed,_=first_frame(path,{'Accept':'text/event-stream','Last-Event-ID':job+':1'})
        check(f'id: {job}:3' in resumed and f'id: {job}:1' not in resumed,'Last-Event-ID resumes after hidden frame')
        check('must-not-be-public' not in first+resumed,'Public SSE keeps private event filter')
    finally:
        with sqlite3.connect(DATA/'arena.sqlite') as db:
            db.execute('DELETE FROM competition_fixtures WHERE id=?',(fixture,))
            db.execute('DELETE FROM competition_entries WHERE competition_id=?',(cid,))
            db.execute('DELETE FROM competitions WHERE id=?',(cid,))
            db.execute('DELETE FROM events WHERE job=?',(job,))
            db.execute('DELETE FROM documents WHERE id=?',(job,))
            db.execute('DELETE FROM documents WHERE team IN (?,?)',(cid+'-0',cid+'-1'))
            db.execute('DELETE FROM teams WHERE id IN (?,?)',(cid+'-0',cid+'-1'))
    status,headers,raw=get('/snapshots/competitions.json',{'Accept-Encoding':'gzip'})
    check(status==200 and headers.get('Content-Encoding')=='gzip','Snapshot served as gzip')
    check(json.loads(gzip.decompress(raw)).get('items') is not None,'Compressed snapshot JSON is valid')
    check(headers.get('Cache-Control')=='public, max-age=1, s-maxage=5','Public snapshot cache policy retained')
    check(headers.get('Vary')=='Accept-Encoding' and bool(headers.get('ETag')),'ETag and Vary present')
    check(get('/snapshots/competitions.json',{'Accept-Encoding':'gzip','If-None-Match':headers['ETag']})[0]==304,'Conditional snapshot GET returns 304')
    check(get('/snapshots/competitions.json',{'Accept-Encoding':'identity'})[2]==gzip.decompress(raw),'Non-gzip client gets identical JSON')
    for hidden in ('.cursor','../arena.sqlite','competitions.json.gz','unknown.json'):
        check(get('/snapshots/'+hidden)[0]==404,'Forbidden snapshot path: '+hidden)
    for name in ('uet-arena-hub','uet-arena-publisher','uet-arena-mailer','uet-arena-nginx'):
        slice_name=subprocess.check_output(['systemctl','show',name,'-p','Slice','--value'],text=True).strip()
        check(slice_name=='arena.slice','Shared slice: '+name)
    subprocess.run(['systemctl','stop','uet-arena-publisher','uet-arena-mailer','uet-arena-hub'],check=True)
    try:
        check(get('/')[0]==200 and b'/_ui/app.js' in get('/')[2],'HTML served with hub stopped')
        check(get('/_ui/app.js')[0]==200,'UI asset served with hub stopped')
        check(get('/snapshots/competitions.json')[0]==200,'Snapshot served with hub stopped')
    finally:subprocess.run(['systemctl','start','uet-arena-hub','uet-arena-publisher','uet-arena-mailer'],check=True)
    result=dict(checks=checks,sse_first_frame_seconds=delay)
    target=Path('/root/g10b-nginx-check.json');target.write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(passed=len(checks),sse_first_frame_seconds=delay)))


if __name__=='__main__':main()
