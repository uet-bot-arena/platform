import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import StreamingResponse
from arena.protocol import Fault
from arena.service import TERMINAL
from arena_api.dependencies import spectator_admin, admin_principal, team_principal
from arena_api.schemas import CompetitionCreate, CompetitionEntry
from arena_api.schemas import CompetitionEdit, Revision

router = APIRouter(prefix="/api/v1", tags=["competitions"])


@router.post("/admin/competitions", status_code=201, summary="Tạo giải vòng tròn hai lượt; ghim engine/image/SDK/profile")
def create(body: CompetitionCreate, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.create(auth["user"]["id"], body.model_dump(exclude_none=True), idempotency_key)


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
    return request.app.state.competitions.listing(admin, limit, offset)


@router.get("/competitions/{ident}", summary="Cấu hình, đội dự giải, trạng thái lịch đấu; không trả thông tin liên hệ/source")
def detail(ident: str, request: Request, admin=Depends(spectator_admin)):
    return request.app.state.competitions.view(ident, admin)


@router.post("/competitions/{ident}/entry", summary="Đội chọn version và benchmark đạt chuẩn; chỉ được thay trước khi chốt")
def entry(ident: str, body: CompetitionEntry, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    return request.app.state.competitions.enter(auth["user"]["id"], auth["user"]["team_id"], ident, body.model_dump(exclude_none=True), idempotency_key)


@router.post("/competitions/{ident}/withdraw", summary="Đội rút bot dự giải trước khi chốt")
def withdraw(ident: str, request: Request, idempotency_key: str = Header(), auth=Depends(team_principal)):
    return request.app.state.competitions.withdraw(auth["user"]["id"], auth["user"]["team_id"], ident, idempotency_key)


@router.post("/admin/competitions/{ident}/entries/{team_id}/remove", summary="Admin loại entry trước khi chốt, có audit")
def remove(ident: str, team_id: str, request: Request, idempotency_key: str = Header(), auth=Depends(admin_principal)):
    return request.app.state.competitions.withdraw(auth["user"]["id"], team_id, ident, idempotency_key)


@router.get("/competitions/{ident}/fixtures", summary="Lịch chính thức, màu và kết quả; hỗ trợ phân trang")
def fixtures(ident: str, request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), admin=Depends(spectator_admin)):
    return request.app.state.competitions.schedule(ident, admin, limit, offset)


@router.get("/competitions/{ident}/standings", summary="Điểm = 3 × thắng + hòa; bằng tổng điểm đồng hạng; chỉ tính fixture hoàn tất hợp lệ")
def standings(ident: str, request: Request, admin=Depends(spectator_admin)):
    return request.app.state.competitions.standings(ident, admin)


@router.get('/competitions/{ident}/watch', summary='Tiến độ lịch đấu cho viewer tự chuyển trận; JSON hoặc SSE, không chứa dữ liệu bot riêng')
def watch(ident: str, request: Request, admin=Depends(spectator_admin)):
    competitions = request.app.state.competitions
    initial = competitions.watch_state(ident, admin)
    if 'text/event-stream' not in request.headers.get('Accept', ''):
        return initial

    async def stream():
        previous, until = None, time.monotonic() + 60
        while time.monotonic() < until and not request.app.state.service.stop.is_set():
            if await request.is_disconnected():
                return
            try:
                state = competitions.watch_state(ident, spectator_admin(request))
            except Fault:
                yield 'event: unavailable\ndata: {}\n\n'
                return
            payload = json.dumps(state, separators=(',', ':'))
            if payload != previous:
                yield 'event: competition\ndata: ' + payload + '\n\n'
                previous = payload
            else:
                yield ': heartbeat\n\n'
            if state['status'] == 'completed':
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


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
        return {"attempt_id": job_id, "items": safe, "next_after": cursor}

    async def stream():
        cursor, until = after, time.monotonic() + 60
        # Always announce attempt, even before the scheduler creates it.
        yield "event: attempt\ndata: " + json.dumps({"attempt_id": job_id}) + "\n\n"
        while time.monotonic() < until and not request.app.state.service.stop.is_set():
            if await request.is_disconnected():
                return
            try:
                current = competitions.live_fixture(ident, fixture_id, spectator_admin(request))
            except Fault:
                yield 'event: unavailable\ndata: {}\n\n'
                return
            if current["current_job_id"] != job_id:
                yield 'event: attempt_changed\ndata: {}\n\n'
                return
            if job_id:
                safe, next_cursor = competitions.safe_events(job_id, cursor)
                for event in safe:
                    yield f"id: {job_id}:{event['seq']}\nevent: arena\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                cursor = next_cursor
                if request.app.state.store.get(job_id)["status"] in TERMINAL and not request.app.state.store.events(job_id, cursor):
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
