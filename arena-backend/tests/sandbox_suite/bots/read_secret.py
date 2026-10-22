"""Đọc file bí mật của người dùng và liệt kê thư mục home. Kỳ vọng: bị chặn -> ok."""
import os

SECRET = __SECRET__
HOMES = __HOMES__


def attack(turn):
    if turn != 1:
        return False
    try:
        with open(SECRET, "rb") as stream:
            if stream.read():
                return True
    except OSError:
        pass
    for path in HOMES:
        try:
            if os.listdir(path):
                return True
        except OSError:
            pass
    return False
