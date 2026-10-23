"""One built-in, easy starter opponent; source/build details remain private."""
import io
from pathlib import Path
import zipfile

from fastapi import APIRouter, Depends, Request
from arena.protocol import Fault
from arena_api.dependencies import admin_principal, team_principal
from arena_api.schemas import Model

router = APIRouter(prefix="/api/v1", tags=["sample-bots"])


def ensure_starter(service):
    store=service.store
    old=store.list("sample_bot")
    for item in old:
        if not item.get("builtin") and item["enabled"]:
            store.update(item["id"],enabled=False)
    if any(item.get("builtin") for item in old):
        return
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ("bot.cpp","json.hpp"):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            bundle.writestr(info,(Path(__file__).resolve().parents[1]/"sdk"/name).read_bytes())
    source=service.submit("sample:starter","v1.0.0","cpp17",raw.getvalue(),"builtin-starter-v1")
    store.create("sample_bot","organizer",dict(name="Bot mẫu",runtime="cpp17",protocol_version=2,
        enabled=True,builtin=True,submission_id=source["id"],owner=source["team_id"]))


def public(item):
    return {k:item[k] for k in ("id","name","runtime","protocol_version","enabled")}


@router.get("/sample-bots")
def listing(request: Request, auth=Depends(team_principal)):
    return {"items":[public(x) for x in request.app.state.store.list("sample_bot") if x.get("builtin") and x["enabled"]]}


@router.get("/admin/sample-bots")
def admin_listing(request: Request, auth=Depends(admin_principal)):
    return {"items":[public(x) for x in request.app.state.store.list("sample_bot") if x.get("builtin")]}


class Enabled(Model):
    enabled: bool


@router.post("/admin/sample-bots/{ident}/enabled")
def enabled(ident: str, body: Enabled, request: Request, auth=Depends(admin_principal)):
    store=request.app.state.store
    item=store.get(ident,kind="sample_bot")
    if not item.get("builtin"):
        raise Fault("not_found","Chỉ dùng bot mẫu của bộ starter")
    with store.lock,store.db:
        value=store.update(ident,enabled=body.enabled)
        request.app.state.accounts.audit(auth["user"]["id"],"sample_bot.enabled" if body.enabled else "sample_bot.disabled",ident)
    return public(value)
