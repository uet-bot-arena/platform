from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from arena_api.dependencies import admin_principal
from arena_api.schemas import AccountLock, Credentials, ResetPassword, IssuedCredential, RegistrationReceipt, UserView
from arena_api.schemas import AdminTeamCreate, AdminTeamEdit, Revision

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/registrations", summary="Danh sách hồ sơ đăng ký theo trạng thái")
def registrations(request: Request, status: Literal["pending", "approved", "rejected"] = "pending",
                  limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(admin_principal)):
    return {"items": request.app.state.accounts.registrations(status, limit, offset), "limit": limit, "offset": offset}


@router.post("/registrations/{ident}/approve", status_code=201, response_model=IssuedCredential, summary="Duyệt và cấp tài khoản trong một transaction; mật khẩu chỉ trả lần này")
def approve(ident: str, body: Credentials, request: Request, auth=Depends(admin_principal)):
    password = body.temporary_password.get_secret_value() if body.temporary_password else None
    return request.app.state.accounts.approve(ident, body.username, password, auth["user"]["id"])


@router.post("/registrations/{ident}/reject", response_model=RegistrationReceipt, summary="Từ chối đăng ký đang chờ duyệt")
def reject(ident: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.accounts.reject(ident, auth["user"]["id"])


@router.get("/accounts", summary="Danh sách tài khoản đội; không trả hash hoặc mật khẩu")
def accounts(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(admin_principal)):
    return {"items": request.app.state.accounts.accounts(limit, offset), "limit": limit, "offset": offset}


@router.post("/accounts/{ident}/lock", response_model=UserView, summary="Khóa/mở khóa đội và thu hồi các phiên hiện có")
def lock(ident: str, body: AccountLock, request: Request, auth=Depends(admin_principal)):
    return request.app.state.accounts.lock_account(ident, body.locked, auth["user"]["id"])


@router.post("/accounts/{ident}/password", response_model=IssuedCredential, summary="Cấp mật khẩu tạm mới; thu hồi phiên; giữ nguyên trạng thái khóa")
def reset(ident: str, body: ResetPassword, request: Request, auth=Depends(admin_principal)):
    password = body.temporary_password.get_secret_value() if body.temporary_password else None
    return request.app.state.accounts.reset_password(ident, password, auth["user"]["id"])


@router.post("/accounts/{ident}/delivered", summary="Ghi nhận admin đã phát thông tin cho đội; không tự gửi email")
def delivered(ident: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.accounts.delivered(ident, auth["user"]["id"])


@router.get("/recoveries", summary="Yêu cầu cấp lại mật khẩu đang chờ xử lý")
def recoveries(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(admin_principal)):
    return {"items": request.app.state.accounts.recoveries(limit, offset), "limit": limit, "offset": offset}


@router.post('/recoveries/{ident}/resolve', summary='Đóng yêu cầu cấp lại đã xử lý; không đổi mật khẩu')
def resolve_recovery(ident: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.accounts.resolve_recovery(ident, auth['user']['id'])


@router.get("/audit", summary="Audit chỉ có actor/action/target/time, không chứa credential")
def audit(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(admin_principal)):
    return {"items": request.app.state.accounts.audit_log(limit, offset), "limit": limit, "offset": offset}


@router.post('/teams', status_code=201, response_model=IssuedCredential, summary='Admin tạo đội và tài khoản; mật khẩu chỉ trả một lần')
def create_team(body: AdminTeamCreate, request: Request, auth=Depends(admin_principal)):
    return request.app.state.operations.create_team(body.profile.model_dump(), body.username,
        body.temporary_password.get_secret_value() if body.temporary_password else None, auth['user']['id'])


@router.get('/teams/{ident}', summary='Hồ sơ đội và các tài khoản liên quan cho ban tổ chức')
def team(ident: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.accounts.team(ident)


@router.post('/teams/{ident}/edit', summary='Sửa hồ sơ đội với kiểm tra revision')
def edit_team(ident: str, body: AdminTeamEdit, request: Request, auth=Depends(admin_principal)):
    return request.app.state.operations.edit_team(ident, body.model_dump(), auth['user']['id'])


@router.post('/teams/{ident}/archive', summary='Xóa mềm đội, khóa tài khoản, thu hồi phiên; giữ lịch sử')
def archive_team(ident: str, body: Revision, request: Request, auth=Depends(admin_principal)):
    return request.app.state.operations.archive_team(ident, body.expected_revision, True, auth['user']['id'])


@router.post('/teams/{ident}/restore', summary='Khôi phục đội; vẫn khóa tài khoản đến khi admin chủ động mở')
def restore_team(ident: str, body: Revision, request: Request, auth=Depends(admin_principal)):
    return request.app.state.operations.archive_team(ident, body.expected_revision, False, auth['user']['id'])


@router.get('/metrics', summary='Metrics vận hành thật; lịch sử 7 ngày theo Asia/Bangkok, health cache 10s')
def metrics(request: Request, auth=Depends(admin_principal)):
    return request.app.state.operations.metrics()


@router.get('/jobs', summary='Danh sách job toàn nền tảng, có lọc; không kèm report nặng')
def jobs(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647),
         status: Literal['queued','building','validating','benchmarking','playing','completed','failed','aborted'] | None = None,
         kind: Literal['benchmark','match'] | None = None, team_id: str | None = None, auth=Depends(admin_principal)):
    return request.app.state.operations.jobs(limit, offset, status, kind, team_id)


@router.get('/jobs/{ident}', summary='Chi tiết job/report riêng cho admin; không trả credential hoặc source code')
def job(ident: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.store.get(ident, kind='job')


@router.get('/submissions', summary='Version bot toàn nền tảng, manifest không có đường dẫn host')
def submissions(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=2147483647), auth=Depends(admin_principal)):
    return request.app.state.operations.submissions(limit, offset)
