"""Plain-text notices: only BTC writes; the publisher exposes a whitelist."""
import time
from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field, field_validator
from arena.protocol import Fault
from arena.runner import digest
from arena_api.dependencies import admin_principal
from arena_api.schemas import Model

router=APIRouter(prefix='/api/v1/admin',tags=['announcements'])


class Notice(Model):
    title: str=Field(min_length=2,max_length=120)
    body: str=Field(min_length=1,max_length=3000)

    @field_validator('title','body')
    @classmethod
    def text(cls,value,info):
        value=value.replace('\r\n','\n').replace('\r','\n').strip()
        allowed='\n\t' if info.field_name=='body' else ''
        if not value or any(ord(c)<32 and c not in allowed for c in value) or info.field_name=='title' and len(value)<2:
            raise ValueError('Nội dung phải là chữ, không chứa ký tự điều khiển')
        return value


class Visibility(Model):
    expected_revision: int=Field(ge=1)
    archived: bool


def public(item):
    return {k:item[k] for k in ('id','title','body','created_unix_ms','revision','archived_ms')}


@router.get('/announcements')
def listing(request: Request,auth=Depends(admin_principal)):
    return dict(items=[public(i) for i in request.app.state.store.list('announcement')])


@router.post('/announcements',status_code=201)
def create(body: Notice,request: Request,idempotency_key: str=Header(),auth=Depends(admin_principal)):
    store=request.app.state.store;actor=auth['user']['id'];data=body.model_dump()
    def action():
        item=store.create('announcement',actor,dict(**data,revision=1,archived_ms=None))
        request.app.state.accounts.audit(actor,'announcement.created',item['id'])
        return item
    with store.transaction():
        return public(store.deduplicate(actor,'announcement:create',idempotency_key,digest(data),action))


@router.post('/announcements/{ident}/visibility')
def visibility(ident: str,body: Visibility,request: Request,auth=Depends(admin_principal)):
    store=request.app.state.store
    with store.transaction():
        item=store.get(ident,kind='announcement')
        if bool(item['archived_ms'])==body.archived:
            return public(item)
        if item['revision']!=body.expected_revision:
            raise Fault('idempotency_conflict','Thông báo vừa thay đổi; tải lại trước khi thao tác')
        item=store.update(ident,revision=item['revision']+1,archived_ms=time.time_ns()//1_000_000 if body.archived else None)
        request.app.state.accounts.audit(auth['user']['id'],'announcement.archived' if body.archived else 'announcement.restored',ident)
        return public(item)
