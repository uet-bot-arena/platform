"""Opt-in Docker experiment for the example; does not change the live API DB."""
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import engine, sandbox
from arena.config import profile
from arena.service import Service
from arena.store import Store


def packed(folder):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('bot.cpp', 'json.hpp', 'LICENSE.MIT', 'README.md'):
            archive.write(folder / name, name)
    return out.getvalue()


def tactical(pieces, side='red'):
    state = {'board': [[None]*9 for _ in range(9)], 'to_move': side, 'ply': 40, 'result': None}
    for cell, who, kind in pieces:
        r, c = engine.position(cell)
        state['board'][r][c] = {'side': who, 'kind': kind}
    return state


def main():
    image = sandbox.image_id()
    root = Path(tempfile.mkdtemp(prefix='alphabeta-', dir=ROOT / 'test-results'))
    report = {'image_id': image, 'engine_sha256': engine.ENGINE_SHA256, 'checks': []}
    def passed(name, **details):
        report['checks'].append({'name': name, **details})
        print('PASS', name, json.dumps(details), flush=True)
    example = ROOT / 'examples' / 'alphabeta'
    # Compile a diagnostic executable from exactly the example's rule methods.
    diagnostic = root / 'diagnostic'
    diagnostic.mkdir()
    wrapper = r'''
#define OTT_RULES_TEST
''' + (example / 'bot.cpp').read_text() + r'''
int main() {
    setup(); std::string line;
    while(std::getline(std::cin,line)) {
        Position p(json::parse(line)); json children=json::array();
        for(Move m:p.moves()) {
            Position child=p.play(m); std::uint64_t h=child.side ? side_key : 0;
            for(int s=0;s<81;++s) if(child.board[s]) h^=zobrist[s][child.board[s]];
            children.push_back({{"move",{{"from",square(m.from)},{"to",square(m.to)}}},
                {"board",child.board},{"winner",child.winner},{"hash_ok",h==child.hash}});
        }
        std::cout<<children.dump()<<'\n';
    }
}
'''
    (diagnostic / 'bot.cpp').write_text(wrapper)
    build = sandbox.build(diagnostic, root / 'diagnostic-bin', profile(), image)
    assert build['ok'], build
    rng = random.Random(20261010)
    states, state = [], engine.initial_state()
    for _ in range(100):
        for _ in range(5):
            if state['result']: state = engine.initial_state()
            state = engine.apply(state, rng.choice(engine.legal_moves(state)))
        if not state['result']: states.append(state)
    capture_base = tactical([('h8','red','rock'),('a2','red','paper'),('c2','red','scissors'),
        ('b9','blue','rock'),('d9','blue','paper'),('f9','blue','scissors')])
    capture_type = tactical([('e5','red','rock'),('a2','red','paper'),('c2','red','scissors'),
        ('f5','blue','scissors'),('i8','blue','rock'),('d9','blue','paper')])
    defend_base = tactical([('c2','red','paper'),('e3','red','rock'),('g3','red','scissors'),
        ('b2','blue','rock'),('i7','blue','rock'),('d9','blue','paper'),('f9','blue','scissors')])
    save_type = tactical([('e5','red','scissors'),('a2','red','paper'),('c2','red','rock'),
        ('f5','blue','rock'),('h8','blue','paper'),('i8','blue','scissors')])
    states += [capture_base, capture_type, defend_base, save_type]
    result = subprocess.run(['docker','run','--rm','--network=none','--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--memory=256m','--cpus=1','--pids-limit=32','-i',
        '--mount',f'type=bind,source={root / "diagnostic-bin"},target=/artifact,readonly',
        '--entrypoint','/artifact/bot',image],
        input=''.join(json.dumps(s)+'\n' for s in states), text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == len(states)
    transitions = 0
    for state, line in zip(states, lines):
        children = json.loads(line)
        assert [c['move'] for c in children] == engine.legal_moves(state)
        for child in children:
            expected = engine.apply(state, child['move'])
            board = [0 if p is None else 1+(0 if p['side']=='red' else 3)+
                {'rock':0,'paper':1,'scissors':2}[p['kind']] for row in expected['board'] for p in row]
            winner = -1 if not expected['result'] else (0 if expected['result']['winner']=='red' else 1)
            assert child['board'] == board and child['winner'] == winner and child['hash_ok']
            transitions += 1
    passed('rules_differential_and_incremental_hash', positions=len(states), transitions=transitions)
    store = Store(root / 'state')
    service = Service(store, start_worker=False)
    def submit(folder, version):
        return service.submit('experiment', version, 'cpp17', packed(folder), uuid.uuid4().hex)
    def execute(kind, body):
        job = service.enqueue('experiment', kind, body, uuid.uuid4().hex)
        service.execute(job)
        final = store.get(job['id'])
        assert final['status'] == 'completed', final
        return final['report']
    advanced = submit(example, 'v2.0.0')
    starter = submit(ROOT / 'sdk', 'v1.0.0')
    benchmark = execute('benchmark', {'submission_id': advanced['id']})
    assert benchmark['passed'] and len(benchmark['samples']) == 72
    assert all(int(s['received_mono_ns']) < int(s['deadline_mono_ns']) for s in benchmark['samples'])
    report['benchmark'] = benchmark
    passed('real_worker_72_sample_benchmark', summary=benchmark['turn_wall_ms'])
    artifact, _ = service.ensure_artifact({'id': 'tactics', 'image_id': image}, store.get(advanced['id']), profile())
    worker = sandbox.Worker(artifact, profile(), image)
    try:
        worker.start('tactics', 'red')
        for index, (name, state) in enumerate([('base_capture',capture_base),('type_elimination',capture_type),
                ('defend_base',defend_base),('save_last_of_type',save_type)]):
            message = {'protocol_version':2,'type':'observation','match_id':'tactics','turn_id':f'opaque-{index}',
                'state_version':state['ply'],'payload':engine.observation(state)}
            result = worker.turn(message)
            action = result['message']['payload']
            after = engine.apply(state, action)
            if index < 2:
                assert after['result'] and after['result']['winner']=='red', (name, action)
            else:
                if after['result']:
                    assert after['result']['winner']=='red', (name, action)
                else:
                    assert not any(engine.apply(after, m)['result'] for m in engine.legal_moves(after)), (name, action)
            passed('tactic_' + name, action=action, wall_ms=result['wall_ms'])
    finally:
        worker.close()
    report['matches'] = []
    for red, blue in ((advanced, starter),(starter, advanced)):
        match = execute('match', {'red_submission_id':red['id'], 'blue_submission_id':blue['id'],
            'delegate_timeout_claims':True})
        assert not match.get('fault'), match.get('fault')
        advanced_side = 'red' if red['id']==advanced['id'] else 'blue'
        item = {'advanced_side':advanced_side,'result':match['result'],
            'plies':match['final_state']['ply'],'timing':match['turn_wall_ms']}
        report['matches'].append(item)
        passed('real_match_vs_starter_' + advanced_side, **item)
    store.db.close()
    report['finished_unix_ms'] = time.time_ns() // 1_000_000
    (root / 'report.json').write_text(json.dumps(report, indent=2))
    print('REPORT', root / 'report.json', flush=True)


if __name__ == '__main__':
    main()
