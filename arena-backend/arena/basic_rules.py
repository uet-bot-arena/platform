"""Validated, immutable SDK v2 tournament rules; add-on SDK v3 stays separate."""
from copy import deepcopy
import hashlib
import json

from arena import engine

# Classic OTTv2 order along the zigzag: a2 scissors, b1 rock, c2 paper, ...
CYCLE = ("scissors", "rock", "paper")
PIECES_PER_SIDE = 9


def rotate(cell, rows, cols):
    r, c = engine.position(cell, rows, cols)
    return engine.square(rows - 1 - r, cols - 1 - c, rows)


def auto_placement(rows, cols, bases):
    """Generalised classic layout: 9 pieces per side (3 of each kind) on the two home ranks.

    Red uses the classic zigzag (rank 2 on even offsets, rank 1 on odd ones), centred on wide
    boards; narrow boards fill the second cell of the central columns. Blue is the 180° rotation
    of red, so both sides see the same position. 9x9 reproduces engine.initial_state() exactly.
    """
    used = 9 if cols >= 9 else cols
    offset = (cols - used) // 2
    home = lambda col, rank: engine.square(rows - rank, col, rows)
    planned = [(offset + k, 2 if k % 2 == 0 else 1, CYCLE[k % 3]) for k in range(used)]
    counts = {kind: sum(p[2] == kind for p in planned) for kind in CYCLE}
    centre = (cols - 1) / 2
    for col in sorted(range(cols), key=lambda c: (abs(c - centre), c)):
        if len(planned) == PIECES_PER_SIDE:
            break
        rank = next((3 - p[1] for p in planned if p[0] == col), 2)
        kind = min(CYCLE, key=lambda k: (counts[k], CYCLE.index(k)))
        planned.append((col, rank, kind))
        counts[kind] += 1
    taken, pieces = set(bases.values()), []
    for col, rank, kind in planned:
        options = [home(col, rank), home(col, 3 - rank)] + [home(c, r) for c in sorted(range(cols), key=lambda c: (abs(c - col), c)) for r in (2, 1)]
        cell = next((x for x in options if x not in taken), None)
        if cell is None:
            raise ValueError("Not enough free home squares for the automatic placement")
        taken.add(cell)
        pieces.append({"square": cell, "side": "red", "kind": kind})
    for piece in list(pieces):
        cell = rotate(piece["square"], rows, cols)
        if cell in taken:
            raise ValueError("Bases are not symmetric with the automatic placement; enter the placement manually")
        taken.add(cell)
        pieces.append({"square": cell, "side": "blue", "kind": piece["kind"]})
    return pieces


def normalize(value):
    if not isinstance(value, dict) or set(value) - {"rows", "cols", "bases", "eliminate_types", "base_capture", "placement"}:
        raise ValueError("Unknown basic rule fields")
    rows, cols = value.get("rows", 9), value.get("cols", 9)
    if type(rows) is not int or type(cols) is not int or not 7 <= rows <= 15 or not 7 <= cols <= 15:
        raise ValueError("Board rows and cols must be integers in 7..15")
    bases = value.get("bases", {"red": "a1", "blue": chr(96+cols)+str(rows)})
    if not isinstance(bases, dict) or set(bases) != set(engine.SIDES) or bases["red"] == bases["blue"]:
        raise ValueError("Two distinct bases required")
    for cell in bases.values():
        engine.position(cell, rows, cols)
    k, capture = value.get("eliminate_types", 1), value.get("base_capture", True)
    if type(k) is not int or k not in (1, 2) or type(capture) is not bool:
        raise ValueError("eliminate_types must be 1 or 2; base_capture must be boolean")
    placement = value.get("placement", "auto")
    if placement == "auto":
        auto_placement(rows, cols, bases)
    else:
        if not isinstance(placement, list) or not 6 <= len(placement) <= rows*cols-2:
            raise ValueError("Manual placement must contain every side/type and leave bases empty")
        seen, kinds = set(), {s:set() for s in engine.SIDES}
        for piece in placement:
            if (not isinstance(piece, dict) or set(piece) != {"square", "side", "kind"}
                    or not all(isinstance(v,str) for v in piece.values()) or piece["side"] not in kinds or piece["kind"] not in engine.BEATS):
                raise ValueError("Invalid piece: square, side and kind required")
            cell = piece["square"]
            engine.position(cell, rows, cols)
            if cell in seen or cell in bases.values():
                raise ValueError("Overlapping piece or occupied base")
            seen.add(cell); kinds[piece["side"]].add(piece["kind"])
        if any(len(ks) != 3 for ks in kinds.values()):
            raise ValueError("Each side must start with all three piece types")
        board = [[None for _ in range(cols)] for _ in range(rows)]
        for piece in placement:
            r, c = engine.position(piece['square'], rows, cols)
            board[r][c] = {'side':piece['side'], 'kind':piece['kind']}
        state = {'board':board, 'to_move':'red', 'ply':0, 'result':None}
        if any(not engine.legal_moves(state, side) for side in engine.SIDES):
            raise ValueError("Each side must have a legal move in the initial placement")
    return {"rows":rows,"cols":cols,"bases":deepcopy(bases),"eliminate_types":k,"base_capture":capture,"placement":deepcopy(placement)}


DEFAULT = {"rows":9,"cols":9,"bases":dict(engine.BASES),"eliminate_types":1,"base_capture":True,"placement":"auto"}


def snapshot(value):
    definition = normalize(value)
    return {"definition":definition,"hash":hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def check_playable(definition):
    """Reject organiser input where either side wins on its very first move.

    Called only where rules are created (competition, benchmark, practice request); the
    per-turn provider() path skips it because the definition was already accepted.
    """
    rules = Rules(definition)
    state = rules.initial_state()
    for side in engine.SIDES:
        probe = {**state, "to_move": side}
        for move in engine.legal_moves(probe):
            if (engine.apply(probe, move)["result"] or {}).get("winner") == side:
                raise ValueError(f"Initial position lets {side} win on its first move ({move['from']}-{move['to']}); move the bases or pieces")
    return definition


def validated_snapshot(value):
    pack = snapshot(value)
    check_playable(pack["definition"])
    return pack


class Rules:
    def __init__(self, value):
        self.definition = normalize(value)

    def initial_state(self):
        d = self.definition
        if d == DEFAULT:
            return engine.initial_state()
        rows, cols = d["rows"], d["cols"]
        board = [[None for _ in range(cols)] for _ in range(rows)]
        pieces = auto_placement(rows, cols, d["bases"]) if d["placement"] == "auto" else d["placement"]
        for piece in pieces:
            r,c = engine.position(piece["square"], rows, cols)
            board[r][c] = {"side":piece["side"],"kind":piece["kind"]}
        return {"board":board,"to_move":"red","ply":0,"result":None,"basic_rules":deepcopy(d)}

    legal_moves = staticmethod(engine.legal_moves)
    apply = staticmethod(engine.apply)
    observation = staticmethod(engine.observation)


def provider(pack):
    if not pack:
        return engine
    if snapshot(pack.get("definition"))["hash"] != pack.get("hash"):
        raise ValueError("Basic rules hash mismatch")
    return Rules(pack["definition"])
