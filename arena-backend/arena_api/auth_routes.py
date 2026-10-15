from fastapi import APIRouter, Depends, Request, Response
from arena_api.dependencies import COOKIE, peer, principal, set_session
from arena_api.schemas import Login, PasswordChange, Recovery, Registration, RegistrationReceipt, SessionView

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/registrations", status_code=201, response_model=RegistrationReceipt, summary="Gửi đăng ký đội 3–4 thành viên, chưa cấp tài khoản")
def register(body: Registration, request: Request):
    return request.app.state.accounts.register(body.model_dump(), peer(request))


@router.post("/auth/login", response_model=SessionView, summary="Đăng nhập bằng username và mật khẩu do admin cấp")
def login(body: Login, request: Request, response: Response):
    result = request.app.state.accounts.login(body.username, body.password.get_secret_value(), peer(request))
    return set_session(request, response, result)


@router.get("/auth/me", response_model=SessionView, summary="Khôi phục phiên và CSRF token sau khi tải lại UI")
def me(auth=Depends(principal)):
    return {key: value for key, value in auth.items() if key != "token_hash"}


@router.post("/auth/logout", summary="Thu hồi phiên hiện tại")
def logout(request: Request, response: Response, auth=Depends(principal)):
    request.app.state.accounts.logout(auth)
    response.delete_cookie(COOKIE, path="/api", httponly=True, secure=request.app.state.settings.cookie_secure, samesite="strict")
    return {"ok": True}


@router.post("/auth/password", response_model=SessionView, summary="Đổi mật khẩu, thu hồi tất cả phiên cũ và tạo phiên mới")
def password(body: PasswordChange, request: Request, response: Response, auth=Depends(principal)):
    result = request.app.state.accounts.change_password(auth, body.current_password.get_secret_value(), body.new_password.get_secret_value())
    return set_session(request, response, result)


@router.post("/auth/recovery", status_code=202, summary="Yêu cầu admin cấp lại mật khẩu; luôn trả lời chung")
def recover(body: Recovery, request: Request):
    return request.app.state.accounts.recover(body.username, body.email, peer(request))
