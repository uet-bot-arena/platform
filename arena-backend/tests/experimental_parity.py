"""Differential test: compile trusted C++ probe in Docker; compare against Python."""
from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import engine, experimental, sandbox
from arena.config import profile

PROBE=r'''
#include <iostream>
#include "experimental.hpp"
int main(){std::string line;while(std::getline(std::cin,line)){
 try{auto m=nlohmann::json::parse(line);arena_experimental::Rules r(m.at("definition"));
 std::cout<<nlohmann::json({{"legal",r.legal_actions(m.at("state"))},{"next",r.apply(m.at("state"),m.at("action"))}}).dump()<<'\n';}
 catch(const std::exception& e){std::cerr<<e.what();return 1;}
}}
'''

def cases():
    rng=random.Random(20261012);configs=list(deepcopy(experimental.PRESETS).values())
    for pattern in ('orthogonal','diagonal','king','knight'):
        d=deepcopy(experimental.PRESETS['domination']);d['name']='Parity '+pattern
        d['modifiers']=[dict(id='custom',filter={'kinds':['rock','paper','scissors']},pattern=pattern,range=1 if pattern in ('king','knight') else 3,cost=0,cooldown=2)]
        configs.append(d)
    d=deepcopy(experimental.PRESETS['domination']);d['modifiers'][0]['filter']['piece_ids']=['red-a2','blue-i8'];configs.append(d)
    out=[]
    for d in configs:
        experimental.validate(d);r=experimental.Rules(d);s=r.initial_state()
        for _ in range(100):
            legal=r.legal_actions(s)
            if not legal:s=r.initial_state();legal=r.legal_actions(s)
            a={'action_id':rng.choice(legal)['id']}
            out.append((dict(definition=d,state=s,action=a),dict(legal=legal,next=r.apply(s,a))))
            s=r.apply(s,a)
        # Force capture of an active shield and check the attacker's cooldown.
        s=r.initial_state();s['board']=[[None]*9 for _ in range(9)]
        s['board'][5][3]=dict(side='red',kind='scissors',id='red-a2',shield_until=0,cooldowns={})
        s['board'][4][4]=dict(side='blue',kind='paper',id='blue-d9',shield_until=3,cooldowns={})
        s['energy']=dict(red=5,blue=5)
        a=next(a for a in r.legal_actions(s) if a.get('to')=='e5')
        action={'action_id':a['id']}
        out.append((dict(definition=d,state=s,action=action),dict(legal=r.legal_actions(s),next=r.apply(s,action))))
    return out

def main():
    image=sandbox.image_id('uet-arena-worker:experimental-v1');output=ROOT/'test-results';output.mkdir(exist_ok=True)
    inputs=cases()
    with tempfile.TemporaryDirectory(prefix='experimental-parity-',dir=output) as directory:
        root=Path(directory);source=root/'source';source.mkdir();(source/'bot.cpp').write_text(PROBE,encoding='utf-8')
        artifact=root/'artifact';built=sandbox.build(source,artifact,profile(),image)
        assert built['ok'],built
        name='arena-parity-'+uuid.uuid4().hex
        try:
            result=subprocess.run(['docker','run','--name',name,'--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=16','--memory=256m','--cpus=1','--user','10001:10001','--log-driver=none','-i','--mount',f'type=bind,source={artifact.resolve()},target=/artifact,readonly','--entrypoint','/artifact/bot',image],input=('\n'.join(json.dumps(m) for m,_ in inputs)+'\n').encode(),capture_output=True,timeout=60)
            assert result.returncode==0,result.stderr.decode(errors='replace')
            lines=result.stdout.splitlines();assert len(lines)==len(inputs),(len(lines),len(inputs))
            for i,(line,(_,expected)) in enumerate(zip(lines,inputs)):
                actual=json.loads(line)
                assert actual==expected,(i,inputs[i][0],actual,expected)
        finally:sandbox.cleanup(name)
    report=dict(passed=True,cases=len(inputs),patterns=['king','orthogonal','diagonal','knight'],engine_sha256=experimental.ENGINE_SHA256,sdk_sha256=experimental.sdk_hash(),image_id=image)
    (output/'experimental-parity-latest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'PASS C++ / Python parity: {len(inputs)} states, legal actions and next states',flush=True)

if __name__=='__main__':main()
