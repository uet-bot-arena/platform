"""Reproducible Linux/WSL experiment. Executes only the trusted local prototypes."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import select
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
RESULTS = ROOT / 'results'
CODON = ROOT / '.tools/compiler/bin/codon'
ALGORITHMS = [('greedy', 0), ('minimax', 3), ('mc', 128)]


def execute(argv, timeout=180, input_text=None):
    start = time.perf_counter()
    completed = subprocess.run([str(x) for x in argv], cwd=ROOT, input=input_text,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout)
    return completed, time.perf_counter() - start


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(samples):
    values = sorted(samples)
    return {'samples': len(values), 'median_ms': statistics.median(values),
            'p95_ms': values[max(0, math.ceil(.95 * len(values)) - 1)],
            'min_ms': values[0], 'max_ms': values[-1]}


class Bot:
    def __init__(self, name, argv):
        self.name = name
        self.buffer = b''
        self.stderr = (BUILD / (name + '-stderr.log')).open('wb')
        started = time.perf_counter_ns()
        self.process = subprocess.Popen([str(x) for x in argv], cwd=ROOT,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr, bufsize=0)
        assert self.readline() == 'READY|1', name + ' did not become ready'
        self.startup_ms = (time.perf_counter_ns() - started) / 1e6

    def readline(self, timeout=10):
        deadline = time.monotonic() + timeout
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                self.process.kill()
                raise TimeoutError(self.name + ' response timeout')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError(self.name + ' closed stdout; see build stderr log')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        return line.decode().strip()

    def exchange(self, request):
        started = time.perf_counter_ns()
        self.process.stdin.write((request + '\n').encode())
        self.process.stdin.flush()
        response = self.readline()
        return response, (time.perf_counter_ns() - started) / 1e6

    def snapshot(self):
        base = Path('/proc') / str(self.process.pid)
        status = (base / 'status').read_text()
        maps = (base / 'maps').read_text()
        hwm = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmHWM:'))
        return {'peak_rss_mib': hwm / 1024, 'executable': str((base / 'exe').resolve()),
                'libpython_loaded': 'libpython' in maps.lower(), 'pid': self.process.pid}

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write(b'QUIT\n')
            self.process.stdin.flush()
        self.process.communicate(timeout=10)
        self.stderr.close()
        assert self.process.returncode == 0, self.name + ' failed to shut down'


def has_winner(board):
    # Independent fixture validation by slicing geometric lines.
    lines = []
    lines += [board[r * 9:(r + 1) * 9] for r in range(9)]
    lines += [[board[r * 9 + c] for r in range(9)] for c in range(9)]
    for delta in range(-8, 9):
        lines.append([board[r * 9 + c] for r in range(9) for c in range(9) if r - c == delta])
        lines.append([board[r * 9 + c] for r in range(9) for c in range(9) if r + c == delta + 8])
    return any(abs(sum(line[i:i + 5])) == 5 for line in lines for i in range(len(line) - 4))


def fixtures():
    rng = random.Random(20261010)
    data = []
    for index in range(24):
        occupied = [6, 12, 20, 32, 48, 60][index % 6]
        for attempt in range(10000):
            cells = rng.sample(range(81), occupied)
            board = [0] * 81
            for turn, cell in enumerate(cells):
                board[cell] = 1 if turn % 2 == 0 else -1
            if not has_winner(board):
                break
        else:
            raise RuntimeError('Could not generate non-terminal fixture')
        data.append({'id': 'random-' + str(index), 'board': board, 'player': 1 if index % 2 == 0 else -1})
    data.append({'id': 'empty', 'board': [0] * 81, 'player': 1})
    winning = [0] * 81
    winning[36] = -1
    for cell in range(37, 41):
        winning[cell] = 1
    data.append({'id': 'win-now', 'board': winning, 'player': 1})
    data.append({'id': 'block-now', 'board': [-x for x in winning], 'player': 1})
    full = [1 if (r + 2 * c) % 4 < 2 else -1 for r in range(9) for c in range(9)]
    assert not has_winner(full)
    almost = full.copy()
    almost[40] = 0
    data += [{'id': 'one-empty', 'board': almost, 'player': 1},
             {'id': 'full-draw', 'board': full, 'player': -1}]
    assert all(not has_winner(f['board']) for f in data)
    return data


def request(fixture, algorithm, parameter, request_id):
    return f"{request_id}|{algorithm}|{fixture['player']}|{parameter}|" + ','.join(map(str, fixture['board']))


def parse_response(text, request_id):
    parts = text.split('|')
    assert len(parts) == 5 and parts[0] == str(request_id), text
    return tuple(map(int, parts[1:4])), int(parts[4]) / 1000


def compatibility_probes():
    snippets = {
        'json_standard_library': 'import json\nx = json.loads(\'{"turn": 3, "board": [0, 1, -1]}\')\nprint(json.dumps(x, sort_keys=True))\n',
        'mixed_list': 'values = [1]\nvalues.append("hello")\nprint(values)\n',
        'integer_overflow': 'x = 2 ** 62\nprint(x * 4)\n',
    }
    output = {}
    probe_dir = BUILD / 'probes'
    probe_dir.mkdir(exist_ok=True)
    for name, code in snippets.items():
        source = probe_dir / (name + '.py')
        executable = probe_dir / name
        source.write_text(code)
        py, _ = execute([sys.executable, source])
        native, elapsed = execute([CODON, 'build', '-release', '-numerics=py', '-exe', '-o', executable, source])
        (probe_dir / (name + '-build.log')).write_text(native.stdout + native.stderr)
        item = {'source': code, 'python_output': py.stdout.strip(), 'compiled': native.returncode == 0,
                'compile_seconds': elapsed, 'diagnostic': native.stderr[-1500:]}
        if native.returncode == 0:
            result, _ = execute([executable])
            item.update(native_output=result.stdout.strip(), native_exit_code=result.returncode,
                        same_output=result.stdout.strip() == py.stdout.strip())
        output[name] = item
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    BUILD.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    affinity_before = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {affinity_before[-1]})
    environment = {'platform': platform.platform(), 'python': sys.version,
                   'cpu': next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), 'unknown'),
                   'cpu_affinity': sorted(os.sched_getaffinity(0)), 'available_cpus': affinity_before,
                   'compiler_sha256': sha(CODON), 'bot_python_sha256': sha(ROOT / 'bot.py'),
                   'bot_cpp_sha256': sha(ROOT / 'bot.cpp'), 'date_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    environment['codon'] = execute([CODON, '--version'])[0].stdout.strip()
    environment['g++'] = execute(['g++', '--version'])[0].stdout.splitlines()[0]
    compile_specs = {'codon': [CODON, 'build', '-release', '-numerics=py', '-exe', '-o', 'build/bot-codon', 'bot.py'],
                     'cpp': ['g++', '-O3', '-DNDEBUG', '-std=c++17', '-march=native', '-o', 'build/bot-cpp', 'bot.cpp']}
    builds = {}
    if not args.skip_build:
        for name, command in compile_specs.items():
            print('Building ' + name, flush=True)
            result, elapsed = execute(command)
            (BUILD / (name + '-build.log')).write_text(result.stdout + result.stderr)
            builds[name] = {'seconds': elapsed, 'command': [str(x) for x in command], 'exit_code': result.returncode}
            if result.returncode:
                print(result.stderr[-5000:], flush=True)
                raise RuntimeError(name + ' build failed')
    (RESULTS / 'builds.json').write_text(json.dumps(builds, indent=2))
    commands = {'cpython': [sys.executable, '-u', ROOT / 'bot.py'],
                'codon': [BUILD / 'bot-codon'], 'cpp': [BUILD / 'bot-cpp']}
    data = fixtures()
    (RESULTS / 'fixtures.json').write_text(json.dumps(data, indent=2))
    sessions = {name: Bot(name, command) for name, command in commands.items()}
    checks = []
    protocol_checks = {}
    try:
        for name, bot in sessions.items():
            assert bot.exchange('malformed')[0] == 'ERROR|protocol'
            assert bot.exchange('bad|greedy|1|0|0')[0] == 'ERROR|board'
            protocol_checks[name] = True
        print(f'Checking {len(data)} positions x 3 algorithms x 3 runtimes', flush=True)
        sequence = 0
        for fixture in data:
            for algorithm, parameter in ALGORITHMS:
                sequence += 1
                outputs = {}
                for name, bot in sessions.items():
                    response, _ = bot.exchange(request(fixture, algorithm, parameter, sequence))
                    outputs[name], _ = parse_response(response, sequence)
                assert len(set(outputs.values())) == 1, (fixture['id'], algorithm, outputs)
                move, score, nodes = outputs['cpython']
                assert (move == -1 and 0 not in fixture['board']) or (0 <= move < 81 and fixture['board'][move] == 0)
                if fixture['id'] == 'win-now' and algorithm in ('greedy', 'minimax'):
                    assert move == 41 and score == 1000000, outputs
                if fixture['id'] == 'block-now' and algorithm == 'minimax':
                    assert move == 41, outputs
                if fixture['id'] == 'one-empty':
                    assert move == 40, outputs
                checks.append({'fixture': fixture['id'], 'algorithm': algorithm, 'parameter': parameter,
                               'move': move, 'score': score, 'nodes': nodes, 'all_equal': True})
        print('Correctness passed. Measuring warmed persistent requests...', flush=True)
        samples = {name: {algorithm: {'compute_ms': [], 'roundtrip_ms': []} for algorithm, _ in ALGORITHMS} for name in commands}
        raw_samples = []
        rng = random.Random(8716)
        bench_fixtures = data[:24]
        # Balanced randomized runtime order; one runtime actively computes at a time.
        for repeat in range(args.repeats):
            jobs = [(fixture, algorithm, parameter) for fixture in bench_fixtures for algorithm, parameter in ALGORITHMS]
            rng.shuffle(jobs)
            for fixture, algorithm, parameter in jobs:
                order = list(commands)
                rng.shuffle(order)
                expected = None
                for name in order:
                    sequence += 1
                    response, latency = sessions[name].exchange(request(fixture, algorithm, parameter, sequence))
                    value, compute_ms = parse_response(response, sequence)
                    if expected is None:
                        expected = value
                    assert value == expected
                    samples[name][algorithm]['compute_ms'].append(compute_ms)
                    samples[name][algorithm]['roundtrip_ms'].append(latency)
                    raw_samples.append({'repeat': repeat, 'fixture': fixture['id'], 'algorithm': algorithm,
                                        'runtime': name, 'compute_ms': compute_ms, 'roundtrip_ms': latency,
                                        'move': value[0], 'score': value[1], 'nodes': value[2]})
            print(f'  repeat {repeat + 1}/{args.repeats} complete', flush=True)
        ping = {}
        for name, bot in sessions.items():
            values = []
            for _ in range(200):
                text, latency = bot.exchange('PING')
                assert text == 'PONG'
                values.append(latency)
            ping[name] = describe(values)
        runtime_info = {name: bot.snapshot() for name, bot in sessions.items()}
    finally:
        for bot in sessions.values():
            bot.close()
    startup = {}
    print('Measuring warm-cache process startup, 20 samples per runtime...', flush=True)
    for name, command in commands.items():
        values = []
        for _ in range(20):
            bot = Bot(name, command)
            values.append(bot.startup_ms)
            bot.close()
        startup[name] = describe(values)
    print('Probing Python compatibility...', flush=True)
    probes = compatibility_probes()
    linkage = {}
    for name in ('codon', 'cpp'):
        executable = commands[name][0]
        linkage[name] = {'file': execute(['file', executable])[0].stdout.strip(),
                         'ldd': execute(['ldd', executable])[0].stdout.strip(),
                         'binary_bytes': executable.stat().st_size}
    summary = {name: {algorithm: {metric: describe(values) for metric, values in metrics.items()}
                     for algorithm, metrics in algorithms.items()} for name, algorithms in samples.items()}
    result = {'environment': environment, 'builds': builds,
              'method': {'game': 'Synthetic 9x9 connect-five, not OTTv2', 'single_threaded': True,
                         'minimax_depth': 3, 'branch_cap': 8, 'mc_rollouts': 128, 'mc_max_plies': 24,
                         'mc_kind': 'Flat Monte Carlo; no MCTS tree', 'repeats': args.repeats,
                         'benchmark_positions': len(bench_fixtures), 'correctness_positions': len(data),
                         'timing': 'compute excludes protocol parsing; roundtrip includes parse/serialize and parent pipe I/O; warmed caches',
                         'source_location': 'Windows filesystem mounted in WSL2; timing is exploratory, not production capacity'},
              'correctness': {'passed': True, 'cross_runtime_cases': len(checks),
                              'runtime_responses_checked': len(checks) * len(commands),
                              'protocol_checks': protocol_checks, 'cases': checks},
              'summary': summary, 'startup': startup, 'ping': ping, 'runtime_info': runtime_info,
              'linkage': linkage, 'compatibility': probes}
    (RESULTS / 'results.json').write_text(json.dumps(result, indent=2))
    (RESULTS / 'samples.json').write_text(json.dumps(raw_samples, indent=2))
    print(json.dumps({'correctness_cases': len(checks), 'timings': summary, 'startup': startup,
                      'memory': runtime_info, 'probes': {name: {k: v for k, v in p.items() if k in ('compiled', 'same_output', 'python_output', 'native_output')} for name, p in probes.items()}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
