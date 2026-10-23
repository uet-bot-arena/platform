"""Remote grader API (token per grader, never cookies) and the organiser's grader page API."""
import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from arena.protocol import Fault
from arena_api.dependencies import admin_principal
from arena_api.schemas import Model

router = APIRouter(prefix="/api/v1", tags=["graders"])


class Hello(Model):
    platform: Literal["linux", "windows"]
    runtime_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    slots: int = Field(ge=1, le=64)
    backend: Literal["bwrap", "windows"]
    version: str = Field(min_length=1, max_length=40)


class LeaseRequest(Model):
    wait_seconds: int = Field(default=25, ge=0, le=55)


class Fenced(Model):
    fencing: int = Field(ge=1)


class Events(Fenced):
    events: list[dict] = Field(max_length=600)


class Result(Fenced):
    status: Literal["completed", "failed", "aborted"]
    report: dict | None = None
    error: dict | None = None


class Release(Fenced):
    reason: str = Field(default="released", max_length=60)


class GraderCreate(Model):
    name: str = Field(min_length=1, max_length=60)


def grader(request: Request):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise Fault("unauthorized", "Grader token required (Authorization: Bearer)")
    return request.app.state.service.authenticate(header[7:].strip())


@router.post("/grader/hello", summary="Máy chấm báo nền tảng, runtime_id, số slot, phiên bản")
def hello(body: Hello, request: Request, who=Depends(grader)):
    return request.app.state.service.hello(who, body.model_dump())


@router.post("/grader/lease", summary="Long-poll tối đa 25 s; trả job, lease_id, fencing token và nơi tải source/artifact")
async def lease(body: LeaseRequest, request: Request, who=Depends(grader)):
    service = request.app.state.service
    deadline = time.monotonic() + min(body.wait_seconds, service.grader.long_poll_seconds)
    while True:
        result = await run_in_threadpool(service.try_lease, who)
        if result or time.monotonic() >= deadline or service.stop.is_set() or await request.is_disconnected():
            return {"lease": result}
        await asyncio.sleep(0.25)
        # Revocation ends a waiting grader within one poll interval, not after 25 s.
        who = await run_in_threadpool(grader, request)


@router.post("/grader/leases/{lease_id}/heartbeat", summary="Gia hạn lease (gọi mỗi 5 s)")
def heartbeat(lease_id: str, body: Fenced, request: Request, who=Depends(grader)):
    return request.app.state.service.heartbeat(who, lease_id, body.fencing)


@router.post("/grader/leases/{lease_id}/events", summary="Gửi event theo lô để live và replay vẫn chạy")
def events(lease_id: str, body: Events, request: Request, who=Depends(grader)):
    return request.app.state.service.post_events(who, lease_id, body.fencing, body.events)


@router.post("/grader/leases/{lease_id}/result", summary="Nộp kết quả kèm fencing token; hub chạy lại nước đi để kiểm")
def result(lease_id: str, body: Result, request: Request, who=Depends(grader)):
    return request.app.state.service.submit_result(who, lease_id, body.fencing, body.status, body.report, body.error)


@router.post("/grader/leases/{lease_id}/release", summary="Trả lại lease khi máy chấm dừng giữa chừng")
def release(lease_id: str, body: Release, request: Request, who=Depends(grader)):
    return request.app.state.service.release(who, lease_id, body.fencing, body.reason)


@router.put("/grader/artifacts/{platform}/{sha256}", summary="Tải lên binary đã build (một lần cho mỗi bài nộp, nền tảng, runtime_id)",
            openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def put_artifact(platform: str, sha256: str, request: Request, x_lease_id: str = Header(), x_fencing: int = Header(),
                       x_submission_id: str = Header(), who=Depends(grader)):
    if platform != who.get("platform"):
        raise Fault("forbidden", "Artifacts are stored under the grader's own platform")
    data = await request.body()
    return await run_in_threadpool(request.app.state.service.store_artifact, who, x_lease_id, x_fencing, x_submission_id, sha256, data)


@router.get("/grader/artifacts/{platform}/{sha256}", summary="Tải binary đã build theo SHA-256")
def get_artifact(platform: str, sha256: str, request: Request, who=Depends(grader)):
    path = request.app.state.service.artifact_path(who, platform, sha256)
    return Response(path.read_bytes(), media_type="application/octet-stream")


@router.get("/grader/sources/{submission_id}", summary="Source của bài nộp trong lease đang giữ, dạng ZIP")
def get_source(submission_id: str, request: Request, who=Depends(grader)):
    return Response(request.app.state.service.source_archive(who, submission_id), media_type="application/zip")


@router.get("/admin/graders", summary="Danh sách máy chấm: online/offline theo nhịp tim, nền tảng, slot, runtime_id")
def listing(request: Request, auth=Depends(admin_principal)):
    service = request.app.state.service
    return {"items": service.graders(), "queue": service.pending.counts(), "health": service.health()}


@router.post("/admin/graders", status_code=201, summary="Tạo mã máy chấm; mã chỉ hiện một lần, DB chỉ lưu bản băm")
def create(body: GraderCreate, request: Request, auth=Depends(admin_principal)):
    item = request.app.state.service.create_grader(body.name, auth["user"]["id"])
    with request.app.state.store.lock, request.app.state.store.db:
        request.app.state.accounts.audit(auth["user"]["id"], "grader.created", item["id"])
    return item


@router.post("/admin/graders/{ident}/revoke", summary="Thu hồi mã; máy đó mất kết nối trong vài giây, job đang chạy được trả lại")
def revoke(ident: str, request: Request, auth=Depends(admin_principal)):
    item = request.app.state.service.revoke_grader(ident, auth["user"]["id"])
    with request.app.state.store.lock, request.app.state.store.db:
        request.app.state.accounts.audit(auth["user"]["id"], "grader.revoked", ident)
    return item
