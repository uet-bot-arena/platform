"""Organizer-owned source stays private; teams see only enabled catalog entries."""
import hashlib
import uuid
from urllib.parse import unquote
from fastapi import APIRouter, Depends, Header, Request
from arena.protocol import Fault
from arena.service import digest
from arena_api.dependencies import admin_principal, team_principal
from arena_api.schemas import Model

router = APIRouter(prefix="/api/v1", tags=["sample-bots"])

def public(item):
    return {k:item[k] for k in ("id", "name", "level", "runtime", "protocol_version", "enabled")}

@router.get("/sample-bots")
def listing(request: Request, auth=Depends(team_principal)):
    return {"items":[public(x) for x in request.app.state.store.list("sample_bot") if x["enabled"]]}

@router.get("/admin/sample-bots")
def admin_listing(request: Request, auth=Depends(admin_principal)):
    return {"items":[public(x) for x in request.app.state.store.list("sample_bot")]}

@router.post("/admin/sample-bots", status_code=201,
             openapi_extra={"requestBody":{"required":True,"content":{"application/zip":{"schema":{"type":"string","format":"binary"}}}}})
async def upload(request: Request, x_bot_name: str = Header(), x_bot_level: str = Header("easy"),
                 x_arena_runtime: str = Header("cpp17"), x_arena_protocol: int = Header(2),
                 idempotency_key: str = Header(), auth=Depends(admin_principal)):
    x_bot_name = unquote(x_bot_name)
    if not 1 <= len(x_bot_name.strip()) <= 80 or any(ord(c)<32 for c in x_bot_name) or x_bot_level not in ("easy", "medium", "hard", "expert"):
        raise Fault("invalid_request", "Name required; level must be easy, medium, hard or expert")
    if request.headers.get("content-type", "").split(";")[0] != "application/zip":
        raise Fault("invalid_request", "Expected application/zip")
    raw, service = await request.body(), request.app.state.service
    store = service.store
    def prepare():
        owner = "sample:" + uuid.uuid4().hex
        return service.submit(owner, "v1.0.0", x_arena_runtime, raw, idempotency_key, x_arena_protocol)
    def create(source):
        return store.create("sample_bot", "organizer", {"name":x_bot_name.strip(),"level":x_bot_level,
            "runtime":x_arena_runtime,"protocol_version":x_arena_protocol,"enabled":True,"submission_id":source["id"],"owner":source["team_id"]})
    from starlette.concurrency import run_in_threadpool
    value = await run_in_threadpool(store.deduplicate, "organizer", "sample_bot", idempotency_key,
        digest({"zip":hashlib.sha256(raw).hexdigest(),"name":x_bot_name,"level":x_bot_level,"runtime":x_arena_runtime,"protocol":x_arena_protocol}), create, prepare)
    with store.lock, store.db:
        request.app.state.accounts.audit(auth["user"]["id"], "sample_bot.uploaded", value["id"])
    return public(value)

class Enabled(Model):
    enabled: bool

@router.post("/admin/sample-bots/{ident}/enabled")
def enabled(ident: str, body: Enabled, request: Request, auth=Depends(admin_principal)):
    store = request.app.state.store
    store.get(ident, kind="sample_bot")
    with store.lock, store.db:
        value = store.update(ident, enabled=body.enabled)
        request.app.state.accounts.audit(auth["user"]["id"], "sample_bot.enabled" if body.enabled else "sample_bot.disabled", ident)
    return public(value)
