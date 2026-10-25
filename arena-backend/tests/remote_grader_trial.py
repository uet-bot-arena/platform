"""3c: installed Windows grader trial, interactive remote or --self-test from WSL.

Secrets are prompted or placed in private installer files, never in result JSON.
"""
import argparse
import getpass
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import zipfile

import httpx
from grader_cluster import Cluster, ROOT, slow_bot, windows_path
sys.path.insert(0,str(ROOT))


def patient_bot():
    # A 180-second first move leaves >=90s even after the existing 60s PC startup wait.
    with zipfile.ZipFile(io.BytesIO(slow_bot())) as source:
        code=source.read('bot.cpp').replace(
            b'if (slow) std::this_thread::sleep_for(std::chrono::milliseconds(400));',
            b'if (slow) { static bool first_slow = true; std::this_thread::sleep_for(std::chrono::milliseconds(first_slow ? 180000 : 400)); first_slow = false; }')
    assert b'first_slow ? 180000 : 400' in code
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as target:target.writestr('bot.cpp',code)
    return out.getvalue()


class Trial(Cluster):
    def __init__(self,args):
        super().__init__()
        self.results['mode']='remote-grader'
        self.args=args;self.clients=[];self.teams=[];self.comps=[];self.issued=[]
        self.profiles={};self.suspended=None
        self.run_id=uuid.uuid4().hex[:7]
        self.laptop='THU-3C '+args.name;self.pc='THU-3C pc-1 '+self.run_id
        if not args.self_test:
            self.origin=args.hub.rstrip('/');self.admin.close()
            self.admin=httpx.Client(base_url=self.origin,headers={'Origin':self.origin,'User-Agent':'uet-arena-trial/1'},timeout=60)
            session=self.api(self.admin,'POST','/auth/login',json={'username':args.admin,'password':getpass.getpass('Mat khau BTC: ')})
            self.admin.headers['X-CSRF-Token']=session['csrf_token']

    def api(self,client,method,path,expected=200,**kwargs):
        # Cluster's assertions include response bodies; keep credential responses private.
        if method=='POST':
            kwargs.setdefault('headers',{}).setdefault('Idempotency-Key',uuid.uuid4().hex)
            if 'json' not in kwargs and 'content' not in kwargs:kwargs['json']={}
        response=client.request(method,'/api/v1'+path,**kwargs)
        assert response.status_code==expected,(method,path,response.status_code)
        return response.json()

    def until(self,predicate,seconds=None):
        end=time.monotonic()+seconds if seconds is not None else float('inf')
        while time.monotonic()<end:
            value=predicate()
            if value:return value
            time.sleep(.5)
        raise AssertionError('Timed out waiting for trial condition')

    def issue(self,name):
        item=self.api(self.admin,'POST','/admin/graders',201,json={'name':name})
        self.issued.append(item['id'])
        return item

    def install(self,name,profile):
        local=subprocess.check_output(['cmd.exe','/c','echo %LOCALAPPDATA%'],cwd='/mnt/c',text=True).strip()
        base=Path(subprocess.check_output(['wslpath','-u',local],text=True).strip())/'uet-arena'/('grader-install-'+profile)
        assert not base.exists(),'Trial profile already exists; inspect it before retrying'
        token=self.issue(name);base.mkdir(parents=True)
        (base/'grader-token').write_text(token['token']);(base/'grader-token').chmod(0o600)
        ps=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',windows_path(ROOT.parent/'scripts/install-grader.ps1'),'-Profile',profile]
        self.profiles[name]={'base':base,'ps':ps}
        result=subprocess.run(ps+['-HubUrl',self.origin,'-Slots','2'],capture_output=True,text=True,timeout=240,cwd='/mnt/c')
        if result.returncode:
            self.results['installer_error']=(result.stdout+result.stderr).replace(token['token'],'[redacted]')[-3000:]
            raise AssertionError('Installer failed; inspect private grader.log at '+str(base/'grader.log'))
        self.wait_online([name])

    def stop_installed(self,name,uninstall=False):
        item=self.profiles[name]
        result=subprocess.run(item['ps']+['-Uninstall' if uninstall else '-Stop'],capture_output=True,timeout=60,cwd='/mnt/c')
        assert result.returncode==0,'Stop/uninstall failed: '+str(item['base']/'grader.log')

    def restart(self,name,timeout=60):
        item=self.profiles[name];(item['base']/'stop').unlink(missing_ok=True)
        startup=subprocess.check_output(['powershell.exe','-NoProfile','-Command',"[Environment]::GetFolderPath('Startup')"],text=True).strip()
        profile=item['base'].name.removeprefix('grader-install-')
        link=startup+'\\UET Arena grader '+profile+'.lnk'
        subprocess.run(['powershell.exe','-NoProfile','-Command',"Start-Process -FilePath '"+link+"' -WindowStyle Hidden"],capture_output=True,check=True)
        self.wait_online([name],timeout=timeout)

    def pid(self,name):return int((self.profiles[name]['base']/'grader.pid').read_text())

    def hard_kill(self,name):
        subprocess.run(['taskkill.exe','/F','/PID',str(self.pid(name))],capture_output=True,check=True,cwd='/mnt/c')

    def suspend(self,resume=False):
        pid=self.suspended if resume else self.pid(self.laptop)
        action='NtResumeProcess' if resume else 'NtSuspendProcess'
        code=("import ctypes; k=ctypes.WinDLL('kernel32'); k.OpenProcess.restype=ctypes.c_void_p; "
              "h=k.OpenProcess(0x0800,False,"+str(pid)+"); assert h; n=ctypes.WinDLL('ntdll'); "
              "f=n."+action+"; f.argtypes=[ctypes.c_void_p]; assert f(h)==0; k.CloseHandle.argtypes=[ctypes.c_void_p]; k.CloseHandle(h)")
        subprocess.run(['python.exe','-c',code],capture_output=True,check=True,cwd='/mnt/c')
        self.suspended=None if resume else pid

    def check_job(self,client,submission,cid=None):
        body={'submission_id':submission}
        if cid:body['competition_id']=cid
        job=self.api(client,'POST','/benchmarks',202,json=body)
        done=self.wait_job(client,job['id'])
        report=done.get('report') or done.get('check_summary') or {}
        samples=report.get('samples',0)
        count=len(samples) if isinstance(samples,list) else samples
        self.check('72 samples on laptop',done['status']=='completed' and done.get('worker')==self.laptop and report.get('passed') is True and count==72,job=done['id'],worker=done.get('worker'),samples=count)
        return done['id']

    def fixtures(self,stage):
        comp=self.api(self.admin,'POST','/admin/competitions',201,json={'title':'THU-3C '+stage+' '+self.run_id,'max_teams':2,'grader_platform':'windows','match_ms':300000})
        self.comps.append(comp['id']);self.api(self.admin,'POST',f"/admin/competitions/{comp['id']}/open")
        for client,submission in self.clients:
            bench=self.check_job(client,submission,comp['id'])
            self.api(client,'POST',f"/competitions/{comp['id']}/entry",json={'submission_id':submission,'benchmark_id':bench,'delegate_timeout_claims':True})
        self.api(self.admin,'POST',f"/admin/competitions/{comp['id']}/lock")
        self.api(self.admin,'POST',f"/admin/competitions/{comp['id']}/start")
        def playing():
            return next((j for j in self.official_jobs() if j['official']['competition_id']==comp['id'] and j['status']=='playing' and j.get('worker')==self.laptop),None)
        job=self.until(playing,120)
        return comp['id'],job

    def lost(self,stage):
        cid,job=self.fixtures(stage)
        if self.pc not in self.profiles:self.install(self.pc,'remote-pc-sim')
        else:self.restart(self.pc)
        if self.args.self_test:
            expires=self.lease_row(job['id'])['expires_ms']
            self.until(lambda:self.lease_row(job['id'])['expires_ms']>expires,10)
            self.suspend() if stage=='B' else self.hard_kill(self.laptop)
        else:
            input(('Tat Wi-Fi laptop ngay' if stage=='B' else 'Tat cung: Stop-Process -Id (Get-Content "$env:LOCALAPPDATA\\uet-arena\\grader-install-default\\grader.pid") -Force')+'; nhan Enter: ')
        killed={'mono':time.monotonic(),'unix_ms':time.time_ns()//1_000_000}
        old=self.until(lambda: (r if (r:=self.api(self.admin,'GET','/admin/jobs/'+job['id']))['status']=='aborted' else None),60)
        self.check(stage+' lease expired',old.get('error',{}).get('code')=='worker_lost' and 'lease_expired' in old.get('error',{}).get('detail',''))
        expired=time.monotonic()
        if self.args.self_test:
            row=self.lease_row(job['id'])
            self.results.setdefault('lease_samples',[]).append({'id':row['id'],'status':row['status'],'seconds_after_kill':expired-killed['mono']})
            self.check_expired_lease(row,killed)
        def retried():
            return next((j for j in self.official_jobs() if j['official']['fixture_id']==job['official']['fixture_id'] and j['id']!=job['id'] and j.get('worker')==self.pc and j['status'] in ('playing','completed')),None)
        retry=self.until(retried,60)
        self.check(stage+' retry within 60s',time.monotonic()-expired<=60,job=retry['id'])
        if stage=='B':
            if self.args.self_test:
                remaining=60-(time.monotonic()-killed['mono'])
                if remaining>0:time.sleep(remaining)
                self.suspend(resume=True)
            else:input('Bat Wi-Fi lai; nhan Enter: ')
            self.until(lambda:self.listing().get(self.laptop,{}).get('online'),120)
        view=self.until(lambda: (v if (v:=self.api(self.admin,'GET','/competitions/'+cid))['status']=='completed' else None),900)
        standings=self.api(self.admin,'GET',f'/competitions/{cid}/standings')['items']
        attempts=self.api(self.admin,'GET',f"/admin/competitions/{cid}/fixtures/{job['official']['fixture_id']}/attempts")['items']
        old=self.api(self.admin,'GET','/admin/jobs/'+job['id'])
        self.check(stage+' score once; late result ignored',sum(r['played'] for r in standings)==4 and all(r['played']==2 for r in standings) and attempts[0]['error_code']=='worker_lost' and attempts[-1]['status']=='completed' and old['status']=='aborted' and old.get('report') is None)
        self.stop_installed(self.pc)

    def run_trial(self):
        started=time.monotonic()
        if self.args.self_test:
            self.start_hub();self.install(self.laptop,'remote-sim')
        else:
            token=self.issue(self.laptop)
            print('MA MAY CHAM (khong ghi log):',token['token'],flush=True)
            print('powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\install-grader.ps1 -HubUrl '+self.origin+' -Slots 2',flush=True)
            self.until(lambda:self.listing().get(self.laptop,{}).get('online'))
        item=self.listing()[self.laptop]
        runtime=subprocess.check_output(['python.exe','-m','arena.sandbox.toolchain','id'],cwd=ROOT,text=True).strip()
        from arena.grader import VERSION
        self.check('A identity',item['platform']=='windows' and item['runtime_id']==runtime and item['version']==VERSION,runtime=runtime,version=item['version'],online_seconds=time.monotonic()-started)
        for n in (1,2):
            client=self.team('THU-3C '+str(n)+' '+self.run_id,'thu3c-'+self.run_id+'-'+str(n))
            self.teams.append(self.api(client,'GET','/auth/me')['user']['team_id'])
            submission=self.api(self.admin,'POST',f'/admin/teams/{self.teams[-1]}/submissions',201,content=patient_bot(),headers={'Content-Type':'application/zip','X-Submission-Version':'v1.0.0'})
            self.clients.append((client,submission['id']))
            self.check_job(client,submission['id'])
        self.install(self.pc,'remote-pc-sim');self.stop_installed(self.pc)
        self.lost('B');self.lost('C')
        if self.args.self_test:self.restart(self.laptop,timeout=300)
        else:input('Dang xuat Windows roi dang nhap lai; nhan Enter: ')
        self.until(lambda:self.listing().get(self.laptop,{}).get('online'),300)
        self.check_job(*self.clients[0]);self.check('D Startup online',True)
        if self.args.self_test:self.stop_installed(self.laptop)
        else:input('Chay install-grader.ps1 -Stop; nhan Enter: ')
        self.until(lambda:not self.listing()[self.laptop]['online'],40)
        for ident in self.issued:self.api(self.admin,'POST',f'/admin/graders/{ident}/revoke')
        if self.args.self_test:
            self.stop_installed(self.laptop,True)
            self.check('E uninstalled',not self.profiles[self.laptop]['base'].exists())
        else:input('Chay install-grader.ps1 -Uninstall; nhan Enter: ')
        self.check('A-E complete',True)

    def finish(self):
        if self.suspended:self.suspend(resume=True)
        # Archive accepts completed cups only; drain our cups before removing graders.
        for ident in self.comps:
            try:
                view=self.api(self.admin,'GET','/competitions/'+ident)
                if view['status'] in ('running','locked','paused'):
                    if self.pc not in self.profiles:self.install(self.pc,'remote-pc-sim')
                    elif not (self.profiles[self.pc]['base']/'grader.pid').exists():self.restart(self.pc)
                    if view['status']!='running':self.api(self.admin,'POST',f'/admin/competitions/{ident}/start')
                    self.until(lambda:self.api(self.admin,'GET','/competitions/'+ident)['status']=='completed',900)
                elif view['status']=='open':
                    for team in self.teams:
                        self.api(self.admin,'POST',f'/admin/competitions/{ident}/entries/{team}/remove')
            except Exception:self.results.setdefault('cleanup_errors',[]).append('Drain '+ident)
        output=ROOT/'test-results/m1/remote-grader';output.mkdir(parents=True,exist_ok=True)
        for name,item in self.profiles.items():
            log=item['base']/'grader.log'
            if log.exists():
                target=output/(self.run_id+'-'+item['base'].name+'.log')
                target.write_bytes(log.read_bytes())
                self.results.setdefault('grader_logs',[]).append(str(target.relative_to(ROOT)))
            try:self.stop_installed(name,True)
            except Exception:self.results.setdefault('cleanup_errors',[]).append('Profile '+name)
        for ident in self.comps:
            try:
                view=self.api(self.admin,'GET','/competitions/'+ident)
                self.api(self.admin,'POST',f'/admin/competitions/{ident}/archive',json={'expected_revision':view['revision']})
            except Exception:self.results.setdefault('cleanup_errors',[]).append('Competition '+ident)
        for ident in self.teams:
            try:
                view=self.api(self.admin,'GET','/admin/teams/'+ident)
                self.api(self.admin,'POST',f'/admin/teams/{ident}/archive',json={'expected_revision':view['revision']})
            except Exception:self.results.setdefault('cleanup_errors',[]).append('Team '+ident)
        for ident in self.issued:
            try:self.api(self.admin,'POST',f'/admin/graders/{ident}/revoke')
            except Exception:pass
        output=ROOT/'test-results/m1/remote-grader';output.mkdir(parents=True,exist_ok=True)
        target=output/(time.strftime('%Y%m%d-%H%M%S')+'.json')
        target.write_text(json.dumps(self.results,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Results:',target,flush=True)
        for client,_ in self.clients:client.close()
        self.admin.close();super().stop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--hub');parser.add_argument('--admin',default='organizer');parser.add_argument('--name',default='laptop-trial')
    args=parser.parse_args()
    if not args.self_test and not args.hub:parser.error('--hub is required')
    trial=Trial(args)
    try:trial.run_trial()
    except BaseException as exc:
        trial.results['failed']=type(exc).__name__
        print('FAIL. Xin grader.log: %LOCALAPPDATA%\\uet-arena\\grader-install-default\\grader.log',flush=True)
        raise
    finally:trial.finish()


if __name__=='__main__':main()
