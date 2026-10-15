"""OTTv2 starter (giao thức v2), Python 3.11 / PyPy, chỉ thư viện chuẩn.

Chỉ cần sửa hàm choose(). Phần main() đã lo việc đọc/ghi JSON đúng giao thức.
"""
import json
import sys


def choose(payload):
    """Trả về một nước trong payload["legal_moves"], ví dụ {"from": "a2", "to": "a3"}."""
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
        value = 10000 if cell == base else 0                     # vào căn cứ đối phương: thắng
        value += 50 if state["board"][row][col] else 0            # ô có quân địch: nước ăn quân
        value -= max(abs(row - base_row), abs(col - base_col))    # càng gần căn cứ địch càng tốt
        return value

    return max(moves, key=score)


def main():
    for line in sys.stdin:
        message = json.loads(line)
        if message["protocol_version"] != 2:
            raise ValueError("This starter uses SDK v2")
        result = {"protocol_version": 2, "match_id": message["match_id"]}
        if message["type"] == "init":
            result["type"] = "ready"
        else:
            result.update(type="action", turn_id=message["turn_id"], state_version=message["state_version"],
                          payload=choose(message["payload"]))
        print(json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
