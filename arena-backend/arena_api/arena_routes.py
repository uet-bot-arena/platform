import asyncio
import io
import json
from pathlib import Path
import time
import zipfile
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import StreamingResponse
from arena import engine
from arena.config import profile, match_profile
from arena.protocol import Fault
from arena.service import TERMINAL
from arena_api.dependencies import COOKIE, team_principal
from arena_api.schemas import Benchmark, Match, TeamView

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(prefix="/api/v1", tags=["arena"])


def public_job(job, full=False):
    # Reports/build manifests contain source hashes, not a host source path.
    if full:
        return job
    result = {key: value for key, value in job.items() if key not in ("report",)}
    if job["kind"] == "benchmark":
        report = job.get("report") or {}
        result["benchmark_summary"] = {"passed": report.get("passed") is True,
                                       "samples": len(report.get("samples", [])),
                                       "suite_id": report.get("suite_id"),
                                       "turn_wall_ms": report.get("turn_wall_ms")}
    return result


@router.get("/health/live", summary="Tiến trình API đang hoạt động; không gọi Docker")
def live():
    return {"ok": True, "service": "uet-arena-web-api", "version": "0.4.0"}


@router.get("/health/ready", summary="DB + immutable worker image sẵn sàng")
def ready(request: Request, response: Response):
    with request.app.state.store.lock:
        request.app.state.store.db.execute("SELECT 1").fetchone()
    result = request.app.state.service.health()
    scheduler_error = request.app.state.competitions.scheduler_error
    if not result["ready"] or scheduler_error:
        response.status_code = 503
    return {"database": "ready", **result, "ready": result["ready"] and not scheduler_error,
            "competition_scheduler": scheduler_error or "ready"}


@router.get("/time", summary="Clock sync cho hiển thị; không xử deadline bằng clock client")
def clock(request: Request):
    received = time.time_ns() / 1_000_000
    return {"clock_id": request.app.state.clock_id, "server_received_unix_ms": received,
            "server_sent_unix_ms": time.time_ns() / 1_000_000, "display_only": True}


@router.get("/config", summary="Cấu hình engine/runtime/profile của đội")
def config(auth=Depends(team_principal)):
    return {"team_id": auth["user"]["team_id"], "profile": match_profile().public(), "match_options_ms": [60000, 300000],
            "runtimes": ["cpp17"], "protocol_version": 2, "engine_id": engine.ENGINE_ID, "codon_enabled": False}


@router.get("/teams/me", response_model=TeamView, summary="Hồ sơ đội hiện tại, chỉ đội đó đọc được")
def team(request: Request, auth=Depends(team_principal)):
    return request.app.state.accounts.team(auth["user"]["team_id"])


@router.get("/dashboard", summary="Thống kê thật từ submissions/jobs của đội")
def dashboard(request: Request, auth=Depends(team_principal)):
    store, team_id = request.app.state.store, auth["user"]["team_id"]
    jobs = store.list("job", team_id)
    return {"team": request.app.state.accounts.team(team_id), "counts": {"submissions": len(store.list("submission", team_id)),
            "benchmarks": sum(j["kind"] == "benchmark" for j in jobs), "matches": sum(j["kind"] == "match" for j in jobs),
            "active_jobs": sum(j["status"] not in TERMINAL for j in jobs)}, "recent_jobs": [public_job(j) for j in jobs[:10]],
            "official_standings_available": True, "competitions_url": "/api/v1/competitions"}


@router.get("/starter.zip", summary="Tải SDK C++17 OTTv2 protocol v2")
def starter(auth=Depends(team_principal)):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("bot.cpp", "json.hpp", "LICENSE.MIT", "README.md"):
            z.write(ROOT / "sdk" / name, name)
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="ottv2-cpp-v2.zip"'})


@router.get("/submissions", summary="Các source version bất biến của đội")
def submissions(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(team_principal)):
    service = request.app.state.service
    items = request.app.state.store.list("submission", auth["user"]["team_id"])
    return {"items": [service.public_submission(s) for s in items[offset:offset + limit]], "total": len(items), "limit": limit, "offset": offset}


@router.post("/submissions", status_code=201, summary="Nộp raw ZIP; không nhận binary hoặc build script",
             openapi_extra={"requestBody": {"required": True, "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload(request: Request, x_submission_version: str = Header(), idempotency_key: str = Header(),
                 x_arena_runtime: str = Header("cpp17"), x_arena_protocol:int=Header(2), auth=Depends(team_principal)):
    if request.headers.get("Content-Type", "").split(";")[0] != "application/zip":
        raise Fault("invalid_request", "Expected application/zip")
    raw = await request.body()
    from starlette.concurrency import run_in_threadpool
    service = request.app.state.service
    item = await run_in_threadpool(service.submit, auth["user"]["team_id"], x_submission_version, x_arena_runtime, raw, idempotency_key,x_arena_protocol)
    return service.public_submission(item)


@router.post("/benchmarks", status_code=202, summary="Đưa benchmark 72 mẫu vào hàng đợi")
def benchmark(body: Benchmark, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    return request.app.state.service.enqueue(auth["user"]["team_id"], "benchmark", body.model_dump(exclude_none=True), idempotency_key)


@router.post("/matches", status_code=202, summary="Trận luyện tập giữa hai version của cùng đội")
def match(body: Match, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    return request.app.state.service.enqueue(auth["user"]["team_id"], "match", body.model_dump(exclude_none=True), idempotency_key)


@router.get("/jobs", summary="Jobs của đội, phân trang")
def jobs(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(team_principal)):
    items = request.app.state.store.list("job", auth["user"]["team_id"])
    return {"items": [public_job(j) for j in items[offset:offset + limit]], "total": len(items), "limit": limit, "offset": offset}


@router.get("/jobs/{ident}", summary="Chi tiết job gồm báo cáo nếu có")
def job(ident: str, request: Request, auth=Depends(team_principal)):
    return public_job(request.app.state.store.get(ident, auth["user"]["team_id"], "job"), full=True)


@router.get("/jobs/{ident}/report", summary="Tải báo cáo timing/provenance; 409 khi chưa có báo cáo")
def report(ident: str, request: Request, auth=Depends(team_principal)):
    result = request.app.state.store.get(ident, auth["user"]["team_id"], "job")["report"]
    if not result:
        raise Fault("report_unavailable", "No report is available for this job")
    return Response(json.dumps(result, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="report.json"'})


@router.get("/jobs/{ident}/events", summary="Event log JSON hoặc SSE; hỗ trợ Last-Event-ID khi nối lại")
def events(ident: str, request: Request, after: int = Query(0, ge=0, le=9223372036854775807), last_event_id: str | None = Header(None), auth=Depends(team_principal)):
    store = request.app.state.store
    team_id = auth["user"]["team_id"]
    store.get(ident, team_id, "job")
    if last_event_id is not None:
        if not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 12:
            raise Fault("invalid_request", "Invalid Last-Event-ID")
        after = int(last_event_id)
    if "text/event-stream" not in request.headers.get("Accept", ""):
        return {"items": store.events(ident, after)}

    async def stream():
        cursor, until = after, time.monotonic() + 60
        while time.monotonic() < until and not request.app.state.service.stop.is_set():
            if await request.is_disconnected():
                return
            try:
                # Revoked/expired sessions must not keep a live feed open.
                request.app.state.accounts.principal(request.cookies.get(COOKIE, ""))
            except Fault:
                yield 'event: auth_expired\ndata: {}\n\n'
                return
            for event in store.events(ident, cursor):
                yield f"id: {event['seq']}\nevent: arena\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                cursor = event["seq"]
            if store.get(ident, team_id, "job")["status"] in TERMINAL and not store.events(ident, cursor):
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
