"""Real Docker C/C++/CPython cross-play and language fault probes."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import engine,sandbox
from arena.adjudication import CURRENT
from arena.config import request_profile
from arena.protocol import Fault
from arena.service import Service
from arena.store import Store

def zipped(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,source in files.items():z.writestr(name,source)
    return out.getvalue()

def main():
    rows=[];p=request_profile({'match_ms':60000});image=sandbox.image_id('uet-arena-worker:clock-v2')
    def passed(name,**data):rows.append({'test':name,'passed':True,**data});print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory(prefix='arena-languages-') as tmp:
        root=Path(tmp);sandbox.controller_scope(root,min(os.sched_getaffinity(0)))
        store=Store(root);service=Service(store,start_worker=False)
        names={'cpp17':('bot.cpp',),'c11':('bot.c','jsmn.h'),'python3':('bot.py',)}
        sources={runtime:{name:(ROOT/'sdk'/name).read_text() for name in files} for runtime,files in names.items()}
        submissions={runtime:service.submit('test-team','v1.0.'+str(i),runtime,zipped(files),'language-source-'+runtime) for i,(runtime,files) in enumerate(sources.items())}
        for runtime,submission in submissions.items():
            job=service.enqueue('test-team','benchmark',{'submission_id':submission['id'],'match_ms':60000},'language-benchmark-'+runtime)
            service.pending.remove(job['id']);service.execute(job)
            report=store.get(job['id'])['report'];assert report['passed'] and len(report['samples'])==72
            passed(runtime+'-72-samples',startup=report['startup'],compiler=report['artifacts'][0]['compiler'])
        for red,blue in (('cpp17','python3'),('python3','cpp17'),('c11','python3'),('python3','c11'),('c11','cpp17'),('cpp17','c11')):
            job=service.enqueue('test-team','match',{'red_submission_id':submissions[red]['id'],'blue_submission_id':submissions[blue]['id'],'match_ms':60000,'delegate_timeout_claims':True},'cross-'+red+'-'+blue)
            service.pending.remove(job['id']);service.execute(job);report=store.get(job['id'])['report']
            assert report.get('result') and not report.get('fault'),report
            passed(red+'-vs-'+blue,result=report['result'],plies=report['final_state']['ply'])
        for runtime in ('c11','python3'):
            for mode,expected in (('crash','crash'),('timeout','timeout'),('spam','output_limit'),('stale','stale_action'),('illegal','illegal_action'),('memhog','crash'),('slow_start','timeout'),('sneaky','denied')):
                source=dict(sources[runtime]);entry='bot.py' if runtime=='python3' else 'bot.c'
                if runtime=='python3':
                    base=source[entry]
                    if mode=='slow_start':base='import time\ntime.sleep(5)\n'+base
                    else:
                        action={
                            'crash':'raise SystemExit(7)',
                            'timeout':'while True: pass',
                            'spam':'print("x"*70000,flush=True)',
                            'stale':'message["turn_id"]="stale"',
                            'illegal':'result["payload"]={"from":"a1","to":"o15"}',
                            'memhog':'a=bytearray(512*1024*1024)',
                            'sneaky':"import os, socket\n            denied=0\n            try: os.fork()\n            except PermissionError: denied+=1\n            try: socket.socket()\n            except PermissionError: denied+=1\n            assert denied==2\n            print('sandbox-denied',file=sys.stderr,flush=True)"
                        }[mode]
                        if mode=='stale':base=base.replace('board,side=configure', 'board,side=configure') # no-op: starter uses payload directly
                        marker='        print(json.dumps(result, separators=(",", ":")), flush=True)'
                        injected='        if message["type"]=="observation":\n            '+action.replace('\n','\n')+'\n'
                        if mode=='stale':injected='        if message["type"]=="observation": result["turn_id"]="stale"\n'
                        assert marker in base;base=base.replace(marker,injected+marker)
                    source[entry]=base
                else:
                    base=original=source[entry]
                    prefix='#include <unistd.h>\n#include <sys/socket.h>\n#include <errno.h>\n'
                    # Anchors of sdk/bot.c; each fault must actually change the starter.
                    observation='      int turn = get(0, "turn_id"), state_version = get(0, "state_version"), count = 0;'
                    action='printf(",\\"payload\\":{\\"from\\":\\"%s\\",\\"to\\":\\"%s\\"}}", moves[k].from, moves[k].to);'
                    if mode=='slow_start':base=base.replace('int main(void) {','int main(void) {\n  sleep(5);',1)
                    elif mode=='stale':base=base.replace('raw(turn);','printf("\\"stale\\"");',1)
                    elif mode=='illegal':base=base.replace(action,'printf(",\\"payload\\":{\\"from\\":\\"a1\\",\\"to\\":\\"o15\\"}}");',1)
                    else:
                        code={'crash':'exit(7);','timeout':'for(;;){}','spam':'for(int i=0;i<70000;i++)putchar(120);putchar(10);fflush(stdout);',
                              'memhog':'volatile char *p=malloc(512u*1024u*1024u);if(!p)exit(7);for(size_t i=0;i<512u*1024u*1024u;i+=4096)p[i]=1;',
                              'sneaky':'if(fork()!=-1||errno!=EPERM)exit(8);if(socket(AF_INET,SOCK_STREAM,0)!=-1||errno!=EPERM)exit(8);fprintf(stderr,"sandbox-denied\\n");'}[mode]
                        base=base.replace(observation,'      '+code+'\n'+observation,1)
                    assert base!=original,('fault not injected into sdk/bot.c',mode)
                    source[entry]=prefix+base
                directory=root/(runtime+'-'+mode);directory.mkdir();src=directory/'source';src.mkdir()
                for name,text in source.items():(src/name).write_text(text)
                built=sandbox.build(src,directory/'artifact',p,image,runtime)
                assert built['ok'],(runtime,mode,built)
                worker=sandbox.Worker(directory/'artifact',replace(p,turn_ms=150,startup_ms=150 if mode=='slow_start' else 2000,match_ms=None),image)
                actual=None
                try:
                    worker.start('fault-probe','red')
                    result=worker.turn({'protocol_version':2,'type':'observation','match_id':'fault-probe','turn_id':'1','state_version':0,'payload':engine.observation(engine.initial_state())})
                    try:engine.apply(engine.initial_state(),result['message']['payload'])
                    except ValueError:actual='illegal_action'
                    if mode=='sneaky':actual='denied'
                except Fault as exc:actual=exc.code
                finally:metrics=worker.close()
                assert actual==expected,(runtime,mode,actual,metrics)
                if mode=='sneaky':assert 'sandbox-denied' in metrics['stderr']
                passed(runtime+'-'+mode,code=actual)
        store.db.close()
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    (output/'languages-integration.json').write_text(json.dumps({'image_id':image,'checks':rows},indent=2))

if __name__=='__main__':main()
