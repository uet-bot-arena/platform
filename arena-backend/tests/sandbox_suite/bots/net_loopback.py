"""Gọi về máy chủ qua 127.0.0.1 (cổng nghe do bộ thử mở, và cổng hub 4181). Kỳ vọng: bị chặn -> ok.

Trên Windows gói tin loopback bị bỏ (không bị từ chối), nên chờ ngắn để không hết giờ lượt.
"""
import socket

PORTS = (__PORT__, 4181)


def attack(turn):
    if turn != 1:
        return False
    for port in PORTS:
        try:
            socket.create_connection(("127.0.0.1", port), 0.4).close()
            return True
        except OSError:
            pass
    return False
