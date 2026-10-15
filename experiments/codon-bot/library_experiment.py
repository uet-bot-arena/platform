"""Compile actual algorithm-library examples; not a whole-module certification."""
import json
import argparse
import platform
import subprocess
import sys
from datetime import datetime, timezone
from experiment import ROOT, BUILD, RESULTS, CODON, execute, sha

PROBES = {
    'heapq-dijkstra': '''from heapq import heappush, heappop
graph = [[(1, 4), (2, 1)], [(3, 1)], [(1, 2), (3, 5)], []]
dist = [1000000] * 4
dist[0] = 0
queue = [(0, 0)]
while queue:
    cost, node = heappop(queue)
    if cost != dist[node]:
        continue
    for nxt, weight in graph[node]:
        if cost + weight < dist[nxt]:
            dist[nxt] = cost + weight
            heappush(queue, (dist[nxt], nxt))
print(dist)
''',
    'deque-bfs': '''from collections import deque
graph = [[1, 2], [3], [3], []]
queue = deque([0])
seen = {0}
order = []
while queue:
    node = queue.popleft()
    order.append(node)
    for nxt in graph[node]:
        if nxt not in seen:
            seen.add(nxt)
            queue.append(nxt)
print(order)
''',
    'bisect': '''from bisect import bisect_left, bisect_right, insort
values = [1, 3, 3, 7]
insort(values, 5)
print(values, bisect_left(values, 3), bisect_right(values, 3))
''',
    'itertools': '''from itertools import combinations, permutations, product
print(list(combinations([1, 2, 3], 2)))
print(list(permutations([1, 2], 2)))
print(list(product([0, 1], repeat=2)))
''',
    'counter-defaultdict': '''from collections import Counter, defaultdict
counts = Counter([3, 1, 3, 2, 3])
groups = defaultdict(list)
for x in [1, 2, 3, 4]:
    groups[x % 2].append(x)
print(counts[3], counts[9], sorted(groups.items()))
''',
    'lru-cache': '''from functools import lru_cache
@lru_cache(maxsize=128)
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
print(fib(30))
''',
    'math': '''from math import gcd, isqrt, comb
print(gcd(84, 30), isqrt(999), comb(10, 3))
''',
    'math-gcd-comb': '''from math import gcd, comb
print(gcd(84, 30), comb(10, 3))
''',
    'math-gcd-sqrt': '''from math import gcd, sqrt
print(gcd(84, 30), sqrt(144.0))
''',
    'dict-memoization': '''cache = {0: 0, 1: 1}
def fib(n):
    if n not in cache:
        cache[n] = fib(n - 1) + fib(n - 2)
    return cache[n]
print(fib(30))
''',
    'numpy-array': '''import numpy as np
a = np.array([1, 2, 3, 4])
print(int(np.sum(a * a)))
''',
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', nargs='+', choices=list(PROBES))
    args = parser.parse_args()
    folder = BUILD / 'library-probes'
    folder.mkdir(parents=True, exist_ok=True)
    output = RESULTS / 'library-results.json'
    rows = [p for p in json.loads(output.read_text())['probes'] if p['feature'] not in args.only] if args.only and output.exists() else []
    for name, source in PROBES.items():
        if args.only and name not in args.only:
            continue
        path = folder / ('probe_' + name.replace('-', '_') + '.py')
        path.write_text(source)
        reference, _ = execute([sys.executable, path])
        compiled, seconds = execute([CODON, 'build', '-release', '-numerics=py', '-exe', '-o', folder / name, path])
        native = None
        if compiled.returncode == 0:
            native, _ = execute([folder / name], timeout=15)
        matched = bool(native and native.returncode == 0 and reference.returncode == 0 and native.stdout == reference.stdout)
        row = {'feature': name, 'source': source, 'source_sha256': sha(path),
               'build_seconds': seconds, 'compile_exit_code': compiled.returncode,
               'compile_stderr': compiled.stderr[-5000:], 'python_exit_code': reference.returncode,
               'python_stdout': reference.stdout, 'python_stderr': reference.stderr[-2000:],
               'native_exit_code': native.returncode if native else None,
               'native_stdout': native.stdout if native else None,
               'native_stderr': native.stderr[-2000:] if native else None,
               'matches_cpython': matched}
        rows.append(row)
        print(name, 'MATCH' if matched else 'COMPILES' if compiled.returncode == 0 else 'COMPILE FAILED', flush=True)
    report = {'measured_at': datetime.now(timezone.utc).isoformat(), 'codon_version': '0.20.3',
              'compiler_sha256': sha(CODON), 'python_version': sys.version,
              'platform': platform.platform(), 'scope': 'Only the exact snippets below; no Python interoperability.',
              'probes': rows}
    output.write_text(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
