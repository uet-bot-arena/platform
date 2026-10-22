"""In vô hạn ra stderr. Kỳ vọng: output_limit."""
import sys


def attack(turn):
    chunk = "x" * 65536 + "\n"
    while True:
        sys.stderr.write(chunk)
        sys.stderr.flush()
