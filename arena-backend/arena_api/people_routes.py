from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, SecretStr

from arena_api.dependencies import active_principal, admin_principal, participant_principal, peer
from arena_api.schemas import Model, AccountLock

router = APIRouter(prefix="/api/v1", tags=["people-and-teams"])


class Finish(Model):
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    verification_token: SecretStr = Field(min_length=40, max_length=100)
    password: SecretStr = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)


class Profile(Model):
    display_name: str = Field(min_length=2, max_length=80)
    class_name: str = Field(default="", max_length=60)
    language: Literal["cpp17", "c11", "python3"] = "cpp17"
    introduction: str = Field(default="", max_length=160)
    contact: str = Field(default="", max_length=120)
    looking: bool = False


class TeamName(Model):
    name: str = Field(min_length=2, max_length=80)


class TeamCreate(TeamName):
    guest: bool = False


class Invite(Model):
    code: str = Field(min_length=1, max_length=30)


class MemberChange(Model):
    user_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class Flag(Model):
    enabled: bool


class Deadline(Model):
    deadline_ms: int | None = Field(default=None, ge=0, le=253402300799000)
    expected_revision: int = Field(ge=1)


class Merge(Model):
    proposal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(default="", max_length=80)


class AdminMember(MemberChange):
    action: Literal["add", "remove", "captain"]


@router.post("/auth/enroll", status_code=201, summary="Tiêu thụ xác minh email rồi đặt mật khẩu cá nhân")
def enroll(body: Finish, request: Request):
    request.app.state.accounts.rate_limit("enroll:" + peer(request), 1000, 3600)
    return request.app.state.people.finish(body.request_id, body.verification_token.get_secret_value(), body.password.get_secret_value(), body.display_name)


@router.post("/auth/reset", summary="Đặt lại mật khẩu bằng email và hủy mọi phiên cũ")
def reset(body: Finish, request: Request):
    request.app.state.accounts.rate_limit("reset:" + peer(request), 1000, 3600)
    request.app.state.people.finish(body.request_id, body.verification_token.get_secret_value(), body.password.get_secret_value(), "", reset=True)
    return {"ok": True}


@router.get("/users/me", summary="Hồ sơ cá nhân, đội hiện tại và hạn lập đội")
def me(request: Request, auth=Depends(participant_principal)):
    return {"profile": request.app.state.people.profile(auth["user"]["id"]), "team": request.app.state.teams.current(auth["user"]["id"]), "policy": request.app.state.teams.policy()}


@router.post("/users/me", summary="Sửa tên, lớp, ngôn ngữ và cờ tìm đồng đội")
def profile(body: Profile, request: Request, auth=Depends(participant_principal)):
    return request.app.state.people.update(auth["user"]["id"], body.model_dump())


@router.post("/teams", status_code=201, summary="Tạo đội và mã mời, người tạo là đội trưởng")
def create(body: TeamCreate, request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.create(auth["user"]["id"], body.name, body.guest)


@router.post("/teams/join", summary="Vào một đội trong mùa bằng mã mời tám ký tự")
def join(body: Invite, request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.join(auth["user"]["id"], body.code)


@router.post("/teams/leave", summary="Rời đội; đội trưởng chuyển quyền trước nếu còn người")
def leave(request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.change(auth["user"]["id"], "leave")


@router.post("/teams/name", summary="Đội trưởng đổi tên đội")
def rename(body: TeamName, request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.change(auth["user"]["id"], "name", name=body.name)


@router.post("/teams/invite/rotate", summary="Đội trưởng đổi mã mời")
def rotate(request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.change(auth["user"]["id"], "rotate")


@router.post("/teams/recruiting", summary="Đội trưởng bật hoặc tắt tuyển người")
def recruiting(body: Flag, request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.change(auth["user"]["id"], "recruiting", enabled=body.enabled)


@router.post("/teams/member/remove", summary="Đội trưởng xóa một thành viên")
def remove(body: MemberChange, request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.change(auth["user"]["id"], "remove", target=body.user_id)


@router.post("/teams/captain", summary="Đội trưởng chuyển quyền cho thành viên")
def captain(body: MemberChange, request: Request, auth=Depends(participant_principal)):
    return request.app.state.teams.change(auth["user"]["id"], "captain", target=body.user_id)


@router.get("/matching", summary="Ghép đội riêng cho người đăng nhập, không xuất email")
def matching(request: Request, auth=Depends(active_principal)):
    return request.app.state.teams.matching()


@router.get("/admin/users", summary="Danh sách tài khoản cá nhân và khách, không xuất hash mật khẩu")
def users(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), auth=Depends(admin_principal)):
    a = request.app.state.accounts
    with a.store.lock:
        rows = a.rows("SELECT * FROM accounts ORDER BY created_ms DESC,id LIMIT ? OFFSET ?", (limit, offset))
        return {"items": [a.public(p) for p in rows], "limit": limit, "offset": offset}


@router.get("/admin/teams", summary="Các đội trong mùa để BTC chọn chỉnh thành viên")
def team_choices(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), auth=Depends(admin_principal)):
    a, teams = request.app.state.accounts, request.app.state.teams
    with a.store.lock:
        rows = a.rows("SELECT id,name,is_guest,locked_ms FROM teams WHERE season_id=? AND archived_ms IS NULL ORDER BY name,id LIMIT ? OFFSET ?", (teams.season, limit, offset))
        for row in rows:
            row["roster"] = [{k: p[k] for k in ("id", "display_name", "captain")} for p in teams.roster(row["id"])]
        return {"items": rows, "limit": limit, "offset": offset}


@router.post("/admin/users/{ident}/judge", summary="Nâng hoặc thu hồi Giám khảo của cán bộ")
def judge(ident: str, body: Flag, request: Request, auth=Depends(admin_principal)):
    return request.app.state.people.judge(ident, body.enabled, auth["user"]["id"])


@router.post("/admin/users/{ident}/lock", summary="Khóa người dùng và thu hồi phiên")
def lock(ident: str, body: AccountLock, request: Request, auth=Depends(admin_principal)):
    return request.app.state.accounts.lock_account(ident, body.locked, auth["user"]["id"])


@router.get("/admin/team-policy", summary="Hạn lập đội và revision của mùa hiện tại")
def policy(request: Request, auth=Depends(admin_principal)):
    return request.app.state.teams.policy()


@router.post("/admin/team-policy", summary="Đặt hạn lập đội; đủ người tự chốt khi đến hạn")
def deadline(body: Deadline, request: Request, auth=Depends(admin_principal)):
    return request.app.state.teams.set_deadline(body.deadline_ms, body.expected_revision, auth["user"]["id"])


@router.get("/admin/team-merges", summary="Đề xuất ghép người lẻ, ưu tiên lớp rồi ngôn ngữ")
def proposals(request: Request, auth=Depends(admin_principal)):
    return request.app.state.teams.proposals()


@router.post("/admin/team-merges/confirm", summary="BTC xác nhận đề xuất trong 48 giờ, kiểm tra lại roster")
def confirm(body: Merge, request: Request, auth=Depends(admin_principal)):
    return request.app.state.teams.confirm(body.proposal_id, body.name, auth["user"]["id"])


@router.post("/admin/teams/{ident}/membership", summary="BTC chỉnh thành viên sau hạn, lưu audit")
def membership(ident: str, body: AdminMember, request: Request, auth=Depends(admin_principal)):
    return request.app.state.teams.admin_change(ident, body.user_id, body.action, auth["user"]["id"])
