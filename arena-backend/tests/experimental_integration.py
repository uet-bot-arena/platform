"""Isolated HTTP + Docker experimental tournament, including classic compatibility."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import httpx
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import experimental, sandbox
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings

def main():
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    report=dict(started_ms=time.time_ns()//1000000,checks=[])
    def checked(name,**data):
        report['checks'].append(dict(name=name,passed=True,**data));print('PASS',name,flush=True)
    def execute(directory):
        store=Store(directory,recover_jobs=False);Accounts(store).bootstrap_admin('organizer','experimental-test-only-password');store.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        settings=Settings(data=Path(directory),origin=f'http://127.0.0.1:{sock.getsockname()[1]}')
        app=create_app(settings);server=uvicorn.Server(uvicorn.Config(app,access_log=False,log_level='warning',proxy_headers=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        admin,a,b,guest=[httpx.Client(base_url=settings.origin,timeout=90) for _ in range(4)]
        try:
            end=time.monotonic()+15
            while not server.started and thread.is_alive() and time.monotonic()<end:time.sleep(.05)
            assert server.started,'API did not start'
            def req(client,method,path,expected=200,**kw):
                if method=='POST':
                    kw['headers']={'Idempotency-Key':uuid.uuid4().hex,**kw.get('headers',{})}
                    if 'json' not in kw and 'content' not in kw:kw['json']={}
                r=client.request(method,'/api/v1'+path,**kw)
                assert r.status_code==expected,(path,r.status_code,r.text[:1500])
                return r
            def login(client,username,password):
                r=req(client,'POST','/auth/login',json=dict(username=username,password=password)).json();client.headers['X-CSRF-Token']=r['csrf_token']
            def wait_job(client,ident,success=True):
                end=time.monotonic()+240
                while time.monotonic()<end:
                    j=req(client,'GET','/jobs/'+ident).json()
                    if j['status'] in ('completed','failed','aborted'):
                        assert j['status']==('completed' if success else 'failed'),j
                        return j
                    time.sleep(.1)
                raise AssertionError('Worker job timed out')
            login(admin,'organizer','experimental-test-only-password')
            assert req(admin,'GET','/admin/rulesets').json()['enabled'] is False
            req(admin,'POST','/admin/experimental',json=dict(enabled=True,expected_revision=0))
            definition=deepcopy(experimental.PRESETS['domination'])
            ruleset=req(admin,'POST','/admin/rulesets',201,json={'definition':definition}).json()
            for verb in ('validate','publish'):
                ruleset=req(admin,'POST',f"/admin/rulesets/{ruleset['id']}/{verb}",json={'expected_revision':ruleset['revision']}).json()
            rid=ruleset['id'];report['ruleset_hash']=ruleset['hash'];report['image_id']=sandbox.image_id('uet-arena-worker:experimental-clock-v2')
            checked('default_off_admin_validate_publish_and_pin_hash')
            sources=[];benchmarks=[]
            for i,client in enumerate((a,b)):
                reg=req(client,'POST','/registrations',201,json=dict(team=f'Experimental Team {i+1}',email=f'experimental-{i}@example.test',members=[dict(name=f'Student {m}',student_id=f'2602000{m}') for m in range(3)],confirmed=True)).json()
                cred=req(admin,'POST',f"/admin/registrations/{reg['id']}/approve",201,json={'username':f'experimental-{i}'}).json()
                login(client,f'experimental-{i}',cred['temporary_password'])
                changed=req(client,'POST','/auth/password',json=dict(current_password=cred['temporary_password'],new_password='experimental-team-test-password')).json();client.headers['X-CSRF-Token']=changed['csrf_token']
                starter=req(client,'GET','/experimental/starter.zip').content
                source=req(client,'POST','/submissions',201,content=starter,headers={'Content-Type':'application/zip','X-Submission-Version':'v1.0.0','X-Arena-Protocol':'3'}).json();sources.append(source)
                queued=req(client,'POST','/benchmarks',202,json={'submission_id':source['id'],'ruleset_id':rid}).json()
                job=wait_job(client,queued['id']);benchmarks.append(job)
                assert job['report']['passed'] and len(job['report']['samples'])==72,job
                assert job['profile']['match_ms']==300000 and 0<job['report']['clocks']['red']<300000,job
                assert job['report']['ruleset']['hash']==ruleset['hash'] and job['report']['protocol_version']==3
                assert job['report']['artifacts'][0]['sdk_sha256']==experimental.sdk_hash()
                checked(f'team_{i+1}_real_cpp_build_v3_handshake_72_samples',p95_ms=job['report']['turn_wall_ms']['p95_ms'])
            # The expanded starter can still speak v2 to the classic engine.
            classic=req(a,'POST','/benchmarks',202,json={'submission_id':sources[0]['id']}).json();classic=wait_job(a,classic['id'])
            assert classic['report']['passed'] and 'ruleset' not in classic['report']
            checked('expanded_starter_also_passes_classic_v2_72_samples')
            comp=req(admin,'POST','/admin/competitions',201,json={'title':'Experimental two-leg cup','max_teams':2,'ruleset_id':rid}).json();cid=comp['id'];report['competition_id']=cid
            req(admin,'POST',f'/admin/competitions/{cid}/open')
            req(a,'POST',f'/competitions/{cid}/entry',409,json=dict(submission_id=sources[0]['id'],benchmark_id=classic['id'],delegate_timeout_claims=True))
            # Same definition/hash is deliberately not sufficient when changed.
            different=deepcopy(definition);different['name']='Different rules';different['energy']['initial']=1
            other=req(admin,'POST','/admin/rulesets',201,json={'definition':different}).json()
            for verb in ('validate','publish'):other=req(admin,'POST',f"/admin/rulesets/{other['id']}/{verb}",json={'expected_revision':other['revision']}).json()
            other_comp=req(admin,'POST','/admin/competitions',201,json=dict(title='Wrong ruleset gate',max_teams=2,ruleset_id=other['id'])).json()
            req(admin,'POST',f"/admin/competitions/{other_comp['id']}/open")
            req(a,'POST',f"/competitions/{other_comp['id']}/entry",409,json=dict(submission_id=sources[0]['id'],benchmark_id=benchmarks[0]['id'],delegate_timeout_claims=True))
            for client,source,job in zip((a,b),sources,benchmarks):req(client,'POST',f'/competitions/{cid}/entry',json=dict(submission_id=source['id'],benchmark_id=job['id'],delegate_timeout_claims=True))
            locked=req(admin,'POST',f'/admin/competitions/{cid}/lock').json();assert locked['fixture_count']==2
            checked('classic_and_cross_ruleset_qualification_rejected_two_leg_schedule_locked')
            req(admin,'POST','/admin/experimental',json=dict(enabled=False,expected_revision=1))
            req(a,'POST','/benchmarks',409,json=dict(submission_id=sources[0]['id'],ruleset_id=rid))
            req(admin,'POST',f"/admin/rulesets/{rid}/archive",json={'expected_revision':ruleset['revision']})
            req(admin,'POST',f'/admin/competitions/{cid}/start')
            end=time.monotonic()+240
            while time.monotonic()<end:
                view=req(guest,'GET',f'/competitions/{cid}').json()
                assert view['fixture_counts']['attention']==0,view
                if view['status']=='completed':break
                time.sleep(.1)
            assert view['status']=='completed',view
            fixtures=req(guest,'GET',f'/competitions/{cid}/fixtures').json()['items'];assert len(fixtures)==2
            events=set();plies=[]
            for fixture in fixtures:
                assert fixture['status']=='completed',fixture
                job=req(admin,'GET',f"/admin/competitions/{cid}/fixtures/{fixture['id']}/attempts/1").json()
                assert job['report']['ruleset']['hash']==ruleset['hash'] and job['report']['eligible_for_tournament']
                assert all(0<x<300000 for x in job['report']['clocks'].values())
                assert job['report']['result']['reason'] not in ('illegal_action','protocol_error','startup_failure','double_startup_failure','worker_error')
                for artifact in job['report']['artifacts']:
                    assert artifact['artifact_sha256'] in [j['report']['artifacts'][0]['artifact_sha256'] for j in benchmarks]
                replay=req(guest,'GET',f"/competitions/{cid}/fixtures/{fixture['id']}/events").json()
                states=[e for e in replay['items'] if e['type']=='state']
                assert states[0]['data']['state']==experimental.Rules(definition).initial_state()
                assert states[-1]['data']['state']==job['report']['final_state']
                assert all(s['data']['ruleset']['hash']==ruleset['hash'] for s in states)
                events.update(t['type'] for s in states for t in s['data']['events']);plies.append(states[-1]['data']['state']['ply'])
                path=f"/competitions/{cid}/fixtures/{fixture['id']}/events"
                wire=req(guest,'GET',path,headers={'Accept':'text/event-stream'}).text
                assert 'event: arena' in wire and '"energy"' in wire and '"ruleset"' in wire
                assert 'source_path' not in wire and 'artifact_sha256' not in wire
                cursor=replay['items'][len(replay['items'])//2]['seq']
                resumed=req(guest,'GET',path,headers={'Accept':'text/event-stream','Last-Event-ID':job['id']+':'+str(cursor)}).text
                assert f"id: {job['id']}:{cursor}\n" not in resumed
            checked('disabled_archived_ruleset_keeps_locked_matches_running',plies=plies)
            assert {'move','energy_gained','control_gained'}.issubset(events),events
            checked('guest_sse_replay_resources_semantic_events_and_reconnect',event_types=sorted(events))
            scores=req(guest,'GET',f'/competitions/{cid}/standings').json()
            assert not scores['provisional'] and all(s['played']==2 and s['points']==3*s['wins']+s['draws'] for s in scores['items'])
            checked('official_results_points_and_standings_no_double_count')
            # Exercise power-up traces and rejection through the actual worker.
            req(admin,'POST','/admin/experimental',json=dict(enabled=True,expected_revision=2))
            code=(ROOT/'sdk/experimental-bot.cpp').read_text()
            def upload_variant(version,source):
                out=io.BytesIO()
                with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('bot.cpp',source)
                return req(a,'POST','/submissions',201,content=out.getvalue(),headers={'Content-Type':'application/zip','X-Submission-Version':version,'X-Arena-Protocol':'3'}).json()
            # Archived rid is unavailable for NEW practice, so publish a clone.
            clone=req(admin,'POST',f'/admin/rulesets/{rid}/clone',json={'expected_revision':ruleset['revision']+1}).json()
            for verb in ('validate','publish'):clone=req(admin,'POST',f"/admin/rulesets/{clone['id']}/{verb}",json={'expected_revision':clone['revision']}).json()
            current=clone['id']
            skill_code=code.replace('     std::cout<<json(','''     if(version==3&&int(payload.at("state").at("ply"))<2){
       for(const auto& a:payload.at("legal_actions"))if((payload.at("state").at("to_move")=="red"&&a.value("ability",std::string())=="dash")||(payload.at("state").at("to_move")=="blue"&&a.at("type")=="shield")){action={{"action_id",a.at("id")}};break;}
     }
     std::cout<<json(''')
            skill=upload_variant('v1.0.1',skill_code)
            practice=req(a,'POST','/matches',202,json=dict(red_submission_id=skill['id'],blue_submission_id=skill['id'],ruleset_id=current,delegate_timeout_claims=True)).json();practice=wait_job(a,practice['id'])
            replay=req(a,'GET',f"/jobs/{practice['id']}/events").json()
            items=replay['items'] if isinstance(replay,dict) else replay
            traces=[t for e in items if e['type']=='state' for t in e['data']['events']]
            assert any(t['type']=='move' and t['ability']=='dash' for t in traces)
            assert any(t['type']=='ability_used' and t['ability']=='shield' for t in traces)
            report['skill_practice_id']=practice['id']
            checked('real_dash_and_shield_actions_in_private_practice_replay')
            for version,source in [('v1.0.2',code.replace('ready["capabilities"]=json::array({"rules-v1"})','ready["capabilities"]=json::array()')),
                                   ('v1.0.3',code.replace('{"payload",action}','{"payload",json({{"action_id","expired"}})}'))]:
                bad=upload_variant(version,source)
                job=req(a,'POST','/benchmarks',202,json=dict(submission_id=bad['id'],ruleset_id=current)).json();job=wait_job(a,job['id'],False)
                assert job['error']['code']==('protocol_error' if version=='v1.0.2' else 'illegal_action'),job
            checked('real_worker_rejects_missing_capability_and_invalid_action_id')
            req(admin,'POST','/admin/experimental',json=dict(enabled=False,expected_revision=3))
            report['fixtures']=fixtures;report['standings']=scores;report['passed']=True
        finally:
            for client in (admin,a,b,guest):client.close()
            server.should_exit=True;thread.join(timeout=360);sock.close()
            assert not thread.is_alive(),'Controller failed to release isolated DB'
        # Catalog and complete snapshots survive opening the persisted database.
        persisted=Store(directory,recover_jobs=False)
        assert len(persisted.list('ruleset'))==3
        assert persisted.list('experimental_settings')[0]['enabled'] is False
        persisted.db.close();checked('catalog_settings_and_snapshot_persistence')
    proof=os.environ.get('ARENA_EXPERIMENTAL_PROOF_DATA')
    if proof:
        directory=(output/proof).resolve();assert directory.parent==output.resolve() and not directory.exists()
        directory.mkdir();execute(directory)
    else:
        with tempfile.TemporaryDirectory(prefix='experimental-integration-',dir=output) as directory:execute(directory)
    report['completed_ms']=time.time_ns()//1000000
    (output/'experimental-integration-latest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('ALL EXPERIMENTAL INTEGRATION CHECKS PASSED',flush=True)

if __name__=='__main__':main()
