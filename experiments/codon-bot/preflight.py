"""A local worker prototype for our trusted bots, NOT an upload sandbox.

Reports runner wall latency, cumulative child CPU and peak process RSS separately.
No arbitrary source/command input: only the three already-built internal prototypes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import select
import subprocess
import sys
import time
from experiment import ROOT, BUILD, RESULTS, fixtures, request, parse_response, sha, describe


class ProbeError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class Process:
    def __init__(self, argv, log):
        self.log = log.open('wb')
        self.p = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.log, bufsize=0)
        self.buffer = b''
        self.peak_rss_mib = None

    def memory(self):
        try:
            lines = (Path('/proc') / str(self.p.pid) / 'status').read_text().splitlines()
            hwm = next(int(s.split()[1]) / 1024 for s in lines if s.startswith('VmHWM:'))
            self.peak_rss_mib = max(self.peak_rss_mib or 0, hwm)
        except (OSError, StopIteration):
            pass

    def line(self, timeout_ms):
        deadline = time.perf_counter() + timeout_ms / 1000
        while b'\n' not in self.buffer:
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or not select.select([self.p.stdout], [], [], remaining)[0]:
                raise ProbeError('timeout', 'Response deadline exceeded')
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                raise ProbeError('crash', 'Process exited or closed stdout before response')
            self.buffer += chunk
            if len(self.buffer) > 65536:
                raise ProbeError('protocol', 'stdout line exceeds 64 KiB')
        line, self.buffer = self.buffer.split(b'\n', 1)
        try:
            return line.decode('utf-8')
        except UnicodeDecodeError:
            raise ProbeError('protocol', 'stdout is not UTF-8')

    def exchange(self, text, budget_ms):
        start = time.perf_counter_ns()
        try:
            self.p.stdin.write((text + '\n').encode())
            self.p.stdin.flush()
        except BrokenPipeError:
            raise ProbeError('crash', 'stdin closed')
        line = self.line(budget_ms)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        if elapsed > budget_ms:
            raise ProbeError('timeout', 'End-to-end response exceeded wall budget')
        self.memory()  # Sample RSS after stopping the latency clock.
        return line, elapsed

    def stop(self, success):
        self.memory()
        if self.p.poll() is None:
            if success:
                try:
                    self.p.stdin.write(b'QUIT\n')
                    self.p.stdin.flush()
                except BrokenPipeError:
                    pass
            else:
                self.p.kill()
        try:
            self.p.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.communicate()
        self.log.close()


def run(runtime, algorithm, repeats=5, budget_ms=100, startup_ms=2000):
    commands = {'cpython': [sys.executable, str(ROOT / 'bot.py')],
                'codon': [str(BUILD / 'bot-codon')], 'cpp': [str(BUILD / 'bot-cpp')]}
    parameter = {'greedy': 0, 'minimax': 3, 'mc': 128}[algorithm]
    positions = fixtures()
    oracle = json.loads((RESULTS / 'results.json').read_text())
    expected = {c['fixture']: (c['move'], c['score'], c['nodes']) for c in oracle['correctness']['cases'] if c['algorithm'] == algorithm}
    samples, errors = [], []
    ready_ms, peak = None, None
    total_cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter_ns()
    process = Process(commands[runtime], BUILD / ('preflight-' + runtime + '.log'))
    phase, current = 'startup', None
    checked = 0
    try:
        if process.line(startup_ms) != 'READY|1':
            raise ProbeError('protocol', 'Expected READY|1 handshake')
        ready_ms = (time.perf_counter_ns() - started) / 1e6
        if ready_ms > startup_ms:
            raise ProbeError('timeout', 'Startup exceeded wall budget')
        request_id = 0
        for phase, cases in [('correctness', positions), ('benchmark', positions[:24] * repeats)]:
            for current in cases:
                request_id += 1
                line, wall_ms = process.exchange(request(current, algorithm, parameter, request_id), budget_ms)
                try:
                    response, _ = parse_response(line, request_id)
                except (ValueError, AssertionError):
                    raise ProbeError('protocol', 'Expected id|move|score|nodes|compute_us integer response')
                if response != expected[current['id']]:
                    raise ProbeError('mismatch', 'Move, score or node count differs from reference')
                if phase == 'correctness':
                    checked += 1
                else:
                    samples.append({'fixture': current['id'], 'wall_ms': wall_ms})
    except ProbeError as exc:
        errors.append({'phase': phase, 'fixture': current['id'] if current else None, 'kind': exc.kind, 'message': str(exc)})
    finally:
        process.stop(not errors)
        peak = process.peak_rss_mib
    total_cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if not errors and process.p.returncode != 0:
        errors.append({'phase': 'shutdown', 'fixture': None, 'kind': 'crash', 'message': 'Nonzero exit on shutdown'})
    cpu_ms = ((total_cpu_after.ru_utime - total_cpu_before.ru_utime) + (total_cpu_after.ru_stime - total_cpu_before.ru_stime)) * 1000
    report = {'schema_version': 1, 'origin': 'local_worker_prototype', 'submission_id': None,
              'status': 'failed' if errors else 'completed', 'measured_at': datetime.now(timezone.utc).isoformat(),
              'runtime': runtime, 'algorithm': algorithm, 'parameter': parameter,
              'source_sha256': sha(ROOT / ('bot.cpp' if runtime == 'cpp' else 'bot.py')),
              'executable_sha256': sha(Path(commands[runtime][0])),
              'suite_sha256': sha(RESULTS / 'fixtures.json'),
              'worker': {'cpu': oracle['environment']['cpu'], 'platform': oracle['environment']['platform'], 'affinity': sorted(os.sched_getaffinity(0))},
              'limits': {'turn_wall_ms': budget_ms, 'startup_wall_ms': startup_ms, 'memory_mib': None, 'scope': 'internal trial only; no memory sandbox'},
              'correctness': {'passed': checked, 'planned': len(positions), 'ott_legality': None},
              'benchmark': {'planned_samples': 24 * repeats, 'completed_samples': len(samples),
                            'turn_wall': describe([s['wall_ms'] for s in samples]) if samples else None,
                            'cpu_total_ms': cpu_ms, 'cpu_scope': 'whole process: startup + correctness + benchmark + shutdown',
                            'startup_wall_ms': ready_ms, 'peak_process_rss_mib': peak,
                            'p95_budget_percent': describe([s['wall_ms'] for s in samples])['p95_ms'] / budget_ms * 100 if samples else None},
              'errors': errors, 'samples': samples,
              'failure_counts': {kind: sum(e['kind'] == kind for e in errors) for kind in ['timeout', 'crash', 'protocol', 'mismatch']},
              'limitations': ['Trusted built-in prototype only; not a sandbox.', 'Synthetic connect-five, not OTTv2.', 'WSL laptop; no prediction of production server capacity.', 'CPU is measured only in aggregate; peak RSS excludes the worker and compiler.', 'No uploaded team source was executed.']}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', choices=['cpython', 'codon', 'cpp'], default='cpp')
    parser.add_argument('--algorithm', choices=['greedy', 'minimax', 'mc'], default='minimax')
    parser.add_argument('--budget-ms', type=float, default=100)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.budget_ms <= 0 or args.repeats < 1:
        parser.error('budget and repeats must be positive')
    os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    report = run(args.runtime, args.algorithm, args.repeats, args.budget_ms)
    path = RESULTS / ('preflight-' + args.runtime + '-' + args.algorithm + '.json')
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ['status', 'runtime', 'correctness', 'benchmark', 'errors']}, indent=2))
