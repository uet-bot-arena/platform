from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import Field, SecretStr

from arena.protocol import Fault
from arena_api.dependencies import admin_principal, peer
from arena_api.outbox import university_email
from arena_api.schemas import Model

router = APIRouter(prefix="/api/v1", tags=["email"])


class EmailRequest(Model):
    email: str = Field(min_length=3, max_length=120)
    purpose: Literal["verify", "reset"] = "verify"


class EmailCode(Model):
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    code: SecretStr = Field(min_length=6, max_length=6)


@router.post("/auth/email/request", status_code=202, summary="Xếp hàng gửi mã; không gửi SMTP trong request")
def request_code(body: EmailRequest, request: Request):
    settings = request.app.state.settings
    if not settings.email_providers or not settings.secrets.has("verification_hmac_key"):
        raise Fault("email_unavailable", "Chưa thể gửi mã. Vui lòng thử lại sau.")
    email = university_email(body.email, settings.account_rules)
    request.app.state.accounts.rate_limit("email-code:ip:" + peer(request), 20, 3600)
    return request.app.state.mail.enqueue(email, body.purpose)


@router.get("/auth/email/requests/{ident}", summary="Trạng thái yêu cầu có mã định danh ngẫu nhiên; không trả mã xác minh")
def request_status(ident: str, request: Request):
    return request.app.state.mail.status(ident)


@router.post("/auth/email/verify", summary="Kiểm mã HMAC, tối đa số lần sai được cấu hình")
def verify_code(body: EmailCode, request: Request):
    request.app.state.accounts.rate_limit("email-check:ip:" + peer(request), 100, 3600)
    return request.app.state.mail.verify(body.request_id, body.code.get_secret_value())


@router.get("/admin/email", summary="Hàng đợi, gửi lại và hạn mức dịch vụ; chỉ ban tổ chức")
def mail_summary(request: Request, auth=Depends(admin_principal)):
    return request.app.state.mail.summary()
