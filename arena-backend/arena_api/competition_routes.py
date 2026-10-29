from starlette.responses import JSONResponse, Response
import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import Field
from starlette.responses import StreamingResponse
from arena.protocol import Fault
from arena.service import TERMINAL
from arena_api.dependencies import spectator_admin, admin_principal, team_principal, COOKIE, peer
from arena_api.schemas import CompetitionCreate, CompetitionEntry
from arena_api.schemas import CompetitionEdit, Revision, Model

router = APIRouter(prefix="/api/v1", tags=["competitions"])


def stream_identity(request):
    cookie=request.cookies.get(COOKIE,'')
    try:
        value=request.app.state.accounts.principal(cookie, refresh=False) if cookie else None
    except Fault:
        value=None
    return (value['user']['id'],True) if value else ('guest:'+peer(request),False)


async def stream_admin(request,authenticated):
    if not authenticated:return False
    auth=await request.app.state.broadcast.principal(request.app.state.accounts,request.cookies.get(COOKIE,''))
    return auth['user']['role']=='admin' and not auth['user']['must_change_password']


@router.post("/admin/competitions", status_code=201, summary="Tạo giải Elo, vòng tròn hoặc Swiss; ghim engine/runtime/SDK/profile")
def create(body: CompetitionCreate, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    values = body.model_dump(exclude_none=True)
    if request.app.state.settings.team_zip_upload and values.get('format') == 'double_round_robin':
        values['scoring'] = dict(win=1,draw=.5,loss=0)
    return request.app.state.competitions.create(auth["user"]["id"], values, idempotency_key)


@router.get("/admin/competitions", summary="Danh sách giải của admin, gồm bản nháp")
def admin_list(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(admin_principal)):
    return request.app.state.competitions.listing(True, limit, offset)


@router.post('/admin/competitions/{ident}/edit', summary='Sửa tên/sức chứa trước khi chốt; đổi timing chỉ khi giải còn là nháp trống')
def edit(ident: str, body: CompetitionEdit, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.edit(auth['user']['id'], ident, body.model_dump(exclude_none=True), idempotency_key)


@router.post('/admin/competitions/{ident}/archive', summary='Xóa mềm giải trống hoặc đã hoàn tất; giữ kết quả và replay')
def archive(ident: str, body: Revision, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.archive(auth['user']['id'], ident, body.expected_revision, True, idempotency_key)


@router.post('/admin/competitions/{ident}/restore', summary='Khôi phục giải đã xóa mềm')
def restore(ident: str, body: Revision, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.archive(auth['user']['id'], ident, body.expected_revision, False, idempotency_key)


@router.post("/admin/competitions/{ident}/open", summary="Mở đăng ký bot đã benchmark cho giải")
def open_entries(ident: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.transition(auth["user"]["id"], ident, "open", idempotency_key)


@router.post("/admin/competitions/{ident}/lock", summary="Chốt version/binary và sinh lịch hai lượt trong một transaction")
def lock(ident: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.lock(auth["user"]["id"], ident, idempotency_key)


@router.post("/admin/competitions/{ident}/start", summary="Bắt đầu hoặc tiếp tục giải; giữ nguyên lịch và kết quả đã có")
def start(ident: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.transition(auth["user"]["id"], ident, "running", idempotency_key)


@router.post("/admin/competitions/{ident}/pause", summary="Dừng ghép trận mới; attempt đã đưa vào queue tiếp tục kết thúc")
def pause(ident: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.transition(auth["user"]["id"], ident, "paused", idempotency_key)


@router.post("/admin/competitions/{ident}/fixtures/{fixture_id}/retry", summary="Admin chủ động chạy lại attempt lỗi/aborted chưa tính điểm, giữ toàn bộ lịch sử")
def retry(ident: str, fixture_id: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.retry(auth["user"]["id"], ident, fixture_id, idempotency_key)


@router.get("/admin/competitions/{ident}/fixtures/{fixture_id}/attempts", summary="Admin xem các attempt, lỗi hạ tầng không tính vào thắng/thua")
def attempts(ident: str, fixture_id: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.competitions.attempts(ident, fixture_id)


@router.get("/admin/competitions/{ident}/fixtures/{fixture_id}/attempts/{number}", summary="Chẩn đoán worker/report riêng cho admin, không mở cho đối thủ")
def attempt(ident: str, fixture_id: str, number: int, request: Request, auth=Depends(admin_principal)):
    return request.app.state.competitions.attempt_job(ident, fixture_id, number)


@router.get("/competitions", summary="Danh sách giải công khai; khách xem không cần đăng nhập")
def listing(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), admin=Depends(spectator_admin)):
    return JSONResponse(request.app.state.competitions.listing(admin, limit, offset))


@router.get("/competitions/{ident}", summary="Cấu hình, đội dự giải, trạng thái lịch đấu; không trả thông tin liên hệ/source")
def detail(ident: str, request: Request, admin=Depends(spectator_admin)):
    return JSONResponse(request.app.state.competitions.view(ident, admin))


@router.post("/competitions/{ident}/entry", summary="Đội chọn version và benchmark đạt chuẩn; chỉ được thay trước khi chốt")
def entry(ident: str, body: CompetitionEntry, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    with request.app.state.store.lock:
        team = request.app.state.teams.captain_team(auth["user"]["id"])
        return request.app.state.competitions.enter(auth["user"]["id"], team["id"], ident, body.model_dump(exclude_none=True), idempotency_key)


@router.post("/competitions/{ident}/withdraw", summary="Đội rút bot dự giải trước khi chốt")
def withdraw(ident: str, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    with request.app.state.store.lock:
        team = request.app.state.teams.captain_team(auth["user"]["id"])
        return request.app.state.competitions.withdraw(auth["user"]["id"], team["id"], ident, idempotency_key)


@router.post('/admin/competitions/{ident}/entries/{team_id}', summary='BTC thêm bản Đạt mới nhất của đội trước khi chốt')
def add_team(ident: str, team_id: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.add_team(auth['user']['id'], team_id, ident, idempotency_key)


@router.post("/admin/competitions/{ident}/entries/{team_id}/remove", summary="Admin loại entry trước khi chốt, có audit")
def remove(ident: str, team_id: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.withdraw(auth["user"]["id"], team_id, ident, idempotency_key)


@router.get("/competitions/{ident}/fixtures", summary="Lịch chính thức, màu và kết quả; hỗ trợ phân trang")
def fixtures(ident: str, request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), admin=Depends(spectator_admin)):
    return request.app.state.competitions.schedule(ident, admin, limit, offset)


@router.get("/competitions/{ident}/standings", summary="Bảng Elo hoặc điểm theo thể thức; chỉ tính kết quả hoàn tất hợp lệ")
def standings(ident: str, request: Request, admin=Depends(spectator_admin)):
    comp=request.app.state.competitions
    if comp.visible(ident,admin)['config']['format']=='elo':
        return Response(comp.elo.standings_json(ident),media_type='application/json')
    return JSONResponse(comp.standings(ident, admin))


@router.get('/competitions/{ident}/watch', summary='Tiến độ lịch đấu cho viewer tự chuyển trận; JSON hoặc SSE, không chứa dữ liệu bot riêng')
def watch(ident: str, request: Request, admin=Depends(spectator_admin)):
    competitions = request.app.state.competitions
    initial = competitions.watch_state(ident, admin)
    if 'text/event-stream' not in request.headers.get('Accept', ''):
        return initial

    account,authenticated=stream_identity(request)
    async def stream():
        previous, until = None, time.monotonic() + 60
        def snapshot(is_admin):
            state = competitions.watch_state(ident, is_admin)
            return json.dumps(state, separators=(',', ':')), state['status'] == 'completed'
        while time.monotonic() < until and not request.app.state.service.stop.is_set():
            if await request.is_disconnected():
                return
            try:
                is_admin = await stream_admin(request,authenticated)
                payload, finished = await request.app.state.broadcast.get_async(("watch", ident, is_admin),
                    lambda: snapshot(is_admin))
            except Fault:
                yield 'event: unavailable\ndata: {}\n\n'
                return
            if payload != previous:
                yield 'event: competition\ndata: ' + payload + '\n\n'
                previous = payload
            else:
                yield ': heartbeat\n\n'
            if finished:
                return
            await asyncio.sleep(1)
    return StreamingResponse(request.app.state.broadcast.limited(account,stream()), media_type='text/event-stream',
                             headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


@router.get('/competitions/{ident}/fixtures/{fixture_id}/live-interest',summary='Khách báo đang xem; nội dung ván vẫn đọc từ file tĩnh')
def live_interest(ident: str,fixture_id: str,request: Request,admin=Depends(spectator_admin)):
    fixture=request.app.state.competitions.live_fixture(ident,fixture_id,admin)
    if fixture.get('current_job_id'):
        request.app.state.broadcast.interest(fixture['current_job_id'])
    return {'ok':True}


@router.get("/competitions/{ident}/fixtures/{fixture_id}/events", summary="Board live/replay JSON hoặc SSE đã lọc log và metric riêng; cursor theo attempt")
def events(ident: str, fixture_id: str, request: Request, after: int = Query(0, ge=0, le=9223372036854775807),
           last_event_id: str | None = Header(None), admin=Depends(spectator_admin)):
    competitions = request.app.state.competitions
    fixture = competitions.live_fixture(ident, fixture_id, admin)
    job_id = fixture["current_job_id"]
    # Pin each connection to its attempt. job:seq prevents a retry's seq=1
    # from being hidden behind the previous attempt's larger sequence cursor.
    if last_event_id is not None:
        parts = last_event_id.split(":")
        if (len(parts) != 2 or len(parts[0]) != 32 or any(c not in "0123456789abcdef" for c in parts[0])
                or not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 12):
            raise Fault("invalid_request", "Expected Last-Event-ID: attempt_job_id:sequence")
        after = int(parts[1]) if parts[0] == job_id else 0
    if "text/event-stream" not in request.headers.get("Accept", ""):
        safe, cursor = competitions.safe_events(job_id, after)
        pruned=bool(job_id and request.app.state.store.get(job_id,hydrate=False).get('replay_pruned'))
        return {"attempt_id": job_id, "items": safe, "next_after": cursor, **({'replay_pruned':True} if pruned else {})}

    account,authenticated=stream_identity(request)
    async def stream():
        cursor, until = after, time.monotonic() + 60
        # Always announce attempt, even before the scheduler creates it.
        yield "event: attempt\ndata: " + json.dumps({"attempt_id": job_id}) + "\n\n"
        while time.monotonic() < until and not request.app.state.service.stop.is_set():
            if await request.is_disconnected():
                return
            try:
                is_admin = await stream_admin(request,authenticated)
                current = await request.app.state.broadcast.get_async(("fixture", ident, fixture_id, is_admin),
                    lambda: competitions.live_fixture(ident, fixture_id, is_admin))
            except Fault:
                yield 'event: unavailable\ndata: {}\n\n'
                return
            if current["current_job_id"] != job_id:
                yield 'event: attempt_changed\ndata: {}\n\n'
                return
            if job_id:
                def load(cursor):
                    safe, next_cursor = competitions.safe_events(job_id, cursor)
                    done = request.app.state.store.get(job_id,hydrate=False)['status'] in TERMINAL and not request.app.state.store.events(job_id, next_cursor)
                    return safe, next_cursor, done
                payload, next_cursor, done = await request.app.state.broadcast.event_chunk(job_id,cursor,load)
                if payload:
                    yield payload
                cursor = next_cursor
                if done:
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(request.app.state.broadcast.limited(account,stream(),job=job_id), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.get('/competitions/{ident}/elo/export',summary='Kết quả theo mã ván để tính lại Elo và điểm vòng tròn; không chứa source')
def elo_export(ident: str,request: Request,stage: str=Query('official',pattern='^(ladder|official)$'),admin=Depends(spectator_admin)):
    comp=request.app.state.competitions
    comp.view(ident,admin)
    return comp.elo.book.export(ident,stage)


@router.get('/team/elo',summary='SHA đang đấu, bản dự kiến đợt sau và ván/Elo của chính đội')
def team_elo(request: Request,auth=Depends(team_principal)):
    return JSONResponse(request.app.state.competitions.elo.team_view(auth['user']['team_id']))



class EloExclusion(Model):
    excluded: bool
    reason: str = Field(min_length=3,max_length=300)


@router.post('/admin/competitions/{ident}/teams/{team}/exclude',summary='BTC loại/khôi phục đội khỏi xếp hạng và suất chung kết, giữ kết quả và lịch sử lý do')
def elo_exclude(ident: str,team: str,body: EloExclusion,request: Request,idempotency_key: str=Header(),auth=Depends(admin_principal)):
    return request.app.state.competitions.elo.exclude(auth['user']['id'],ident,team,body.model_dump(),idempotency_key)


@router.get('/admin/competitions/{ident}/exclusions',summary='BTC xem lịch sử loại/khôi phục đội và lý do; không công khai')
def elo_exclusions(ident: str,request: Request,auth=Depends(admin_principal)):
    comp=request.app.state.competitions;comp.get(ident)
    return dict(items=comp.rows('SELECT * FROM elo_exclusions WHERE competition_id=? ORDER BY id',(ident,)))


@router.post('/admin/competitions/{ident}/retry-pairing',summary='BTC gỡ lỗi ghép ladder; chờ mốc đợt tiếp theo, không chạy bù')
def retry_pairing(ident: str,request: Request,idempotency_key: str=Header(),auth=Depends(admin_principal)):
    return request.app.state.competitions.elo.retry_pairing(auth['user']['id'],ident,idempotency_key)
