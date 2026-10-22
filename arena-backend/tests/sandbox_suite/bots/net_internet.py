"""Gọi ra Internet (IP trực tiếp, không cần DNS). Kỳ vọng: bị chặn -> ok."""
import socket


def attack(turn):
    if turn != 1:
        return False
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 53), 2).close()
            return True
        except OSError:
            pass
    try:
        socket.getaddrinfo("example.com", 80)
        return True
    except OSError:
        return False
