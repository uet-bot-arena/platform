"""Bot Python xin 100 MiB (trên ngưỡng chung 70 MiB). Kỳ vọng: crash trên mọi nền tảng."""
keep = []


def attack(turn):
    if turn == 1:
        keep.append(bytearray(100 * 1024 * 1024))
    return False
