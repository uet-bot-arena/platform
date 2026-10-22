"""Bot Python dùng 60 MiB (dưới ngưỡng chung 70 MiB). Kỳ vọng: ok trên mọi nền tảng."""
keep = []


def attack(turn):
    if turn == 1:
        keep.append(bytearray(60 * 1024 * 1024))
    return False
