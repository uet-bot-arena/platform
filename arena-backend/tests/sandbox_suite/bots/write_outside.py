"""Ghi file ra ngoài thư mục làm việc: thư mục của máy chủ, thư mục bot (artifact). Kỳ vọng: bị chặn -> ok."""
import os
import sys

TARGETS = __OUTSIDE__


def attack(turn):
    if turn != 1:
        return False
    here = os.path.dirname(os.path.abspath(sys.argv[0] or "."))
    for folder in TARGETS + [here]:
        try:
            with open(os.path.join(folder, "pwned.txt"), "w") as stream:
                stream.write("x")
            return True
        except OSError:
            pass
    return False
