from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from arena_api.dependencies import admin_principal, team_principal

router = APIRouter(prefix="/api/v1", tags=["github"])


class Repository(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=180)


@router.get("/teams/me/github", summary="Repo, 20 commit gần nhất và bản dự giải của đội")
def view(request: Request, auth=Depends(team_principal)):
    return request.app.state.github.view(auth["user"]["team_id"])


@router.post("/teams/me/github", status_code=201, summary="Đội trưởng gắn repo private đúng mã đội, một lần")
def attach(body: Repository, request: Request, auth=Depends(team_principal)):
    return request.app.state.github.attach(auth["user"]["id"], auth["user"]["team_id"], body.url)


@router.get("/admin/teams/{ident}/github", summary="BTC xem kết nối repo và nguồn chốt hạn")
def admin_view(ident: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.github.view(ident)


@router.post("/admin/teams/{ident}/github", status_code=201, summary="BTC gắn repo ngoại lệ, có audit")
def admin_attach(ident: str, body: Repository, request: Request, auth=Depends(admin_principal)):
    return request.app.state.github.attach(auth["user"]["id"], ident, body.url, admin=True)


@router.post("/admin/competitions/{ident}/github/{team}/approve-fallback", summary="BTC duyệt nguồn hub quan sát khi activity không đọc được, có audit")
def approve(ident: str, team: str, request: Request, auth=Depends(admin_principal)):
    return request.app.state.github.approve_fallback(ident, team, auth["user"]["id"])


@router.post("/github/webhook", status_code=202, summary="Webhook HMAC chỉ yêu cầu hub đọc lại API GitHub")
async def webhook(request: Request, x_hub_signature_256: str = Header(""), x_github_delivery: str = Header(""), x_github_event: str = Header("")):
    return request.app.state.github.webhook(await request.body(), x_hub_signature_256, x_github_delivery, x_github_event)
