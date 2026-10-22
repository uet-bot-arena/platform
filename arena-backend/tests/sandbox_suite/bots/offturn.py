"""Tính toán ngoài lượt: trả lời rồi bận 400 ms. Đo CPU giữa hai lượt; nếu không bị đóng băng thì BREACH. Kỳ vọng: ok."""
import time

state = {}


def after_reply(turn):
    state["cpu"] = time.process_time()
    end = time.monotonic() + 0.4
    while time.monotonic() < end:
        pass


def attack(turn):
    if turn == 2:
        return time.process_time() - state["cpu"] > 0.15
    return False
