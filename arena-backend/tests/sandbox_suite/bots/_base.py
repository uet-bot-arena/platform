
# ---- shared OTTv2 v2 loop (appended after each attack); stdlib only ----
import json as _json
import sys as _sys


def _choose(payload):
    state, moves = payload["state"], payload["legal_moves"]
    if not moves:
        return {"resign": True}
    rows = len(state["board"])
    side = state["to_move"]
    base = payload["bases"]["blue" if side == "red" else "red"]
    base_row, base_col = rows - int(base[1:]), ord(base[0]) - 97

    def score(move):
        cell = move["to"]
        row, col = rows - int(cell[1:]), ord(cell[0]) - 97
        return (10000 if cell == base else 0) + (50 if state["board"][row][col] else 0) \
            - max(abs(row - base_row), abs(col - base_col))
    return max(moves, key=score)


def _main():
    turn = 0
    for line in _sys.stdin:
        message = _json.loads(line)
        result = {"protocol_version": 2, "match_id": message["match_id"]}
        if message["type"] == "init":
            result["type"] = "ready"
        else:
            turn += 1
            if attack(turn):
                print("BREACH", flush=True)
                continue
            result.update(type="action", turn_id=message["turn_id"], state_version=message["state_version"],
                          payload=RESIGN if RESIGN else _choose(message["payload"]))
        print(_json.dumps(result, separators=(",", ":")), flush=True)
        after_reply(turn)


try:
    RESIGN
except NameError:
    RESIGN = None
try:
    after_reply
except NameError:
    def after_reply(turn):
        return None
_main()
