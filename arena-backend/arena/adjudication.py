"""Versioned match adjudication, separate from immutable OTTv2 move rules."""
from arena.protocol import Fault

LEGACY = {"id": "ottv2-match-v1", "repetition_count": None, "limit_result": "aborted", "startup_failure": "attention"}
CURRENT = {"id": "ottv2-match-v2", "repetition_count": 3, "limit_result": "draw", "startup_failure": "technical_loss"}
BOT_FAULTS = frozenset(("timeout", "crash", "protocol_error", "stale_action", "output_limit"))
DRAW_REASONS = frozenset(("threefold_repetition", "ply_limit", "wall_limit"))


def policy(value=None):
    value = LEGACY if value is None else value
    if value != LEGACY and value != CURRENT:
        raise Fault("provenance_mismatch", "Unsupported match adjudication policy")
    return dict(value)


def position_key(state):
    # Ply counters and clock timestamps don't change the position. In OTTv2
    # there are no castling/en-passant rights or other hidden move state.
    return (state["to_move"], tuple(None if piece is None else (piece["side"], piece["kind"])
                                  for row in state["board"] for piece in row))


def valid_result(result, rules, experimental=False):
    if not isinstance(result, dict) or set(result) != {"winner", "reason"} or not isinstance(result["reason"], str):
        return False
    if result["winner"] in ("red", "blue"):
        reasons = BOT_FAULTS | {"illegal_action", "resignation", "base_capture", "all_pieces_eliminated", "type_eliminated", "no_legal_moves"}
        return result["reason"] in reasons or (experimental and result["reason"] == "control_target") or (result["reason"] == "startup_failure" and rules == CURRENT)
    return result["winner"] is None and rules == CURRENT and result["reason"] in DRAW_REASONS | {"double_startup_failure"}
