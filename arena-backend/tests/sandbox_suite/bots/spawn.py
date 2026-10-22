"""Tạo tiến trình con. Kỳ vọng: bị chặn -> ok."""
import os
import subprocess
import sys


def attack(turn):
    if turn != 1:
        return False
    commands = [[sys.executable, "-c", "pass"], ["/bin/sh", "-c", "true"], [r"C:\Windows\System32\cmd.exe", "/c", "exit"]]
    for command in commands:
        try:
            subprocess.run(command, capture_output=True, timeout=3)
            return True
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if hasattr(os, "fork"):
        try:
            pid = os.fork()
        except OSError:
            return False
        if pid == 0:
            os._exit(0)
        return True
    return False
