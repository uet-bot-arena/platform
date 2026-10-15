"""Shared helpers for the load lab: bot sources, ZIPs, a real arena_api server, multi-IP clients.

Run from arena-backend with the project venv on Linux/WSL with Docker and the worker images.
Data goes to test-results/load-lab/ (ignored by Git); the real platform data is never touched.
"""
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid
import zipfile

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]                                   # arena-backend
HOME_LAB = Path(os.environ.get("ARENA_LAB_DATA", REPO / "test-results" / "load-lab"))
RESULTS = HOME_LAB / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True

ADMIN_PW = "lab-only-admin-password"
FAULTS = {"crash": 1, "illegal": 2, "timeout": 3, "spam": 4, "memhog": 5, "slow_start": 6, "sneaky": 7, "stale": 8}
# Valid bots, roughly strongest first.
ALGORITHMS = ["ab50", "ab20", "ab_d2", "onestep", "greedy", "random"]


def alphabeta(think_ms, max_depth=8):
    src = (REPO / "examples" / "alphabeta" / "bot.cpp").read_text()
    old = "constexpr int THINK_MS = 250, MAX_DEPTH = 8, QDEPTH = 2;"
    assert old in src, "alphabeta example changed"
    return src.replace(old, f"constexpr int THINK_MS = {think_ms}, MAX_DEPTH = {max_depth}, QDEPTH = 2;")


def bot_source(kind):
    """kind: random | greedy | onestep | abNN (alpha-beta NN ms) | ab_d2 | compile_error | <fault>."""
    if kind == "random":
        return (LAB / "bots" / "random.cpp").read_text()
    if kind == "greedy":
        return (REPO / "sdk" / "bot.cpp").read_text()          # the C++ starter
    if kind == "onestep":
        return (LAB / "bots" / "onestep.cpp").read_text()
    if kind == "ab_d2":
        return alphabeta(30, max_depth=2)
    if kind.startswith("ab") and kind[2:].isdigit():
        return alphabeta(int(kind[2:]))
    if kind == "compile_error":
        return "#include <iostream>\nint main() { std::cout << \"oops\" return 0; }\n"
    if kind in FAULTS:
        return f"#define FAULT {FAULTS[kind]}\n" + (LAB / "bots" / "faulty.cpp").read_text()
    raise ValueError(kind)


def zipped(source, salt="", name="bot.cpp"):
    """Flat ZIP with one source file; salt makes each team's source distinct (no shared build cache)."""
    out = io.BytesIO()
    comment = "#" if name.endswith(".py") else "//"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, (f"{comment} submission: {salt}\n" if salt else "") + source)
    return out.getvalue()


def team_ip(i):
    """One loopback address per team: 127.1.x.y (Linux routes all of 127/8 to lo)."""
    return f"127.1.{i // 250}.{i % 250 + 1}"


def start_server(data_dir, limit_concurrency=None):
    """Start arena_api like `python -m arena_api serve` (one process, configured connection limit)."""
    import uvicorn
    from arena.store import Store
    from arena_api.accounts import Accounts
    from arena_api.app import create_app
    from arena_api.settings import Settings
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir, recover_jobs=False)
    Accounts(store).bootstrap_admin("organizer", ADMIN_PW)
    store.db.close()
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    settings = Settings(data=data_dir, origin=f"http://127.0.0.1:{sock.getsockname()[1]}")
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning", proxy_headers=False,
                                           limit_concurrency=limit_concurrency or settings.connection_limit,
                                           timeout_keep_alive=5))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    end = time.monotonic() + 30
    while not server.started and time.monotonic() < end:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    return settings.origin, server, thread


def stop_server(server, thread):
    server.should_exit = True
    thread.join(timeout=600)


class Api:
    """HTTP client bound to one source IP; adds Idempotency-Key/CSRF; counts 429/503."""

    def __init__(self, origin, ip=None, timeout=180):
        import httpx
        transport = httpx.HTTPTransport(local_address=ip) if ip else httpx.HTTPTransport()
        self.http = httpx.Client(base_url=origin, timeout=timeout, transport=transport)
        self.ip = ip
        self.stats = {"429": 0, "503": 0}

    def call(self, method, path, expected=(200,), retry_503=True, **kw):
        headers = dict(kw.pop("headers", {}))
        if method == "POST":
            headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
            if "json" not in kw and "content" not in kw:
                kw["json"] = {}
        while True:
            r = self.http.request(method, "/api/v1" + path, headers=headers, **kw)
            if r.status_code == 503 and retry_503 and "worker_unavailable" not in r.text:
                self.stats["503"] += 1          # Uvicorn admission (connection limit) rejected it
                time.sleep(0.5)
                continue
            if r.status_code == 429:
                self.stats["429"] += 1
            if r.status_code not in expected:
                raise ApiError(path, r.status_code, r.text[:600])
            return r

    def json(self, method, path, expected=(200,), **kw):
        return self.call(method, path, expected, **kw).json()

    def login(self, username, password):
        data = self.json("POST", "/auth/login", json={"username": username, "password": password})
        self.http.headers["X-CSRF-Token"] = data["csrf_token"]
        return data


class ApiError(Exception):
    def __init__(self, path, status, body):
        super().__init__(f"{path} -> {status}: {body}")
        self.path, self.status, self.body = path, status, body


def save(name, data):
    path = RESULTS / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
