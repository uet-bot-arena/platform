"""Evaluate PyPy under UNCHANGED 256 MiB RLIMIT_AS/seccomp before enabling it."""
from dataclasses import replace
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import sandbox,engine
from arena.config import request_profile
from arena.protocol import Fault
from arena.service import fixtures

def main():
    p=replace(request_profile({'match_ms':60000}),match_ms=None,turn_ms=300,startup_ms=2000)
    image=sandbox.image_id('uet-arena-worker:clock-v2')
    starter=(ROOT/'sdk/bot.py').read_text();reference=(ROOT/'examples/reference-bots/bot.py').read_text()
    sources={'starter':starter,**{mode:reference.replace('MODE = "alphabeta"',f'MODE = "{mode}"') for mode in ('alphabeta','defensive','random','mcts')}}
    injections={'crash':'raise SystemExit(7)','timeout':'while True: pass','spam':'print("x"*70000,flush=True)',
                'stale':'result["turn_id"]="stale"','illegal':'result["payload"]={"from":"a1","to":"o15"}',
                'memhog':'a=bytearray(512*1024*1024)',
                'sneaky':"import os,socket\n            denied=0\n            try: os.fork()\n            except PermissionError: denied+=1\n            try: socket.socket()\n            except PermissionError: denied+=1\n            assert denied==2\n            print('sandbox-denied',file=sys.stderr,flush=True)"}
    for name,code in injections.items():
        marker='        print(json.dumps(result, separators=(",", ":")), flush=True)'
        sources[name]=starter.replace(marker,'        if message["type"]=="observation":\n            '+code+'\n'+marker)
    sources['slow_start']='import time\ntime.sleep(5)\n'+starter
    expected={'crash':'crash','timeout':'timeout','spam':'output_limit','stale':'stale_action','illegal':'illegal_action','memhog':'crash','slow_start':'timeout','sneaky':'ok'}
    rows=[]
    with tempfile.TemporaryDirectory(prefix='arena-pypy-probe-') as tmp:
        root=Path(tmp);sandbox.controller_scope(root,min(os.sched_getaffinity(0)))
        for name,source in sources.items():
            src=root/name;src.mkdir();(src/'bot.py').write_text(source)
            artifact=root/(name+'-artifact');built=sandbox.build(src,artifact,p,image,'pypy3')
            record={'bot':name,'build':built,'expected':expected.get(name,'ok')}
            worker=None
            try:
                assert built['ok'],built
                worker=sandbox.Worker(artifact,replace(p,startup_ms=150 if name=='slow_start' else 2000),image)
                start=time.perf_counter();startup=worker.start('pypy-probe','red');record.update(startup_ms=startup['wall_ms'],start_rpc_ms=(time.perf_counter()-start)*1000)
                cases=fixtures()*3 if name in ('alphabeta','mcts','defensive','random','starter') else [{'state':engine.initial_state()}]
                samples=[]
                for i,case in enumerate(cases):
                    result=worker.turn({'protocol_version':2,'type':'observation','match_id':'pypy-probe','turn_id':str(i+1),'state_version':case['state']['ply'],'payload':engine.observation(case['state'])})
                    try:engine.apply(case['state'],result['message']['payload'])
                    except ValueError:
                        record['actual']='illegal_action';break
                    samples.append(result['wall_ms'])
                record.setdefault('actual','ok');record['turns_wall_ms']=samples
            except Fault as exc:record.update(actual=exc.code,detail=exc.detail,worker_error=getattr(exc,'worker_data',{}))
            except Exception as exc:record.update(actual='harness_error',detail=str(exc))
            finally:
                if worker:
                    record['metrics']=worker.close()
                    record['telemetry']=[json.loads(line) for line in (record['metrics'] or {}).get('stderr','').splitlines() if line.startswith('{')]
            record['passed']=record['actual']==record['expected']
            if name=='sneaky' and record['passed']:record['passed']='sandbox-denied' in (record.get('metrics') or {}).get('stderr','')
            rows.append(record);print(name,record['actual'],'PASS' if record['passed'] else 'FAIL',flush=True)
    all_pass=all(r['passed'] for r in rows)
    from arena.config import PYTHON_INTERPRETER, PYTHON_TOOLCHAIN
    payload={'image_id':image,'memory_limit_mib':256,'rlimit_as_mib':256,'seccomp':'unchanged: fork/clone/socket denied','pypy_available_for_submissions':PYTHON_INTERPRETER=='pypy3',
             'all_compatibility_checks_passed':all_pass,'selected_default':PYTHON_INTERPRETER,'checks':rows}
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    (output/'pypy-probe.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    lines=['# Kiểm tra PyPy trong sandbox OTTv2','',f'Image `{image}`; PyPy trong worker, RLIMIT_AS và container 256 MiB; giữ nguyên seccomp. Không nới bộ nhớ, không tắt JIT để tạo số đo đẹp.',
           '', '| Bot | Mong đợi | Thực tế | Đạt |','|---|---|---|---|',*(f"| {r['bot']} | {r['expected']} | {r['actual']} | {r['passed']} |" for r in rows),'',
           'Mỗi bot đều được build/chạy riêng. JSON đầy đủ gồm startup, RAM cgroup, stderr và telemetry nếu bot chạy được.', '',
           'Kết luận: '+(f'Đạt bộ tương thích. {PYTHON_TOOLCHAIN.split(";")[0]} là runtime mặc định cho bot Python; tốc độ so với CPython và C++ xem [PYTHON-COMPARISON.md](PYTHON-COMPARISON.md). Mọi ngôn ngữ cùng quỹ giờ, không nhân hệ số.' if all_pass else 'PyPy không đạt bộ kiểm chứng này; cần điều tra trước khi dùng.'),'',
           'Nếu PyPy không khởi động được trong hạn mức, nodes/s và độ sâu 100 ms trong điều kiện này là không đo được, không dùng số đo host ngoài sandbox thay thế.']
    (ROOT/'docs/PYPY-VALIDATION.md').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':main()
