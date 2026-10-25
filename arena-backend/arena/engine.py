"""OTT v2 rules from luat/OTTV2-RULES.md. No clock or I/O in the engine."""
from copy import deepcopy
import hashlib
from pathlib import Path

ENGINE_ID = "ottv2-v1"
ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes() + Path(__file__).with_name("basic_rules.py").read_bytes()).hexdigest()
SIDES = ("red", "blue")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
BASES = {"red": "a1", "blue": "i9"}
DIRECTIONS = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]


def position(square, rows=9, cols=9):
    if (not isinstance(square, str) or not 2 <= len(square) <= 3 or not 'a' <= square[0] < chr(97+cols)
            or not square[1:].isascii() or not square[1:].isdigit() or square[1:].startswith('0') or not 1 <= int(square[1:]) <= rows):
        raise ValueError("Invalid square")
    return rows - int(square[1:]), ord(square[0]) - 97


def square(row, col, rows=9):
    return chr(97 + col) + str(rows - row)


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
    rows, cols = len(board), len(board[0])
    for r, row in enumerate(board):
        for c, piece in enumerate(row):
            if not piece or piece["side"] != side:
                continue
            for dr, dc in DIRECTIONS:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                target = board[nr][nc]
                if target is None or (target["side"] != side and BEATS[piece["kind"]] == target["kind"]):
                    moves.append({"from": square(r, c, rows), "to": square(nr, nc, rows)})
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
    rows, cols = len(state["board"]), len(state["board"][0])
    rules = state.get("basic_rules", {})
    fr, fc = position(action["from"], rows, cols)
    tr, tc = position(action["to"], rows, cols)
    result["board"][tr][tc] = result["board"][fr][fc]
    result["board"][fr][fc] = None
    result["ply"] += 1
    result["to_move"] = other(side)
    remaining = counts(result)[other(side)]
    if rules.get("base_capture", True) and action["to"] == rules.get("bases", BASES)[other(side)]:
        return finish(result, side, "base_capture")
    if sum(remaining.values()) == 0:
        return finish(result, side, "all_pieces_eliminated")
    if sum(n == 0 for n in remaining.values()) >= rules.get("eliminate_types", 1):
        return finish(result, side, "type_eliminated")
    if not legal_moves(result):
        return finish(result, side, "no_legal_moves")
    return result


def observation(state):
    return {"state": deepcopy(state), "legal_moves": legal_moves(state), "counts": counts(state), "bases": state.get("basic_rules", {}).get("bases", BASES),
            **({"basic_rules": state["basic_rules"]} if "basic_rules" in state else {})}
