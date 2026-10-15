"""OTT v2 rules from docs/OTTV2-RULES.md. No clock or I/O in the engine."""
from copy import deepcopy
import hashlib
from pathlib import Path

ENGINE_ID = "ottv2-v1"
ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SIDES = ("red", "blue")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
BASES = {"red": "a1", "blue": "i9"}
DIRECTIONS = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]


def position(square):
    if not isinstance(square, str) or len(square) != 2 or square[0] not in "abcdefghi" or square[1] not in "123456789":
        raise ValueError("Invalid square")
    return 9 - int(square[1]), ord(square[0]) - 97


def square(row, col):
    return chr(97 + col) + str(9 - row)


def other(side):
    return "blue" if side == "red" else "red"


def initial_state():
    board = [[None for _ in range(9)] for _ in range(9)]
    placements = {
        "blue": [("b9", "rock"), ("d9", "paper"), ("f9", "scissors"), ("h9", "rock"),
                 ("a8", "paper"), ("c8", "scissors"), ("e8", "rock"), ("g8", "paper"), ("i8", "scissors")],
        "red": [("a2", "scissors"), ("c2", "paper"), ("e2", "rock"), ("g2", "scissors"),
                ("i2", "paper"), ("b1", "rock"), ("d1", "scissors"), ("f1", "paper"), ("h1", "rock")],
    }
    for side, pieces in placements.items():
        for cell, kind in pieces:
            r, c = position(cell)
            board[r][c] = {"side": side, "kind": kind}
    return {"board": board, "to_move": "red", "ply": 0, "result": None}


def counts(state):
    result = {s: {kind: 0 for kind in BEATS} for s in SIDES}
    for row in state["board"]:
        for p in row:
            if p:
                result[p["side"]][p["kind"]] += 1
    return result


def legal_moves(state, side=None):
    if state["result"]:
        return []
    side = side or state["to_move"]
    board, moves = state["board"], []
    for r, row in enumerate(board):
        for c, piece in enumerate(row):
            if not piece or piece["side"] != side:
                continue
            for dr, dc in DIRECTIONS:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < 9 and 0 <= nc < 9):
                    continue
                target = board[nr][nc]
                if target is None or (target["side"] != side and BEATS[piece["kind"]] == target["kind"]):
                    moves.append({"from": square(r, c), "to": square(nr, nc)})
    return moves


def finish(state, winner, reason):
    state["result"] = {"winner": winner, "reason": reason}
    return state


def apply(state, action):
    if state["result"]:
        raise ValueError("Match already finished")
    if not isinstance(action, dict):
        raise ValueError("Action must be an object")
    side = state["to_move"]
    result = deepcopy(state)
    if set(action) == {"resign"} and action["resign"] is True:
        return finish(result, other(side), "resignation")
    if set(action) != {"from", "to"} or action not in legal_moves(state):
        raise ValueError("Illegal move")
    fr, fc = position(action["from"])
    tr, tc = position(action["to"])
    result["board"][tr][tc] = result["board"][fr][fc]
    result["board"][fr][fc] = None
    result["ply"] += 1
    result["to_move"] = other(side)
    remaining = counts(result)[other(side)]
    if action["to"] == BASES[other(side)]:
        return finish(result, side, "base_capture")
    if sum(remaining.values()) == 0:
        return finish(result, side, "all_pieces_eliminated")
    if 0 in remaining.values():
        return finish(result, side, "type_eliminated")
    if not legal_moves(result):
        return finish(result, side, "no_legal_moves")
    return result


def observation(state):
    return {"state": deepcopy(state), "legal_moves": legal_moves(state), "counts": counts(state), "bases": BASES}
