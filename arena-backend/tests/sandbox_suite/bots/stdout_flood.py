"""In vô hạn ra stdout, không xuống dòng. Kỳ vọng: output_limit."""
import sys


def attack(turn):
    chunk = "x" * 65536
    while True:
        sys.stdout.write(chunk)
        sys.stdout.flush()
