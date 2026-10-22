"""Ghi 24 MiB vào thư mục tạm (TEMP/TMPDIR) thay vì thư mục làm việc. Kỳ vọng: output_limit."""
import os
import tempfile


def attack(turn):
    if turn != 1:
        return False
    block = b"x" * (1024 * 1024)
    folder = tempfile.gettempdir()
    for index in range(3):
        try:
            with open(os.path.join(folder, f"big-{index}.bin"), "wb") as stream:
                for _ in range(8):
                    stream.write(block)
        except OSError:
            return False
    return False
