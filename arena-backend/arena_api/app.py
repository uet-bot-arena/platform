"""ASGI composition root. Use one Uvicorn process per data directory."""
import asyncio
from contextlib import asynccontextmanager
import html
import json
import logging
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from arena.protocol import Fault
from arena.service import Service, TERMINAL
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.settings import Lease, Settings
from arena_api import auth_routes, admin_routes, arena_routes, competition_routes, ui_routes, ruleset_routes, sample_bots, grader_routes, email_routes
from arena_api.competitions import Competitions
from arena_api.dependencies import SPECTATOR_PATHS

ERROR_STATUS = {"not_found": 404, "unauthorized": 401, "forbidden": 403, "password_change_required": 403,
                "quota_exceeded": 429, "rate_limited": 429, "worker_unavailable": 503,
                "idempotency_conflict": 409, "version_conflict": 409, "account_conflict": 409, "admin_conflict": 409,
                "registration_conflict": 409, "report_unavailable": 409, "competition_conflict": 409,
                "qualification_required": 409, "provenance_mismatch": 409, "ruleset_conflict": 409,
                "experimental_disabled": 409, "stale_lease": 409, "email_unavailable": 503, "github_unavailable": 503}
GRADER_PREFIX = "/api/v1/grader/"
MIB = 1024 * 1024


def body_limit(method, path):
    """(bytes, binary?) accepted for a mutation on this path."""
    if path == "/api/v1/submissions":
        return 10 * MIB, True  # LAN ZIP; online handler still rejects this channel
    if path == "/api/v1/admin/sample-bots" or path.startswith("/api/v1/admin/teams/") and path.endswith("/submissions"):
        return 10 * MIB, True
    if method == "PUT" and path.startswith(GRADER_PREFIX + "artifacts/"):
        return 17 * MIB, True  # compiled bot (<= 16 MiB)
    if path.startswith(GRADER_PREFIX) and path.endswith("/result-replay"):
        return 8 * MIB, True
    if path.startswith(GRADER_PREFIX):
        return 8 * MIB, False  # match reports and event batches
    if path == "/api/v1/github/webhook":
        return MIB, False
    return 16_384, False


class RequestBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode().lower(): value.decode("latin1") for key, value in scope["headers"]}
        ident = secrets.token_hex(12)
        scope.setdefault("state", {})["request_id"] = ident

        async def safe_send(message):
            if message["type"] == "http.response.start":
                if not any(k.lower()==b'cache-control' for k,v in message.get('headers', [])):
                    message.setdefault('headers', []).append((b'cache-control', b'no-store'))
                message.setdefault("headers", []).extend([
                    (b"x-request-id", ident.encode()),
                    (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
            await send(message)

        async def reject(status, code, detail):
            response = JSONResponse({"error": {"code": code, "detail": detail}, "request_id": ident}, status_code=status)
            await response(scope, receive, safe_send)

        tunnel_webhook = (headers.get("host") in self.settings.github.webhook_hosts
                          and scope["path"] == "/api/v1/github/webhook" and scope["method"] == "POST")
        if headers.get("host") != urlsplit(self.settings.origin).netloc and not tunnel_webhook:
            return await reject(403, "forbidden", "Unexpected Host header")
        if headers.get("origin") not in (None, self.settings.origin) or headers.get("sec-fetch-site") == "cross-site":
            return await reject(403, "forbidden", "Cross-origin request rejected")
        if scope["method"] in ("POST", "PUT", "PATCH"):
            limit, archive = body_limit(scope["method"], scope["path"])
            length = headers.get("content-length", "")
            if not length.isascii() or not length.isdigit() or headers.get("transfer-encoding"):
                return await reject(411, "invalid_request", "Content-Length required; chunked bodies unsupported")
            if len(length) > 10 or int(length) > limit:
                return await reject(413, "invalid_request", "Request too large")
            body = bytearray()
            until = time.monotonic() + 30
            while True:
                try:
                    message = await asyncio.wait_for(receive(), max(0, until - time.monotonic()))
                except TimeoutError:
                    return await reject(408, "invalid_request", "Body read timed out")
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > limit or len(body) > int(length):
                    return await reject(413, "invalid_request", "Request too large")
                if not message.get("more_body", False):
                    break
            if len(body) != int(length):
                return await reject(400, "invalid_request", "Incomplete request body")
            # Validate strict JSON before Pydantic to reject duplicate keys,
            # NaN and Infinity consistently with the bot/control protocol.
            if body and not archive and headers.get("content-type", "").split(";")[0] != "application/json":
                return await reject(415, "invalid_request", "Expected application/json")
            if body and headers.get("content-type", "").split(";")[0] == "application/json":
                from arena.protocol import strict_json
                try:
                    strict_json(bytes(body))
                except Fault as exc:
                    return await reject(400, "invalid_request", exc.detail)
            delivered = False

            async def replay_body():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            return await self.app(scope, replay_body, safe_send)
        await self.app(scope, receive, safe_send)


def create_app(settings=None, start_worker=True):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        lease, store, service, competitions, github, retention = Lease(settings.data), None, None, None, None, None
        try:
            store = Store(settings.data)
            app.state.store = store
            import anyio.to_thread
            anyio.to_thread.current_default_thread_limiter().total_tokens = settings.thread_limit
            app.state.accounts = Accounts(store, settings.session_seconds, settings.hash_workers, settings.session_idle_seconds,
                                          settings.admin_session_seconds, settings.admin_session_idle_seconds)
            from arena_api.outbox import MailQueue
            app.state.mail = MailQueue(store, settings)
            from arena_api.people import People
            from arena_api.teams import Teams
            app.state.people = People(app.state.accounts, app.state.mail, settings.account_rules)
            app.state.accounts.season_id = settings.account_rules.season_id
            app.state.accounts.team_size = settings.account_rules.team_size
            app.state.teams = Teams(app.state.accounts, settings.account_rules)
            app.state.clock_id = secrets.token_hex(16)
            service = Service(store, start_worker=start_worker, grader=settings.grader)
            service.max_submissions_per_hour = settings.submissions.max_per_hour
            app.state.service = service
            sample_bots.ensure_starter(service)
            from arena_api.rulesets import RuleCatalog
            app.state.rulesets = RuleCatalog(store, app.state.accounts)
            service.rulesets = app.state.rulesets
            competitions = Competitions(store, service, app.state.accounts, settings.grader.max_attempts)
            app.state.competitions = competitions
            competitions.teams = app.state.teams
            from arena_api.github import GitHub
            github = GitHub(store, app.state.accounts, app.state.teams, service, competitions, settings, concurrent=start_worker)
            app.state.github = github
            from arena_api.broadcast import Snapshots
            app.state.broadcast = Snapshots()
            def featured_jobs():
                import gzip
                path=(settings.publisher.output_dir or settings.data/'snapshots')/'featured.json.gz'
                try:
                    value=json.loads(gzip.decompress(path.read_bytes()))
                    if time.time_ns()//1_000_000-value.get('generated_ms',0)>10000:return set()
                    return {board.get('attempt_id') for board in value.get('boards',[])}
                except (OSError,ValueError,EOFError):return set()
            service.event_flush_ms=lambda job:150 if (app.state.broadcast.watched(job) or job in app.state.broadcast.get('featured-jobs',featured_jobs)) else 0
            from arena_api.operations import Operations
            app.state.operations = Operations(store, app.state.accounts, service, competitions)
            from arena_api.retention import Retention
            retention = Retention(service, settings.retention)
            app.state.retention = retention
            if start_worker:
                competitions.thread.start()
                github.thread.start()
                retention.thread.start()
            yield
        finally:
            if retention:
                retention.stop.set()
                if retention.thread.is_alive():
                    await asyncio.to_thread(retention.thread.join)
            if github:
                github.stop.set()
                github.wake.set()
                if github.thread.is_alive():
                    await asyncio.to_thread(github.thread.join)
                await asyncio.to_thread(github.close_pools)
            if competitions:
                competitions.stop.set()
                if competitions.thread.is_alive():
                    await asyncio.to_thread(competitions.thread.join)
            if service:
                service.stop.set()
                # Startup invalidates these leases and schedules fresh clocks/attempts.
                await asyncio.to_thread(service.join)
                if competitions:
                    competitions.reconcile()
            if store:
                store.db.close()
            lease.close()

    app = FastAPI(title="UET Bot Arena Web API", version="0.6.0", lifespan=lifespan,
                  description="Team/admin control API and durable double round robin competitions. Only locked official fixtures count toward wins; isolated C++ workers own deadlines.",
                  docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.add_middleware(RequestBoundary, settings=settings)
    from arena_api import snapshot_routes
    app.include_router(snapshot_routes.router)
    app.include_router(auth_routes.router)
    from arena_api import people_routes, competition_views
    app.include_router(competition_views.router)
    app.include_router(people_routes.router)
    app.include_router(admin_routes.router)
    from arena_api import announcements
    app.include_router(announcements.router)
    app.include_router(arena_routes.router)
    app.include_router(competition_routes.router)
    app.include_router(ui_routes.router)
    app.include_router(ruleset_routes.router)
    app.include_router(sample_bots.router)
    app.include_router(grader_routes.router)
    app.include_router(email_routes.router)
    from arena_api import github_routes
    app.include_router(github_routes.router)

    def error_body(request, code, detail):
        return {"error": {"code": code, "detail": detail}, "request_id": getattr(request.state, "request_id", None)}

    @app.exception_handler(Fault)
    async def fault(request: Request, exc: Fault):
        headers = {"Retry-After": str(getattr(exc, "retry_after", 900))} if exc.code == "rate_limited" else None
        return JSONResponse(error_body(request, exc.code, exc.detail), status_code=ERROR_STATUS.get(exc.code, 400), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        # Do not echo password values from Pydantic's input/ctx fields.
        detail = [{"loc": e["loc"], "type": e["type"], "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(error_body(request, "invalid_request", detail), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(error_body(request, "not_found" if exc.status_code == 404 else "http_error", exc.detail), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logging.getLogger("arena.api").error("Unhandled API error request_id=%s", getattr(request.state, "request_id", None), exc_info=exc)
        return JSONResponse(error_body(request, "internal_error", "See controller diagnostics with the request ID"), status_code=500)

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {}).update({
            "cookieAuth": {"type": "apiKey", "in": "cookie", "name": "arena_web_session"},
            "csrfAuth": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"}})
        public = {"/api/v1/github/webhook","/api/v1/auth/enroll", "/api/v1/auth/reset", "/api/v1/registrations", "/api/v1/auth/login", "/api/v1/auth/recovery", "/api/v1/auth/email/request", "/api/v1/auth/email/requests/{ident}", "/api/v1/auth/email/verify", "/api/v1/time", "/api/v1/health/live", "/api/v1/health/ready", "/api/v1/platform", "/api/v1/rules", "/api/v1/experimental/guide"}
        schema["components"]["securitySchemes"]["graderToken"] = {"type": "http", "scheme": "bearer", "description": "Mã máy chấm, chỉ hiện một lần khi admin tạo"}
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if path.startswith(GRADER_PREFIX):
                    operation["security"] = [{"graderToken": []}]
                elif method == "get" and path in SPECTATOR_PATHS:
                    operation["security"] = [{}, {"cookieAuth": []}]
                elif path not in public:
                    operation["security"] = [{"cookieAuth": [], **({"csrfAuth": []} if method == "post" else {})}]
        return schema
    app.openapi = openapi

    @app.get("/docs", include_in_schema=False)
    def docs():
        rows = []
        for path, methods in app.openapi()["paths"].items():
            for method, operation in methods.items():
                rows.append(f'<tr><td><b>{html.escape(method.upper())}</b></td><td><code>{html.escape(path)}</code></td><td>{html.escape(operation.get("summary", ""))}</td></tr>')
        return HTMLResponse('<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UET Bot Arena · Backend API</title><link rel="stylesheet" href="/docs.css"><main><p class="eyebrow">UET BOT ARENA / BACKEND 0.6</p><h1>API cho đội thi và ban tổ chức.</h1><p>Xác minh email trường → lập đội bốn người → nộp GitHub → Kiểm tra → ladder → chốt hạn → vòng chung/vòng tròn → chung kết.</p><aside>Bot chạy trên máy chấm từ xa trong sandbox native; bảng xếp hạng chỉ tính trận chính thức hợp lệ. Trận lỗi hạ tầng tự chạy lại trong giới hạn; quá giới hạn cần BTC xử lý.</aside><p><a href="/openapi.json">Tải OpenAPI JSON</a> · <a href="/api/v1/health/live">Liveness</a> · <a href="/api/v1/health/ready">Độ sẵn sàng của hub</a></p><h2>Endpoints</h2><div class="table"><table><thead><tr><th>METHOD</th><th>PATH</th><th>CHỨC NĂNG</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><p>Mutation sau đăng nhập cần cookie HttpOnly và X-CSRF-Token từ /auth/me. Hướng dẫn bootstrap admin, API và vận hành có trong TAI-LIEU.docx của dự án.</p></main></html>')

    @app.get("/docs.css", include_in_schema=False)
    def docs_css():
        return PlainTextResponse("*{box-sizing:border-box}body{margin:0;background:#f3f5ef;color:#193d33;font:15px/1.6 system-ui}main{max-width:1250px;margin:auto;padding:48px 5%}.eyebrow{letter-spacing:.15em;font-size:12px}h1{font-size:36px;line-height:1.2}aside{border-left:4px solid #27745c;background:#e3ece1;padding:18px;margin:24px 0}a{color:#176d52}.table{overflow:auto}table{border-collapse:collapse;width:100%;background:white}th,td{padding:14px;text-align:left;border-bottom:1px solid #dfe7db}th{font-size:12px}code{font-size:13px;white-space:nowrap}td:first-child{color:#267053;font-size:12px}@media(max-width:600px){h1{font-size:28px}main{padding:24px 5%}}", media_type="text/css")
    ui_routes.mount_ui(app, settings)
    return app
