import asyncio
from copy import deepcopy
import io
import json
from pathlib import Path
import time
import zipfile
from typing import Literal
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import StreamingResponse, JSONResponse
from arena import engine
from arena.config import profile, match_profile
from arena.protocol import Fault
from arena.service import TERMINAL
from arena_api.dependencies import COOKIE, team_principal, admin_principal
from arena_api.schemas import Benchmark, Match, TeamView
from arena_api.teams import public_team

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(prefix="/api/v1", tags=["arena"])


@router.get('/ui/page', summary='Dữ liệu riêng của một trang đội; một request, giữ quyền của API đội')
def page_data(request: Request, view: Literal['home','team','submissions','benchmark','practice','competitions','jobs','arena']='home',
              job_id: str | None = None, auth=Depends(team_principal)):
    return page_bundle(request,view,job_id,auth)


@router.get('/ui/personal', summary='Hồ sơ/đội trên process chính, gồm chốt hạn lập đội')
def page_personal(request: Request, view: Literal['home','team','submissions','competitions']='home', auth=Depends(team_principal)):
    return page_bundle(request,view,None,auth,section='personal')


@router.get('/ui/read', summary='Dữ liệu trang đội chỉ đọc, dùng chung bộ lọc quyền')
def page_read(request: Request, view: Literal['home','team','submissions','competitions']='home', auth=Depends(team_principal)):
    return page_bundle(request,view,None,auth,section='data')


def page_bundle(request,view,job_id,auth,section='all'):
    state=request.app.state;team_id=auth['user']['team_id'];data={}
    received=time.time_ns()/1_000_000
    if section!='data' and view in ('home','team','submissions','competitions'):
        data['personal']={'profile':state.people.profile(auth['user']['id']),
            'team':state.teams.current(auth['user']['id']),'policy':state.teams.policy()}
        data['team']=data['personal']['team']
        if view in ('home','team'):
            from arena_api.competition_views import team_competitions
            data['teamCompetitions']=team_competitions(state.competitions, team_id)
    if section=='personal':
        return JSONResponse(data)
    if view in ('team','submissions'):
        data['github']=state.github.view(team_id)
    if view=='team':data['teamElo']=state.competitions.elo.team_view(team_id)
    if view in ('home','benchmark','practice','competitions','arena'):
        data['submissions']=[state.service.public_submission(s) for s in state.store.list('submission',team_id)]
    if view in ('home','benchmark','practice','competitions'):
        jobs=state.store.list('job',team_id,summary=True)
        data['jobs']=[public_job(j) for j in jobs]
        if view=='home':
            data['dashboard']={'counts':{'submissions':len(data['submissions']),
                'benchmarks':sum(j['kind']=='benchmark' for j in jobs),'matches':sum(j['kind']=='match' for j in jobs),
                'active_jobs':sum(j['status'] not in TERMINAL for j in jobs)}}
    if view in ('benchmark','practice'):data['rulesetCatalog']=state.rulesets.listing(False)
    if view=='practice':
        from arena_api.sample_bots import public
        data['sampleBots']=[public(x) for x in state.store.list('sample_bot') if x.get('builtin') and x['enabled']]
    if view in ('jobs','arena'):
        if not job_id:raise Fault('invalid_request','Cần mã tác vụ')
        job=state.store.get(job_id,team_id,'job')
        data['job']=dict(public_job(job,full=True),queue_position=state.service.pending.position(job_id),eta=state.service.job_eta(job))
        if view=='arena':
            events=[];cursor=0
            while batch:=state.store.events(job_id,cursor):
                events.extend(private_event(job,e) for e in batch);cursor=batch[-1]['seq']
            data['initialHistory']=dict(items=events,**({'replay_pruned':True} if job.get('replay_pruned') else {}))
    data['clock']={'clock_id':state.clock_id,'server_received_unix_ms':received,'server_sent_unix_ms':time.time_ns()/1_000_000}
    return JSONResponse(data)


PRIVATE_SAMPLE = "Chẩn đoán của bot mẫu BTC được giữ kín"


def public_job(job, full=False):
    if job.get("sample_bot"):
        # Hide only the organiser bot's side: its build manifest/log, stderr and fault text.
        # The team keeps every diagnostic about its own bot.
        job = deepcopy(job)
        side = job["sample_bot"].get("side", "blue")
        index = engine.SIDES.index(side)
        job["sample_bot"] = {"id": job["sample_bot"]["id"], "side": side}
        if job.get("error") and job["error"].get("side") == side:
            job["error"]["detail"] = PRIVATE_SAMPLE
        report = job.get("report") or {}
        for field in ("artifacts", "sandbox_metrics"):
            if len(report.get(field) or []) > index:
                report[field][index] = {"redacted": True}
        for fault in [report.get("fault"), *(report.get("startup_faults") or [])]:
            if fault and fault.get("side") == side:
                fault["detail"] = PRIVATE_SAMPLE
    # Reports/build manifests contain source hashes, not a host source path.
    if full:
        return job
    result = {key: value for key, value in job.items() if key not in ("report",)}
    if job["kind"] == "benchmark":
        report = job.get("report") or job.get('check_summary') or {}
        result["benchmark_summary"] = {"passed": report.get("passed") is True,
                                       "samples": len(report['samples']) if isinstance(report.get('samples'), list) else report.get('samples', 0),
                                       "suite_id": report.get("suite_id"),
                                       "turn_wall_ms": report.get("turn_wall_ms"), 'reason': report.get('reason')}
    return result


def private_event(job, event):
    if not job.get("sample_bot"):
        return event
    event = deepcopy(event)
    sample = job["sample_bot"]
    if event["type"] == "build" and event["data"].get("submission_id") == sample["submission_id"]:
        event["data"] = {"redacted": True}
    if event["type"] == "failed" and event["data"].get("side") == sample.get("side", "blue"):
        event["data"]["detail"] = PRIVATE_SAMPLE
    return event


@router.get("/health/live", summary="Tiến trình API đang hoạt động")
def live():
    return {"ok": True, "service": "uet-arena-web-api", "version": "0.6.0"}


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
            "runtimes": ["cpp17", "c11", "python3"], "protocol_version": 2, "engine_id": engine.ENGINE_ID, "codon_enabled": False}


@router.get("/teams/me", response_model=TeamView, summary="Hồ sơ đội hiện tại, chỉ đội đó đọc được")
def team(request: Request, auth=Depends(team_principal)):
    return public_team(request.app.state.accounts.team(auth["user"]["team_id"]))


@router.get("/dashboard", summary="Thống kê thật từ submissions/jobs của đội")
def dashboard(request: Request, auth=Depends(team_principal)):
    store, team_id = request.app.state.store, auth["user"]["team_id"]
    jobs = store.list("job", team_id, summary=True)
    return JSONResponse({"team": public_team(request.app.state.accounts.team(team_id)), "counts": {"submissions": len(store.list("submission", team_id)),
            "benchmarks": sum(j["kind"] == "benchmark" for j in jobs), "matches": sum(j["kind"] == "match" for j in jobs),
            "active_jobs": sum(j["status"] not in TERMINAL for j in jobs)}, "recent_jobs": [public_job(j) for j in jobs[:10]],
            "official_standings_available": True, "competitions_url": "/api/v1/competitions"})


@router.get("/starter.zip", summary="Tải SDK C++17 OTTv2 protocol v2")
def starter(runtime: str = Query("cpp17", pattern="^(cpp17|c11|python3)$"), auth=Depends(team_principal)):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        names = {"cpp17": ("bot.cpp", "json.hpp", "LICENSE.MIT", "README.md"),
                 "c11": ("bot.c", "jsmn.h", "README.md"), "python3": ("bot.py", "README.md")}[runtime]
        for name in names:
            z.write(ROOT / "sdk" / name, name)
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="ottv2-{runtime}-v2.zip"'})


@router.get("/submissions", summary="Các source version bất biến của đội")
def submissions(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(team_principal)):
    service = request.app.state.service
    items = request.app.state.store.list("submission", auth["user"]["team_id"])
    return JSONResponse({"items": [service.public_submission(s) for s in items[offset:offset + limit]], "total": len(items), "limit": limit, "offset": offset})


@router.post("/submissions", include_in_schema=False)
def closed_upload(auth=Depends(team_principal)):
    raise Fault("forbidden", "Đội nộp bài qua repo GitHub đã gắn; không nhận ZIP trên web")


@router.post("/admin/teams/{ident}/submissions", status_code=201, summary="BTC nạp ZIP nội bộ cho đội; sinh viên dùng GitHub",
             openapi_extra={"requestBody": {"required": True, "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload(ident: str, request: Request, x_submission_version: str = Header(), idempotency_key: str = Header(),
                 x_arena_runtime: str = Header("cpp17"), x_arena_protocol:int=Header(2), auth=Depends(admin_principal)):
    if request.headers.get("Content-Type", "").split(";")[0] != "application/zip":
        raise Fault("invalid_request", "Expected application/zip")
    raw = await request.body()
    from starlette.concurrency import run_in_threadpool
    service = request.app.state.service
    request.app.state.accounts.team(ident)
    item = await run_in_threadpool(service.submit, ident, x_submission_version, x_arena_runtime, raw, idempotency_key,x_arena_protocol)
    with request.app.state.store.transaction():
        request.app.state.accounts.audit(auth["user"]["id"], "submission.internal_import", item["id"])
    return service.public_submission(item)


@router.post("/benchmarks", status_code=202, summary="Đưa benchmark 72 mẫu vào hàng đợi")
def benchmark(body: Benchmark, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    return request.app.state.service.enqueue(auth["user"]["team_id"], "benchmark", body.model_dump(exclude_none=True), idempotency_key)


@router.post("/matches", status_code=202, summary="Trận luyện tập giữa hai version của cùng đội")
def match(body: Match, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    job = request.app.state.service.enqueue(auth["user"]["team_id"], "match", body.model_dump(exclude_none=True), idempotency_key)
    return public_job(job, full=True)


@router.get("/jobs", summary="Jobs của đội, phân trang")
def jobs(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(team_principal)):
    items = request.app.state.store.list("job", auth["user"]["team_id"], summary=True)
    return JSONResponse({"items": [public_job(j) for j in items[offset:offset + limit]], "total": len(items), "limit": limit, "offset": offset})


@router.get("/jobs/{ident}", summary="Chi tiết job gồm báo cáo nếu có")
def job(ident: str, request: Request, auth=Depends(team_principal)):
    value = public_job(request.app.state.store.get(ident, auth["user"]["team_id"], "job"), full=True)
    return {**value, "queue_position": request.app.state.service.pending.position(ident),
            'eta':request.app.state.service.job_eta(value)}


@router.get("/jobs/{ident}/report", summary="Tải báo cáo timing/provenance; 409 khi chưa có báo cáo")
def report(ident: str, request: Request, auth=Depends(team_principal)):
    result = public_job(request.app.state.store.get(ident, auth["user"]["team_id"], "job"), True)["report"]
    if not result:
        raise Fault("report_unavailable", "No report is available for this job")
    return Response(json.dumps(result, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="report.json"'})


@router.get("/jobs/{ident}/events", summary="Event log JSON hoặc SSE; hỗ trợ Last-Event-ID khi nối lại")
def events(ident: str, request: Request, after: int = Query(0, ge=0, le=9223372036854775807), last_event_id: str | None = Header(None), auth=Depends(team_principal)):
    store = request.app.state.store
    team_id = auth["user"]["team_id"]
    job = store.get(ident, team_id, "job")
    if last_event_id is not None:
        if not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 12:
            raise Fault("invalid_request", "Invalid Last-Event-ID")
        after = int(last_event_id)
    if "text/event-stream" not in request.headers.get("Accept", ""):
        return {"items": [private_event(job, e) for e in store.events(ident, after)],
                **({'replay_pruned':True} if job.get('replay_pruned') else {})}

    async def stream():
        cursor, until = after, time.monotonic() + 60
        while time.monotonic() < until and not request.app.state.service.stop.is_set():
            if await request.is_disconnected():
                return
            try:
                # Revoked/expired sessions must not keep a live feed open.
                value=await request.app.state.broadcast.principal(request.app.state.accounts,request.cookies.get(COOKIE, ""))
                if value['user']['team_id']!=team_id:
                    raise Fault('unauthorized','Đội đã thay đổi')
            except Fault:
                yield 'event: auth_expired\ndata: {}\n\n'
                return
            def batch():
                values = store.events(ident, cursor)
                next_cursor = values[-1]['seq'] if values else cursor
                done = store.get(ident, team_id, 'job')['status'] in TERMINAL and not store.events(ident, next_cursor)
                return values, done
            values, done = await request.app.state.broadcast.get_async(('private', ident, team_id, cursor), batch)
            for event in values:
                event = private_event(job, event)
                yield f"id: {event['seq']}\nevent: arena\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                cursor = event["seq"]
            if done:
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(request.app.state.broadcast.limited(auth["user"]["id"],stream(),job=ident), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control":"no-cache"})
