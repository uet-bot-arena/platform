import asyncio
from copy import copy, deepcopy
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
from arena_api.dependencies import COOKIE, team_principal, practice_principal, admin_principal
from arena_api.schemas import Benchmark, Match, TeamView, Model
from arena_api.teams import public_team

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(prefix="/api/v1", tags=["arena"])


@router.get('/ui/page', summary='Dữ liệu riêng của một trang đội; một request, giữ quyền của API đội')
def page_data(request: Request, view: Literal['home','team','submissions','benchmark','practice','competitions','jobs','arena']='home',
              job_id: str | None = None, auth=Depends(practice_principal)):
    return page_bundle(request,view,job_id,auth)


@router.get('/ui/personal', summary='Hồ sơ/đội trên process chính, gồm chốt hạn lập đội')
def page_personal(request: Request, view: Literal['home','team','submissions','competitions']='home', poll: bool = False, auth=Depends(practice_principal)):
    return page_bundle(request,view,None,auth,section='personal',poll=poll)


@router.get('/ui/read', summary='Dữ liệu trang đội chỉ đọc, dùng chung bộ lọc quyền')
def page_read(request: Request, view: Literal['home','team','submissions','competitions']='home', auth=Depends(practice_principal)):
    return page_bundle(request,view,None,auth,section='data')


def page_bundle(request,view,job_id,auth,section='all',poll=False):
    state=request.app.state;team_id=auth['owner_id'];has_team=bool(auth['user']['team_id']);data={}
    received=time.time_ns()/1_000_000
    if section!='data' and view in ('home','team','submissions','competitions'):
        data['personal']={'profile':state.people.profile(auth['user']['id']),
            'team':state.teams.current(auth['user']['id']),'policy':state.teams.policy()}
        data['team']=data['personal']['team']
        if view in ('home','team') and not poll:
            from arena_api.competition_views import team_competitions
            data['teamCompetitions']=team_competitions(state.competitions, team_id) if has_team else {'items':[]}
    if section=='personal':
        return JSONResponse(data)
    if has_team and view in ('home','team','submissions'):
        data['github']=state.github.view(team_id)
    if has_team and view=='team':data['teamElo']=state.competitions.elo.team_view(team_id)
    if view in ('home','benchmark','practice','competitions','arena','submissions'):
        data['submissions']=[state.service.public_submission(s) for s in state.store.list('submission',team_id)]
    if view in ('home','benchmark','practice','competitions','submissions'):
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
def starter(runtime: str = Query("cpp17", pattern="^(cpp17|c11|python3)$"), auth=Depends(practice_principal)):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        names = {"cpp17": ("bot.cpp", "json.hpp", "LICENSE.MIT", "README.md"),
                 "c11": ("bot.c", "jsmn.h", "README.md"), "python3": ("bot.py", "README.md")}[runtime]
        for name in names:
            z.write(ROOT / "sdk" / name, name)
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="ottv2-{runtime}-v2.zip"'})


@router.get("/submissions", summary="Các source version bất biến của đội")
def submissions(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(practice_principal)):
    service = request.app.state.service
    items = request.app.state.store.list("submission", auth["owner_id"])
    return JSONResponse({"items": [service.public_submission(s) for s in items[offset:offset + limit]], "total": len(items), "limit": limit, "offset": offset})


class PracticeSubmission(Model):
    runtime: Literal['cpp17', 'c11', 'python3']
    code: str


@router.post('/practice-submissions', status_code=201, summary='Dán mã nguồn để kiểm 72 mẫu và luyện tập, không dùng dự giải')
async def practice_submission(body: PracticeSubmission, request: Request, idempotency_key: str = Header(alias='Idempotency-Key'), auth=Depends(practice_principal)):
    if not request.app.state.settings.practice_upload:
        raise Fault('forbidden', 'Hub này không nhận bản thử nhanh')
    if not body.code.strip() or len(body.code.encode('utf-8')) > 200_000:
        raise Fault('invalid_request', 'Mã nguồn không được rỗng và tối đa 200.000 byte UTF-8')
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        entry = zipfile.ZipInfo({'cpp17':'bot.cpp', 'c11':'bot.c', 'python3':'bot.py'}[body.runtime], (1980,1,1,0,0,0))
        entry.external_attr = 0o644 << 16
        archive.writestr(entry, body.code.encode('utf-8'))
    from starlette.concurrency import run_in_threadpool
    service, team = request.app.state.service, auth['owner_id']
    item = await run_in_threadpool(lambda: service.submit(team, '', body.runtime, raw.getvalue(), idempotency_key, channel='practice'))
    await run_in_threadpool(service.enqueue, team, 'benchmark', {'submission_id':item['id']}, 'practice-check-'+item['id'])
    return service.public_submission(item)


@router.post('/practice-submissions/zip', status_code=201, summary='ZIP thử nhanh, không dùng dự giải')
async def practice_zip_submission(request: Request, idempotency_key: str = Header(alias='Idempotency-Key'),
                                  x_arena_runtime: str = Header('cpp17'), auth=Depends(practice_principal)):
    if not request.app.state.settings.practice_upload:
        raise Fault('forbidden', 'Hub này không nhận bản thử nhanh')
    item = await receive_zip(request, auth['owner_id'], '', idempotency_key, x_arena_runtime, 2, practice=True)
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(request.app.state.service.enqueue, auth['owner_id'], 'benchmark',
                            {'submission_id':item['id']}, 'practice-check-'+item['id'])
    return request.app.state.service.public_submission(item)


def practice_zip(raw):
    # Strip only the starter's bot/ prefix; archive.unpack keeps all source/security checks.
    if len(raw) > 1024 * 1024:
        raise Fault('invalid_archive', 'ZIP thử nhanh tối đa 1 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            if not items or len(items) > 65 or sum(i.file_size for i in items) > profile().max_source_bytes:
                raise Fault('invalid_archive', 'ZIP cần 1–64 file, tổng mã nguồn tối đa 20 MiB')
            files = [i for i in items if not (i.filename == 'bot/' and i.is_dir())]
            if not files or len(files) > 64:
                raise Fault('invalid_archive', 'ZIP cần 1–64 file mã nguồn')
            wrapped = all(i.filename.startswith('bot/') for i in files)
            if not wrapped:
                return raw
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as flat:
                for item in files:
                    if item.flag_bits & 1:
                        raise Fault('invalid_archive', 'Duplicate, special or encrypted entry')
                    entry = copy(item)
                    entry.filename = item.filename[4:]
                    flat.writestr(entry, archive.read(item))
            return output.getvalue()
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, UnicodeError) as exc:
        raise Fault('invalid_archive', str(exc)) from exc


@router.post("/submissions", status_code=201, summary="Đội nạp ZIP khi hub LAN cho phép")
async def team_upload(request: Request, auth=Depends(team_principal)):
    if not request.app.state.settings.team_zip_upload:
        raise Fault("forbidden", "Đội nộp bài qua repo GitHub đã gắn; không nhận ZIP trên web")
    headers = request.headers
    version, key = headers.get('x-submission-version', ''), headers.get('idempotency-key', '')
    try:
        protocol = int(headers.get('x-arena-protocol', '2'))
    except ValueError:
        raise Fault('invalid_request', 'SDK phải là 2 hoặc 3') from None
    item = await receive_zip(request, auth['user']['team_id'], version, key, headers.get('x-arena-runtime', 'cpp17'), protocol)
    from starlette.concurrency import run_in_threadpool
    body = {'submission_id': item['id']}
    # A retry after a dropped HTTP response must not create a second check.
    await run_in_threadpool(request.app.state.service.enqueue, auth['user']['team_id'], 'benchmark', body, 'lan-check-' + item['id'])
    return request.app.state.service.public_submission(item)


async def receive_zip(request, team, version, key, runtime, protocol, *, practice=False):
    if request.headers.get("Content-Type", "").split(";")[0] != "application/zip":
        raise Fault("invalid_request", "Expected application/zip")
    raw = await request.body()
    from starlette.concurrency import run_in_threadpool
    if practice:
        raw = practice_zip(raw)
    else:
        request.app.state.accounts.team(team)
    return await run_in_threadpool(lambda: request.app.state.service.submit(
        team, version, runtime, raw, key, protocol, channel='practice' if practice else 'internal'))


@router.post("/admin/teams/{ident}/submissions", status_code=201, summary="BTC nạp ZIP nội bộ cho đội; sinh viên dùng GitHub",
             openapi_extra={"requestBody": {"required": True, "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload(ident: str, request: Request, x_submission_version: str = Header(), idempotency_key: str = Header(),
                 x_arena_runtime: str = Header("cpp17"), x_arena_protocol:int=Header(2), auth=Depends(admin_principal)):
    service = request.app.state.service
    item = await receive_zip(request, ident, x_submission_version, idempotency_key, x_arena_runtime, x_arena_protocol)
    with request.app.state.store.transaction():
        request.app.state.accounts.audit(auth["user"]["id"], "submission.internal_import", item["id"])
    return service.public_submission(item)


@router.post("/benchmarks", status_code=202, summary="Đưa benchmark 72 mẫu vào hàng đợi")
def benchmark(body: Benchmark, request: Request, idempotency_key: str = Header(), auth=Depends(practice_principal)):
    return request.app.state.service.enqueue(auth["owner_id"], "benchmark", body.model_dump(exclude_none=True), idempotency_key)


@router.post("/matches", status_code=202, summary="Trận luyện tập giữa hai version của cùng đội")
def match(body: Match, request: Request, idempotency_key: str = Header(), auth=Depends(practice_principal)):
    job = request.app.state.service.enqueue(auth["owner_id"], "match", body.model_dump(exclude_none=True), idempotency_key)
    return public_job(job, full=True)


@router.get("/jobs", summary="Jobs của đội, phân trang")
def jobs(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(practice_principal)):
    items = request.app.state.store.list("job", auth["owner_id"], summary=True)
    return JSONResponse({"items": [public_job(j) for j in items[offset:offset + limit]], "total": len(items), "limit": limit, "offset": offset})


@router.get("/jobs/{ident}", summary="Chi tiết job gồm báo cáo nếu có")
def job(ident: str, request: Request, auth=Depends(practice_principal)):
    value = public_job(request.app.state.store.get(ident, auth["owner_id"], "job"), full=True)
    return {**value, "queue_position": request.app.state.service.pending.position(ident),
            'eta':request.app.state.service.job_eta(value)}


@router.get("/jobs/{ident}/report", summary="Tải báo cáo timing/provenance; 409 khi chưa có báo cáo")
def report(ident: str, request: Request, auth=Depends(practice_principal)):
    result = public_job(request.app.state.store.get(ident, auth["owner_id"], "job"), True)["report"]
    if not result:
        raise Fault("report_unavailable", "No report is available for this job")
    return Response(json.dumps(result, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="report.json"'})


@router.get("/jobs/{ident}/events", summary="Event log JSON hoặc SSE; hỗ trợ Last-Event-ID khi nối lại")
def events(ident: str, request: Request, after: int = Query(0, ge=0, le=9223372036854775807), last_event_id: str | None = Header(None), auth=Depends(practice_principal)):
    store = request.app.state.store
    team_id = auth["owner_id"]
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
                if (value['user']['team_id'] or 'user:'+value['user']['id'])!=team_id:
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
