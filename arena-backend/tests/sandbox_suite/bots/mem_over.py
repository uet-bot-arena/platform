"""Bot Python xin 600 MiB. Kỳ vọng: crash (MemoryError không bắt)."""
keep = []


def attack(turn):
    keep.append(bytearray(600 * 1024 * 1024))
    return False
