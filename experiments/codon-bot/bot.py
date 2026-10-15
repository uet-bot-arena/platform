"""Same source for CPython and Codon; synthetic 9x9 connect-five, NOT OTTv2.

Protocol: id|algorithm|player|parameter|81 comma-separated integers
Response: id|move|score|visited_nodes|compute_microseconds
No third-party imports, Python interoperability, decorators, or native extensions.
"""
import sys
from time import perf_counter

SIZE = 9
CELLS = 81
WIN = 1000000
WEIGHTS = [0, 1, 8, 80, 800, WIN]
DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


def make_windows():
    windows = []
    for row in range(SIZE):
        for col in range(SIZE):
            for dr, dc in DIRECTIONS:
                end_row = row + 4 * dr
                end_col = col + 4 * dc
                if 0 <= end_row < SIZE and 0 <= end_col < SIZE:
                    windows.append([(row + k * dr) * SIZE + col + k * dc for k in range(5)])
    return windows


WINDOWS = make_windows()


def evaluate(board, player):
    score = 0
    for window in WINDOWS:
        own = 0
        enemy = 0
        for cell in window:
            value = board[cell]
            if value == player:
                own += 1
            elif value == -player:
                enemy += 1
        if enemy == 0:
            if own == 5:
                return WIN
            score += WEIGHTS[own]
        if own == 0:
            if enemy == 5:
                return -WIN
            score -= WEIGHTS[enemy]
    return score


def won(board, cell, player):
    row = cell // SIZE
    col = cell % SIZE
    for dr, dc in DIRECTIONS:
        count = 1
        for sign in [-1, 1]:
            rr = row + sign * dr
            cc = col + sign * dc
            while 0 <= rr < SIZE and 0 <= cc < SIZE and board[rr * SIZE + cc] == player:
                count += 1
                rr += sign * dr
                cc += sign * dc
        if count >= 5:
            return True
    return False


def candidates(board, limit):
    # Deterministic ordering and branch cap, identical in all three runtimes.
    ranked = []
    for cell in range(CELLS):
        if board[cell] != 0:
            continue
        row = cell // SIZE
        col = cell % SIZE
        neighbours = 0
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                rr = row + dr
                cc = col + dc
                if 0 <= rr < SIZE and 0 <= cc < SIZE and board[rr * SIZE + cc] != 0:
                    neighbours += 1
        priority = neighbours * 20 - abs(row - 4) - abs(col - 4)
        if won(board, cell, 1) or won(board, cell, -1):
            priority += 10000
        ranked.append((-priority, cell))
    ranked.sort()
    return [pair[1] for pair in ranked[:limit]]


def greedy(board, player):
    best_move = -1
    best_score = -2 * WIN
    nodes = 0
    for move in candidates(board, 12):
        board[move] = player
        score = evaluate(board, player)
        board[move] = 0
        nodes += 1
        if score > best_score:
            best_move = move
            best_score = score
    return best_move, best_score, nodes


def negamax(board, player, depth, alpha, beta):
    current = evaluate(board, player)
    if depth == 0 or abs(current) == WIN:
        return current, 1
    moves = candidates(board, 8)
    if len(moves) == 0:
        return 0, 1
    best = -2 * WIN
    nodes = 1
    for move in moves:
        board[move] = player
        child_score, child_nodes = negamax(board, -player, depth - 1, -beta, -alpha)
        score = -child_score
        board[move] = 0
        nodes += child_nodes
        if score > best:
            best = score
        if score > alpha:
            alpha = score
        if alpha >= beta:
            break
    return best, nodes


def minimax(board, player, depth):
    best_move = -1
    best_score = -2 * WIN
    nodes = 0
    alpha = -2 * WIN
    for move in candidates(board, 8):
        board[move] = player
        child_score, child_nodes = negamax(board, -player, depth - 1, -2 * WIN, -alpha)
        score = -child_score
        board[move] = 0
        nodes += child_nodes
        if score > best_score:
            best_move = move
            best_score = score
        if score > alpha:
            alpha = score
    return best_move, best_score, nodes


def monte_carlo(board, player, rollouts):
    # Flat Monte Carlo, round-robin allocation; deliberately NOT full tree MCTS.
    moves = candidates(board, 8)
    if len(moves) == 0:
        return -1, 0, 0
    totals = [0 for move in moves]
    counts = [0 for move in moves]
    seed = 1234567
    nodes = 0
    for trial in range(rollouts):
        index = trial % len(moves)
        move = moves[index]
        work = board.copy()
        work[move] = player
        nodes += 1
        value = 0
        if won(work, move, player):
            value = 1
        else:
            empty = [cell for cell in range(CELLS) if work[cell] == 0]
            turn = -player
            for ply in range(min(24, len(empty))):
                # Values stay within signed 64-bit in Python/Codon/C++.
                seed = (seed * 1103515245 + 12345) & 2147483647
                chosen = seed % len(empty)
                cell = empty[chosen]
                empty[chosen] = empty[-1]
                empty.pop()
                work[cell] = turn
                nodes += 1
                if won(work, cell, turn):
                    value = 1 if turn == player else -1
                    break
                turn = -turn
            if value == 0:
                heuristic = evaluate(work, player)
                value = 1 if heuristic > 0 else -1 if heuristic < 0 else 0
        totals[index] += value
        counts[index] += 1
    best = 0
    for index in range(1, len(moves)):
        if totals[index] * counts[best] > totals[best] * counts[index]:
            best = index
    return moves[best], totals[best], nodes


def run():
    print('READY|1', flush=True)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if line == 'QUIT':
            break
        if line == 'PING':
            print('PONG', flush=True)
            continue
        fields = line.split('|')
        if len(fields) != 5:
            print('ERROR|protocol', flush=True)
            continue
        request_id = fields[0]
        algorithm = fields[1]
        player = int(fields[2])
        parameter = int(fields[3])
        board = [int(value) for value in fields[4].split(',')]
        if len(board) != CELLS or player not in [-1, 1] or any(value not in [-1, 0, 1] for value in board):
            print('ERROR|board', flush=True)
            continue
        started = perf_counter()
        if all(value != 0 for value in board):
            move, score, nodes = -1, 0, 0
        elif algorithm == 'greedy':
            move, score, nodes = greedy(board, player)
        elif algorithm == 'minimax' and 1 <= parameter <= 5:
            move, score, nodes = minimax(board, player, parameter)
        elif algorithm == 'mc' and 8 <= parameter <= 4096:
            move, score, nodes = monte_carlo(board, player, parameter)
        else:
            print('ERROR|algorithm', flush=True)
            continue
        microseconds = int((perf_counter() - started) * 1000000)
        print(f'{request_id}|{move}|{score}|{nodes}|{microseconds}', flush=True)


if __name__ == '__main__':
    run()
