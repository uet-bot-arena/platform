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
from arena_api import auth_routes, admin_routes, arena_routes, competition_routes, ui_routes, ruleset_routes
from arena_api.competitions import Competitions
from arena_api.dependencies import SPECTATOR_PATHS

ERROR_STATUS = {"not_found": 404, "unauthorized": 401, "forbidden": 403, "password_change_required": 403,
                "quota_exceeded": 429, "rate_limited": 429, "worker_unavailable": 503,
                "idempotency_conflict": 409, "version_conflict": 409, "account_conflict": 409, "admin_conflict": 409,
                "registration_conflict": 409, "report_unavailable": 409, "competition_conflict": 409,
                "qualification_required": 409, "provenance_mismatch": 409, "ruleset_conflict": 409,
                "experimental_disabled": 409}


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
                message.setdefault("headers", []).extend([
                    (b"x-request-id", ident.encode()), (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
            await send(message)

        async def reject(status, code, detail):
            response = JSONResponse({"error": {"code": code, "detail": detail}, "request_id": ident}, status_code=status)
            await response(scope, receive, safe_send)

        if headers.get("host") != urlsplit(self.settings.origin).netloc:
            return await reject(403, "forbidden", "Unexpected Host header")
        if headers.get("origin") not in (None, self.settings.origin) or headers.get("sec-fetch-site") == "cross-site":
            return await reject(403, "forbidden", "Cross-origin request rejected")
        if scope["method"] in ("POST", "PUT", "PATCH"):
            limit = 10 * 1024 * 1024 if scope["path"] == "/api/v1/submissions" else 16_384
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
            if body and scope["path"] != "/api/v1/submissions" and headers.get("content-type", "").split(";")[0] != "application/json":
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
        lease, store, service, competitions = Lease(settings.data), None, None, None
        try:
            store = Store(settings.data)
            app.state.store = store
            app.state.accounts = Accounts(store, settings.session_seconds)
            app.state.clock_id = secrets.token_hex(16)
            service = Service(store, start_worker=start_worker)
            app.state.service = service
            from arena_api.rulesets import RuleCatalog
            app.state.rulesets = RuleCatalog(store, app.state.accounts)
            service.rulesets = app.state.rulesets
            competitions = Competitions(store, service, app.state.accounts)
            app.state.competitions = competitions
            from arena_api.operations import Operations
            app.state.operations = Operations(store, app.state.accounts, service, competitions)
            if start_worker:
                competitions.thread.start()
            yield
        finally:
            if competitions:
                competitions.stop.set()
                if competitions.thread.is_alive():
                    await asyncio.to_thread(competitions.thread.join)
            if service:
                service.stop.set()
                # Finish the bounded in-flight operation before closing SQLite
                # or releasing its lease. No second controller can race it.
                if service.worker_thread.is_alive():
                    await asyncio.to_thread(service.worker_thread.join)
                for job in store.list("job"):
                    if job["status"] not in TERMINAL:
                        store.update(job["id"], status="aborted", error={"code": "server_shutdown", "detail": "Controller stopped; no result awarded"})
                        store.event(job["id"], "aborted", {"code": "server_shutdown"})
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
    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(arena_routes.router)
    app.include_router(competition_routes.router)
    app.include_router(ui_routes.router)
    app.include_router(ruleset_routes.router)

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
        public = {"/api/v1/registrations", "/api/v1/auth/login", "/api/v1/auth/recovery", "/api/v1/time", "/api/v1/health/live", "/api/v1/health/ready", "/api/v1/platform", "/api/v1/rules", "/api/v1/experimental/guide"}
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if method == "get" and path in SPECTATOR_PATHS:
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
        return HTMLResponse('<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UET Bot Arena · Backend API</title><link rel="stylesheet" href="/docs.css"><main><p class="eyebrow">UET BOT ARENA / BACKEND 0.6</p><h1>API cho đội thi và ban tổ chức.</h1><p>Đăng ký → admin duyệt → nộp bot → benchmark → chốt version → giải vòng tròn hai lượt → bảng xếp hạng.</p><aside>C++ chạy trong worker Docker; bảng xếp hạng chỉ tính trận chính thức hợp lệ. Trận lỗi hạ tầng chờ admin xử lý.</aside><p><a href="/openapi.json">Tải OpenAPI JSON</a> · <a href="/api/v1/health/live">Liveness</a> · <a href="/api/v1/health/ready">Worker readiness</a></p><h2>Endpoints</h2><div class="table"><table><thead><tr><th>METHOD</th><th>PATH</th><th>CHỨC NĂNG</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><p>Mutation sau đăng nhập cần cookie HttpOnly và X-CSRF-Token từ /auth/me. Hướng dẫn bootstrap admin, API và vận hành có trong docs/BACKEND.md của dự án.</p></main></html>')

    @app.get("/docs.css", include_in_schema=False)
    def docs_css():
        return PlainTextResponse("*{box-sizing:border-box}body{margin:0;background:#f3f5ef;color:#193d33;font:15px/1.6 system-ui}main{max-width:1250px;margin:auto;padding:48px 5%}.eyebrow{letter-spacing:.15em;font-size:12px}h1{font-size:36px;line-height:1.2}aside{border-left:4px solid #27745c;background:#e3ece1;padding:18px;margin:24px 0}a{color:#176d52}.table{overflow:auto}table{border-collapse:collapse;width:100%;background:white}th,td{padding:14px;text-align:left;border-bottom:1px solid #dfe7db}th{font-size:12px}code{font-size:13px;white-space:nowrap}td:first-child{color:#267053;font-size:12px}@media(max-width:600px){h1{font-size:28px}main{padding:24px 5%}}", media_type="text/css")
    ui_routes.mount_ui(app, settings)
    return app
