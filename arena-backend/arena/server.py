"""Loopback control plane. Run with Linux Python; see README before exposure."""
import argparse
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from urllib.parse import parse_qs, urlsplit
import zipfile

from arena.config import profile, match_profile
from arena.protocol import Fault, strict_json
from arena.service import Service, TERMINAL
from arena.store import Store

ROOT = Path(__file__).resolve().parents[1]
ERROR_STATUS = {"not_found": 404, "unauthorized": 401, "forbidden": 403, "quota_exceeded": 429,
                "worker_unavailable": 503, "idempotency_conflict": 409, "version_conflict": 409}


class Application:
    def __init__(self, root, tokens=None, dev=False, start_worker=True):
        self.store = Store(root)
        self.service = Service(self.store, start_worker)
        self.dev = dev
        self.tokens = tokens or {}
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.clock_id = secrets.token_hex(16)

    def session(self, team):
        value = secrets.token_urlsafe(32)
        with self.session_lock:
            self.sessions[value] = (team, time.monotonic() + 3600)
        return value

    def team(self, headers):
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            supplied = auth[7:]
            for token, team in self.tokens.items():
                if secrets.compare_digest(supplied.encode(), token.encode()):
                    return team
        try:
            cookies = SimpleCookie(headers.get("Cookie", ""))
            token = cookies["arena_session"].value if "arena_session" in cookies else ""
            with self.session_lock:
                item = self.sessions.get(token)
                if item and item[1] > time.monotonic():
                    return item[0]
        except Exception:
            pass
        raise Fault("unauthorized", "Valid team token/session required")


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_):
            pass  # Do not log Authorization, cookies, uploaded source or query.

        def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
            payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def body(self, limit=16_384):
            if self.headers.get("Transfer-Encoding") or not self.headers.get("Content-Length", "").isdigit():
                raise Fault("invalid_request", "Content-Length required; chunked uploads unsupported")
            size = int(self.headers["Content-Length"])
            if size > limit:
                self.close_connection = True
                raise Fault("invalid_request", "Request too large")
            data = self.rfile.read(size)
            if len(data) != size:
                raise Fault("invalid_request", "Incomplete body")
            return data

        def json_body(self):
            if self.headers.get_content_type() != "application/json":
                raise Fault("invalid_request", "Expected application/json")
            result = strict_json(self.body())
            if not isinstance(result, dict):
                raise Fault("invalid_request", "Expected JSON object")
            return result

        def cookie(self, value):
            return f"arena_session={value}; HttpOnly; SameSite=Strict; Path=/api; Max-Age=3600" + ("; Secure" if os.environ.get("ARENA_COOKIE_SECURE") == "1" else "")

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, post):
            received_ms = time.time_ns() / 1_000_000
            try:
                expected_host = f"127.0.0.1:{self.server.server_port}"
                public_origin = os.environ.get("ARENA_PUBLIC_ORIGIN", "http://" + expected_host)
                if self.headers.get("Host") != urlsplit(public_origin).netloc:
                    raise Fault("forbidden", "Unexpected Host header")
                origin = self.headers.get("Origin")
                if origin and origin != public_origin:
                    raise Fault("forbidden", "Cross-origin request rejected")
                if self.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise Fault("forbidden", "Cross-site request rejected")
                parts = urlsplit(self.path)
                route = parts.path
                query = parse_qs(parts.query)
                if not post and route in ("/", "/app.js", "/style.css"):
                    names = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
                    name, mime = names[route]
                    extra = {"Set-Cookie": self.cookie(app.session("local-practice"))} if route == "/" and app.dev else None
                    return self.send(200, (ROOT / "web" / name).read_bytes(), mime, extra)
                if not post and route == "/api/v1/health":
                    return self.send(200, {"service": "uet-arena-backend-v1", "engine": "ottv2-v1", **app.service.health()})
                if not post and route == "/api/v1/time":
                    return self.send(200, {"clock_id": app.clock_id, "server_received_unix_ms": received_ms,
                                          "server_sent_unix_ms": time.time_ns() / 1_000_000, "display_only": True})
                if post and route == "/api/v1/session":
                    body = self.json_body()
                    token = body.get("token")
                    if not isinstance(token, str):
                        raise Fault("unauthorized", "Team token required")
                    team = app.team({"Authorization": "Bearer " + token})
                    return self.send(200, {"team_id": team, "expires_in_seconds": 3600}, extra={"Set-Cookie": self.cookie(app.session(team))})
                team = app.team(self.headers)
                if post and route == "/api/v1/logout":
                    cookie = SimpleCookie(self.headers.get("Cookie", ""))
                    if "arena_session" in cookie:
                        with app.session_lock:
                            app.sessions.pop(cookie["arena_session"].value, None)
                    return self.send(200, {"ok": True}, extra={"Set-Cookie": "arena_session=; Path=/api; Max-Age=0; HttpOnly; SameSite=Strict"})
                if not post and route == "/api/v1/config":
                    return self.send(200, {"team_id": team, "profile": match_profile().public(), "match_options_ms": [60000, 300000],
                                          "runtimes": ["cpp17"], "protocol_version": 2, "dev_mode": app.dev})
                if not post and route == "/api/v1/starter.zip":
                    output = io.BytesIO()
                    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
                        for name in ("bot.cpp", "json.hpp", "LICENSE.MIT", "README.md"):
                            z.write(ROOT / "sdk" / name, name)
                    return self.send(200, output.getvalue(), "application/zip", {"Content-Disposition": 'attachment; filename="ottv2-cpp-v2.zip"'})
                if route == "/api/v1/submissions":
                    if not post:
                        return self.send(200, {"items": [app.service.public_submission(s) for s in app.store.list("submission", team)]})
                    if self.headers.get_content_type() != "application/zip":
                        raise Fault("invalid_request", "Expected application/zip")
                    raw = self.body(profile().max_zip_bytes)
                    item = app.service.submit(team, self.headers.get("X-Submission-Version", ""),
                                              self.headers.get("X-Arena-Runtime", "cpp17"), raw, self.headers.get("Idempotency-Key"))
                    return self.send(201, app.service.public_submission(item))
                if route in ("/api/v1/benchmarks", "/api/v1/matches") and post:
                    kind = "benchmark" if route.endswith("benchmarks") else "match"
                    item = app.service.enqueue(team, kind, self.json_body(), self.headers.get("Idempotency-Key"))
                    return self.send(202, item)
                if route == "/api/v1/jobs" and not post:
                    return self.send(200, {"items": app.store.list("job", team)})
                match = re.fullmatch(r"/api/v1/jobs/([0-9a-f]{32})(?:/(events|report))?", route)
                if match and not post:
                    ident, subroute = match.groups()
                    job = app.store.get(ident, team, "job")
                    if subroute == "report":
                        return self.send(200, job.get("report"), extra={"Content-Disposition": f'attachment; filename="{ident}.json"'})
                    if subroute == "events":
                        try:
                            after = int(self.headers.get("Last-Event-ID") or query.get("after", ["0"])[0])
                        except ValueError as exc:
                            raise Fault("invalid_request", "Invalid event cursor") from exc
                        if after < 0:
                            raise Fault("invalid_request", "Negative event cursor")
                        if "text/event-stream" in self.headers.get("Accept", ""):
                            return self.sse(ident, after)
                        return self.send(200, {"items": app.store.events(ident, after)})
                    return self.send(200, job)
                raise Fault("not_found", "Unknown endpoint")
            except Fault as exc:
                self.close_connection = True
                self.send(ERROR_STATUS.get(exc.code, 400), {"error": {"code": exc.code, "detail": exc.detail}})
            except ValueError as exc:
                self.send(400, {"error": {"code": "invalid_request", "detail": str(exc)}})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except Exception:
                self.send(500, {"error": {"code": "internal_error", "detail": "See controller diagnostics"}})

        def sse(self, ident, cursor):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            until = time.monotonic() + 60
            while time.monotonic() < until and not app.service.stop.is_set():
                events = app.store.events(ident, cursor)
                for event in events:
                    self.wfile.write(f"id: {event['seq']}\nevent: arena\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode())
                    cursor = event["seq"]
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                if app.store.get(ident)["status"] in TERMINAL and not app.store.events(ident, cursor):
                    break
                app.service.stop.wait(1)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--data", type=Path, default=ROOT / ".state")
    parser.add_argument("--dev", action="store_true", help="Loopback-only local workspace session; no real team auth")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("Run this controller under Linux/WSL Python")
    tokens = json.loads(os.environ.get("ARENA_TEAM_TOKENS", "{}"))
    if not isinstance(tokens, dict) or any(not isinstance(k, str) or len(k) < 24 or not isinstance(v, str) or not v or len(v) > 64 for k, v in tokens.items()):
        parser.error("ARENA_TEAM_TOKENS must map random tokens (24+ characters) to team IDs")
    if not tokens and not args.dev:
        parser.error("Configure ARENA_TEAM_TOKENS or explicitly select --dev")
    # Prevent two controllers serving the same queue or racing artifacts.
    import fcntl
    args.data.mkdir(parents=True, exist_ok=True)
    lease = (args.data / "controller.lock").open("a")
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Another controller owns this data directory")
    app = Application(args.data, tokens, args.dev)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(app))
    server.daemon_threads = True
    def shutdown(*_):
        app.service.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(f"Control UI: http://127.0.0.1:{args.port} · {'LOCAL DEV SESSION' if args.dev else 'TEAM AUTH'}", flush=True)
    try:
        server.serve_forever()
    finally:
        app.service.stop.set()
        app.service.worker_thread.join(timeout=12)
        server.server_close()
        lease.close()


if __name__ == "__main__":
    main()
