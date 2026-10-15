from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    data: Path = field(default_factory=lambda: Path(os.environ.get("ARENA_API_DATA", ".platform-state")).resolve())
    origin: str = field(default_factory=lambda: os.environ.get("ARENA_API_ORIGIN", "http://127.0.0.1:4181"))
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("ARENA_API_COOKIE_SECURE", "0") == "1")
    session_seconds: int = 3600
    trusted_proxies: tuple[str, ...] = field(default_factory=lambda: tuple(filter(None, os.environ.get("ARENA_TRUSTED_PROXIES", "").split(","))))
    connection_limit: int = field(default_factory=lambda: int(os.environ.get("ARENA_CONNECTION_LIMIT", "1024")))
    # Sync endpoints and Argon2 waits run in this pool; the AnyIO default of 40 threads makes a
    # login burst block uploads and public reads.
    thread_limit: int = field(default_factory=lambda: int(os.environ.get("ARENA_THREAD_LIMIT", "256")))
    hash_workers: int = field(default_factory=lambda: int(os.environ.get("ARENA_HASH_WORKERS", "4")))
    ui_root: Path = field(default_factory=lambda: Path(os.environ.get("ARENA_UI_ROOT",
        str(Path(__file__).resolve().parents[2] / "uet-bot-arena" / "dist"))).resolve())

    def __post_init__(self):
        import ipaddress
        for network in self.trusted_proxies:
            ipaddress.ip_network(network.strip())
        if not 40 <= self.thread_limit <= 4096 or not 1 <= self.hash_workers <= 32:
            raise ValueError("ARENA_THREAD_LIMIT must be 40..4096 and ARENA_HASH_WORKERS 1..32")
        if not 256 <= self.connection_limit <= 16384:
            raise ValueError("ARENA_CONNECTION_LIMIT must be 256..16384")
        parts = urlsplit(self.origin)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError("ARENA_API_ORIGIN must be an exact origin without a trailing slash")
        if parts.hostname not in ("127.0.0.1", "localhost", "[::1]", "::1") and (parts.scheme != "https" or not self.cookie_secure):
            raise ValueError("Public origins require HTTPS and ARENA_API_COOKIE_SECURE=1")
        if not 300 <= self.session_seconds <= 86400:
            raise ValueError("Session TTL out of range")


class Lease:
    """Single process owns the controller queue and its data directory."""
    def __init__(self, root):
        import fcntl
        Path(root).mkdir(parents=True, exist_ok=True)
        self.file = (Path(root) / "controller.lock").open("a")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError("Another controller owns this data directory") from None

    def close(self):
        self.file.close()
