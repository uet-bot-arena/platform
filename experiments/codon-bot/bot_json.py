"""Thin JSON-lines SDK wrapper around the exact same Python bot algorithms.

Algorithm/parameter are process arguments. Per-turn input is JSON with integer
id, player and an 81-element board; output fields are all integers.
Indexing/casting at the boundary works with CPython and Codon's native JSON type.
"""
import json
import sys
from time import perf_counter
from bot import greedy, minimax, monte_carlo

algorithm = sys.argv[1]
parameter = int(sys.argv[2])
print('READY|1', flush=True)
for raw_line in sys.stdin:
    line = raw_line.strip()
    if line == 'QUIT':
        break
    if line == 'PING':
        print('PONG', flush=True)
        continue
    observation = json.loads(line)
    request_id = int(observation['id'])
    player = int(observation['player'])
    board = [int(observation['board'][i]) for i in range(81)]
    started = perf_counter()
    if algorithm == 'greedy':
        move, score, nodes = greedy(board, player)
    elif algorithm == 'minimax':
        move, score, nodes = minimax(board, player, parameter)
    else:
        move, score, nodes = monte_carlo(board, player, parameter)
    microseconds = int((perf_counter() - started) * 1000000)
    print(json.dumps({'id': request_id, 'move': move, 'score': score,
                      'nodes': nodes, 'compute_us': microseconds}), flush=True)
