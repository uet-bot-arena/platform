"""Build the exact downloadable SDK v3 ZIP and exercise it in trusted Docker workers."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import experimental, sandbox
from arena.adjudication import CURRENT
from arena.archive import unpack
from arena.config import profile
from arena.service import fixtures, summary


def main():
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    downloads = ROOT / 'downloads'
    downloads.mkdir(exist_ok=True)
    archive = downloads / 'ottv2-sdk-v3-minimax.zip'
    example = ROOT / 'examples' / 'experimental-minimax'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('bot.cpp', 'README.md'):
            z.write(example / name, name)
        for name in ('experimental.hpp', 'json.hpp', 'LICENSE.MIT'):
            z.write(ROOT / 'sdk' / name, name)
    image = sandbox.image_id('uet-arena-worker:experimental-v1')
    p = profile()
    report = dict(passed=False, image_id=image, sdk_sha256=experimental.sdk_hash(), checks=[])

    def checked(name, **data):
        report['checks'].append(dict(name=name, passed=True, **data))
        print('PASS', name, data, flush=True)

    def context(definition, kind):
        return dict(kind=kind, adjudication=CURRENT, ruleset=dict(
            definition=definition, hash=experimental.rule_hash(definition), requires=['rules-v1']))

    def message(rules, state, turn):
        payload = rules.observation(state)
        payload['adjudication'] = CURRENT
        return dict(protocol_version=3, type='observation', match_id='minimax-proof',
                    turn_id=str(turn), state_version=state['ply'], payload=payload)

    with tempfile.TemporaryDirectory(prefix='minimax-', dir=output) as directory:
        base = Path(directory)
        manifest = unpack(archive.read_bytes(), base / 'source', p)
        report['archive'] = manifest
        built = sandbox.build(base / 'source', base / 'artifact', p, image)
        assert built['ok'], built
        checked('exact_flat_zip_passes_upload_validation_and_cpp17_build', build_ms=built['wall_ms'])

        logs = []
        for name, definition in experimental.PRESETS.items():
            rules = experimental.Rules(definition)
            w = sandbox.Worker(base / 'artifact', p, image)
            samples = []
            try:
                w.start('minimax-proof', 'red', context(definition, 'benchmark'))
                for i, case in enumerate(fixtures(rules) * 3):
                    result = w.turn(message(rules, case['state'], i + 1))
                    rules.apply(case['state'], result['message']['payload'])
                    samples.append(result)
            finally:
                final = w.close()
                if final:
                    logs.append(final['stderr'])
            assert len(samples) == 72
            checked(name + '_real_worker_v3_ready_and_72_legal_samples', timing=summary(samples))

        definition = deepcopy(experimental.PRESETS['domination'])
        rules = experimental.Rules(definition)

        def state(pieces):
            s = rules.initial_state()
            s['board'] = [[None] * 9 for _ in range(9)]
            for square, side, kind in pieces:
                r, c = 9 - int(square[1]), ord(square[0]) - ord('a')
                s['board'][r][c] = dict(side=side, kind=kind, id=side + '-' + square,
                                      shield_until=0, cooldowns={})
            return s

        base_win = state([('h8', 'red', 'rock'), ('b8', 'red', 'paper'), ('c8', 'red', 'scissors'),
                          ('f6', 'blue', 'rock'), ('g6', 'blue', 'paper'), ('h6', 'blue', 'scissors')])
        control_win = state([('d5', 'red', 'paper'), ('e5', 'blue', 'rock'), ('i8', 'blue', 'scissors')])
        control_win['control']['red'] = 5
        defend = state([('b1', 'red', 'paper'), ('d4', 'red', 'rock'), ('h2', 'red', 'scissors'),
                        ('b2', 'blue', 'rock'), ('h8', 'blue', 'paper'), ('g8', 'blue', 'scissors')])
        for name, s in [('base_capture', base_win), ('control_target', control_win), ('defend_base', defend)]:
            w = sandbox.Worker(base / 'artifact', p, image)
            try:
                w.start('minimax-proof', 'red', context(definition, 'match'))
                result = w.turn(message(rules, s, 1))
                a = next(a for a in rules.legal_actions(s) if a['id'] == result['message']['payload']['action_id'])
                after = rules.apply(s, result['message']['payload'])
                if name != 'defend_base':
                    assert after['result'] == dict(winner='red', reason=name), (name, a, after)
                else:
                    assert not any(rules.apply(after, {'action_id': reply['id']})['result'] ==
                                   dict(winner='blue', reason='base_capture') for reply in rules.legal_actions(after)), a
                checked('tactic_' + name, action=a, wall_ms=result['wall_ms'])
            finally:
                final = w.close()
                if final:
                    logs.append(final['stderr'])

        # One bounded actual duel: minimax Red versus the SDK's one-ply heuristic Blue.
        # No production DB, account, tournament or artifact is touched.
        w = sandbox.Worker(base / 'artifact', p, image)
        s = rules.initial_state()
        seen = {rules.position_key(s): 1}
        try:
            w.start('minimax-proof', 'red', context(definition, 'match'))
            for turn in range(1, 65):
                if s['to_move'] == 'red':
                    result = w.turn(message(rules, s, turn))
                    action = result['message']['payload']
                else:
                    options = rules.legal_actions(s)
                    chosen = max(options, key=lambda a: heuristic(rules, rules.apply(s, {'action_id': a['id']}), 'blue'))
                    action = {'action_id': chosen['id']}
                s = rules.apply(s, action)
                key = rules.position_key(s)
                seen[key] = seen.get(key, 0) + 1
                if s['result']:
                    break
                if seen[key] >= 3:
                    s['result'] = dict(winner=None, reason='threefold_repetition')
                    break
            checked('bounded_legal_duel_against_one_ply', plies=s['ply'], result=s['result'], cap=64)
        finally:
            final = w.close()
            if final:
                logs.append(final['stderr'])
        depths = [int(n) for log in logs for n in re.findall(r'depth=(\d+)', log)]
        assert depths and max(depths) >= 2, depths
        checked('completed_lookahead_beyond_one_ply', min_depth=min(depths), max_depth=max(depths))
        report['depth_histogram'] = {str(d): depths.count(d) for d in sorted(set(depths))}
        report['passed'] = True
        report['completed_ms'] = time.time_ns() // 1000000
        (output / 'experimental-minimax-latest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('ALL MINIMAX ZIP CHECKS PASSED', archive, flush=True)


def heuristic(rules, s, side):
    # Same baseline objective weights as SDK Rules::evaluate_objectives.
    if s['result']:
        return 0 if s['result']['winner'] is None else 1e6 if s['result']['winner'] == side else -1e6
    out = 0
    for who in (side, 'red' if side == 'blue' else 'blue'):
        sign = 1 if who == side else -1
        out += sign * (8 * s['energy'][who] + 25 * s['control'][who])
        for r, row in enumerate(s['board']):
            for c, p in enumerate(row):
                if not p or p['side'] != who:
                    continue
                out += sign * (100 + (8-r+c if who == 'red' else r+8-c) + 8 * (p['shield_until'] > s['turns'][who]))
                square = chr(ord('a') + c) + str(9-r)
                out += sign * 15 * sum(z['square'] == square for z in rules.d['zones'])
    return out


if __name__ == '__main__':
    main()
