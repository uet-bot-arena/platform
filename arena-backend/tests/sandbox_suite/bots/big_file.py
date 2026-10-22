"""Ghi 24 MiB vào thư mục làm việc (3 file x 8 MiB, mỗi file dưới giới hạn 16 MiB/file). Kỳ vọng: output_limit."""


def attack(turn):
    if turn != 1:
        return False
    block = b"x" * (1024 * 1024)
    for index in range(3):
        try:
            with open(f"big-{index}.bin", "wb") as stream:
                for _ in range(8):
                    stream.write(block)
        except OSError:
            return False
    return False
