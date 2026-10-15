"""Matched OTTv2 algorithms in real Docker; no language handicap is applied."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import engine,sandbox
from arena.adjudication import CURRENT,position_key
from arena.config import request_profile
from arena.service import Service,fixtures
from arena.store import Store


def main():
    image=sandbox.image_id('uet-arena-worker:clock-v2');profile=request_profile({'match_ms':60000})
    report={'image_id':image,'algorithm':'matched negamax alpha-beta; stable ordering, same evaluation, no TT/quiescence',
            'policy':'equal time retained for measurement only; no production language multiplier','cases':[],'matches':[],'pch':{}}
    states=[c['state'] for c in fixtures()[::4]]
    with tempfile.TemporaryDirectory(prefix='arena-language-bench-') as tmp:
        root=Path(tmp);sandbox.controller_scope(root,min(os.sched_getaffinity(0)))
        artifacts={}
        pypy_probe=ROOT/'test-results/pypy-probe.json'
        languages=[('cpp17','bot.cpp'),('python3','bot.py')]
        if pypy_probe.exists() and json.loads(pypy_probe.read_text()).get('all_compatibility_checks_passed'):
            languages.append(('pypy3','bot.py'))
        for language,entry in languages:
            original=(ROOT/'examples/reference-bots'/entry).read_text()
            for mode in ('fixed','timed'):
                source=original
                if mode=='fixed':
                    source=source.replace('MAX_DEPTH=8','MAX_DEPTH=2').replace('MAX_DEPTH = 8','MAX_DEPTH = 2').replace('THINK_MS=100','THINK_MS=5000').replace('THINK_MS = 100','THINK_MS = 5000')
                src=root/(language+'-'+mode);src.mkdir();(src/entry).write_text(source)
                artifact=root/(language+'-'+mode+'-artifact')
                result=sandbox.build(src,artifact,profile,image,'cpython3' if language=='python3' else language);assert result['ok'],result
                artifacts[language,mode]=artifact
                worker=sandbox.Worker(artifact,profile,image)
                try:
                    started=time.perf_counter();startup=worker.start('bench','red');rpc_ms=(time.perf_counter()-started)*1000
                    records=[]
                    for i,state in enumerate(states*12):
                        result=worker.turn({'protocol_version':2,'type':'observation','match_id':'bench','turn_id':str(i+1),'state_version':state['ply'],'payload':engine.observation(state)})
                        assert result['message']['payload'] in engine.legal_moves(state)
                        records.append({'case':i,'action':result['message']['payload'],'wall_ms':result['wall_ms']})
                finally:metrics=worker.close()
                telemetry=[json.loads(line) for line in metrics['stderr'].splitlines() if line.startswith('{')]
                assert len(telemetry)==len(records),metrics
                for record,stats in zip(records,telemetry):
                    report['cases'].append({'language':language,'mode':mode,**record,**stats,'nodes_per_second':stats['nodes']/max(stats['search_ms']/1000,1e-9)})
                report.setdefault('startup',[]).append({'language':language,'mode':mode,'bot_handshake_ms':startup['wall_ms'],'start_rpc_ms':rpc_ms,
                    'container_peak_mib':metrics['cgroup']['memory_peak_bytes']/1048576})
        fixed={language:[r for r in report['cases'] if r['language']==language and r['mode']=='fixed'] for language in ('cpp17','python3')}
        for cpp,py in zip(fixed['cpp17'],fixed['python3']):
            assert (cpp['action'],cpp['nodes'],cpp['depth'])==(py['action'],py['nodes'],py['depth']),(cpp,py)
        report['fixed_depth_parity']=True
        # Swap colours on each opening. Actual clocks, repeats and faults count.
        def game(index):
            cpu=sorted(os.sched_getaffinity(0))[index%min(4,len(os.sched_getaffinity(0)))]
            sandbox.controller_scope(root,cpu)
            languages=('cpp17','python3') if index%2==0 else ('python3','cpp17')
            state=json.loads(json.dumps(states[(index//2)%len(states)]));seen={position_key(state):1}
            workers=[sandbox.Worker(artifacts[language,'timed'],profile,image) for language in languages]
            try:
                for worker,side in zip(workers,engine.SIDES):worker.start('league-'+str(index),side)
                for turn in range(1,129):
                    side=state['to_move'];i=0 if side=='red' else 1
                    result=workers[i].turn({'protocol_version':2,'type':'observation','match_id':'league-'+str(index),'turn_id':str(turn),'state_version':state['ply'],'payload':engine.observation(state)})
                    state=engine.apply(state,result['message']['payload'])
                    key=position_key(state);seen[key]=seen.get(key,0)+1
                    if not state['result'] and seen[key]>=3:engine.finish(state,None,'threefold_repetition')
                    if state['result']:break
                else:engine.finish(state,None,'benchmark_128_ply_cap')
                result=state['result']
                return {'index':index,'red':languages[0],'blue':languages[1],**result,'winning_language':languages[0 if result['winner']=='red' else 1] if result['winner'] else None,'plies':state['ply']}
            finally:
                for worker in workers:worker.close()
        with ThreadPoolExecutor(4) as pool:report['matches']=list(pool.map(game,range(12)))
        for mode in ('without_pch','with_pch'):
            values=[]
            for i in range(3):
                src=root/(mode+str(i));src.mkdir();(src/'bot.cpp').write_text((ROOT/'sdk/bot.cpp').read_text())
                if mode=='without_pch':(src/'json.hpp').write_bytes((ROOT/'sdk/json.hpp').read_bytes()+b'\n')
                result=sandbox.build(src,root/(mode+str(i)+'-artifact'),profile,image)
                assert result['ok'],result
                values.append(result['wall_ms'])
            report['pch'][mode]={'samples_ms':values,'median_ms':statistics.median(values)}
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    (output/'language-benchmark.json').write_text(json.dumps(report,indent=2))
    render_report(report)
    print('PASS matched algorithms, 12 games, PCH comparison',flush=True)


def speedup(report):
    rate = lambda language: statistics.median(r['nodes_per_second'] for r in report['cases'] if r['language'] == language and r['mode'] == 'timed')
    return rate('pypy3') / rate('python3')


def render_report(report):
    from arena.config import PYTHON_TOOLCHAIN
    image=report['image_id']
    languages=sorted({row['language'] for row in report['cases']})
    names={'cpp17':'C++17','python3':'CPython (đối chứng)','pypy3':'PyPy (Python mặc định)'}
    lines=['# Đo Python / C++ trên OTTv2','',f"Image: `{image}`. Sáu thế từ suite OTTv2 công khai, lặp 12 lần (72 mẫu/runtime); cùng thuật toán/evaluation/thứ tự nước; fixed-depth C++/CPython kiểm tra bằng nhau cả nước, số nút và độ sâu.",'',
           '| Ngôn ngữ | Nút/s median (100 ms) | Độ sâu median | Search ms median |','|---|---:|---:|---:|']
    for language in languages:
        rows=[r for r in report['cases'] if r['language']==language and r['mode']=='timed']
        lines.append(f"| {names[language]} | {statistics.median(r['nodes_per_second'] for r in rows):.0f} | {statistics.median(r['depth'] for r in rows):g} | {statistics.median(r['search_ms'] for r in rows):.2f} |")
    lines+=['','| Runtime | Startup handshake median ms | Peak RAM container MiB |','|---|---:|---:|']
    for language in languages:
        rows=[r for r in report['startup'] if r['language']==language]
        lines.append(f"| {names[language]} | {statistics.median(r['bot_handshake_ms'] for r in rows):.2f} | {max(r['container_peak_mib'] for r in rows):.2f} |")
    wins={language:sum(m['winning_language']==language for m in report['matches']) for language in ('cpp17','python3')}
    draws=sum(m['winner'] is None for m in report['matches'])
    lines+=['',f"12 ván C++/CPython, đổi màu trên 6 thế mở đầu: C++ thắng {wins['cpp17']}, CPython thắng {wins['python3']}, hòa {draws}. Giới hạn thí nghiệm 128 ply, lặp 3 lần hòa. Đây là mẫu nhỏ, không đại diện mọi bot; chưa đo tỉ lệ thắng C++/PyPy. JSON từng ván nằm trong test-results/language-benchmark.json.",'',
            '| Build cùng starter | Median ms |','|---|---:|',*(f"| {mode} | {result['median_ms']:.1f} |" for mode,result in report['pch'].items()),'',
            'PCH đối chứng: cùng image và source; chỉ thêm newline vào bản json.hpp để buộc nhánh không dùng PCH. Không gọi đây là đo nguyên commit cũ.', '',
            '## Chính sách thời gian','',
            f'**Cùng quỹ giờ cho mọi ngôn ngữ; không nhân hệ số Python.** Bot Python chạy bằng {PYTHON_TOOLCHAIN.split(";")[0]}; trong phép đo này PyPy tìm được khoảng {speedup(report):.1f} lần số nút/giây của CPython. C/C++ vẫn thường nhanh hơn Python. Xem [PYPY-VALIDATION.md](PYPY-VALIDATION.md).','',
            'Các lựa chọn đã cân nhắc:', '',
            '1. Cùng thời gian: đơn giản, đo đúng bot nhanh trên máy thi; Python thường tìm nông hơn.',
            '2. Nhân thời gian Python: tăng thời lượng giải; hệ số phụ thuộc thuật toán/thế cờ, không suy ra công bằng từ một median.',
            '3. PyPy hoặc Codon: phải thêm image/toolchain riêng và kiểm sandbox, bộ thư viện/giao thức; PyPy là JIT, Codon có thể biên dịch một tập con Python. Không bảo đảm bot CPython tùy ý sẽ chạy được.',
            '', 'Phép đo dùng sandbox thật. Nhãn python3 trong JSON đo chỉ CPython đối chứng; API python3 trong sản phẩm chạy PyPy. Không suy hệ số công bằng chung từ một thuật toán hay median.']
    (ROOT/'docs/PYTHON-COMPARISON.md').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':main()
