"""Verify a real JSON observation -> native decision -> JSON response flow."""
import json
import os
import random
import sys
from experiment import ROOT, BUILD, RESULTS, CODON, ALGORITHMS, Bot, describe, execute, sha


def main():
    os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    source = ROOT / 'bot_json.py'
    executable = BUILD / 'bot-json-codon'
    result, build_seconds = execute([CODON, 'build', '-release', '-numerics=py', '-exe', '-o', executable, source])
    (BUILD / 'json-build.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stderr)
    original = json.loads((RESULTS / 'results.json').read_text())
    fixtures = json.loads((RESULTS / 'fixtures.json').read_text())
    expected = {(case['fixture'], case['algorithm']): (case['move'], case['score'], case['nodes'])
                for case in original['correctness']['cases']}
    commands = {'cpython': [sys.executable, '-u', source], 'codon': [executable]}
    timings = {}
    checks = []
    raw = []
    live = {}
    sequence = 0
    rng = random.Random(2026)
    for algorithm, parameter in ALGORITHMS:
        print('JSON observation/response: ' + algorithm, flush=True)
        sessions = {name: Bot('json-' + name, command + [algorithm, str(parameter)]) for name, command in commands.items()}
        samples = {name: [] for name in sessions}
        try:
            for fixture in fixtures:
                # Full-board handling belongs to the toy text wrapper, not core search.
                if fixture['id'] == 'full-draw':
                    continue
                sequence += 1
                payload = json.dumps({'id': sequence, 'player': fixture['player'], 'board': fixture['board']})
                for name, bot in sessions.items():
                    response, _ = bot.exchange(payload)
                    value = json.loads(response)
                    assert value['id'] == sequence
                    assert (value['move'], value['score'], value['nodes']) == expected[(fixture['id'], algorithm)]
                checks.append({'fixture': fixture['id'], 'algorithm': algorithm, 'matches_text_protocol': True})
            for repeat in range(3):
                for fixture in fixtures[:24]:
                    sequence += 1
                    payload = json.dumps({'id': sequence, 'player': fixture['player'], 'board': fixture['board']})
                    order = list(sessions)
                    rng.shuffle(order)
                    for name in order:
                        response, latency = sessions[name].exchange(payload)
                        value = json.loads(response)
                        assert (value['move'], value['score'], value['nodes']) == expected[(fixture['id'], algorithm)]
                        samples[name].append(latency)
                        raw.append({'runtime': name, 'algorithm': algorithm, 'repeat': repeat,
                                    'fixture': fixture['id'], 'roundtrip_ms': latency})
            timings[algorithm] = {name: describe(values) for name, values in samples.items()}
            live[algorithm] = {name: bot.snapshot() for name, bot in sessions.items()}
        finally:
            for bot in sessions.values():
                bot.close()
    native_libs = execute(['ldd', executable])[0].stdout
    assert 'libpython' not in native_libs.lower()
    assert not any(info['codon']['libpython_loaded'] for info in live.values())
    output = {'source_sha256': sha(source), 'core_sha256_matches_main_experiment': sha(ROOT / 'bot.py') == original['environment']['bot_python_sha256'],
              'compiled': True, 'build_seconds': build_seconds, 'cross_runtime_cases': len(checks),
              'responses_checked': len(checks) * 2, 'correctness_passed': True, 'checks': checks,
              'roundtrip': timings, 'runtime_info': live, 'linkage': native_libs,
              'limitations': 'Valid schema only; minimal SDK adapter, not hardened input handling. Same WSL environment as main experiment.'}
    (RESULTS / 'json-results.json').write_text(json.dumps(output, indent=2))
    (RESULTS / 'json-samples.json').write_text(json.dumps(raw, indent=2))
    print(json.dumps({key: output[key] for key in ['compiled', 'build_seconds', 'core_sha256_matches_main_experiment', 'cross_runtime_cases', 'correctness_passed', 'roundtrip']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
