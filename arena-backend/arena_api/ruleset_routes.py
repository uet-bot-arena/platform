import io
import zipfile
import json
from pathlib import Path
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field
from starlette.responses import FileResponse
from arena import experimental
from arena_api.dependencies import admin_principal,active_principal
from arena_api.schemas import Model
from arena_api import addons

router=APIRouter(prefix='/api/v1',tags=['ruleset-addons'])
class Definition(Model):
    definition:dict
    release_notes:str=Field(default='',max_length=2000)
class Change(Model):
    expected_revision:int=Field(ge=1)
    definition:dict|None=None
    release_notes:str|None=Field(default=None,max_length=2000)
class Toggle(Model):
    enabled:bool
    expected_revision:int=Field(ge=0)
class Preview(Definition):
    actions:list[str]=Field(default_factory=list,max_length=32)
class Play(Model):
    actions:list[str]=Field(default_factory=list,max_length=32)
class ImportPackage(Model):
    package:dict
    publish:bool=False

def starter_bytes(root,record):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(root/'sdk/experimental-bot.cpp','bot.cpp')
        for name in ('experimental.hpp','json.hpp','LICENSE.MIT'):z.write(root/'sdk'/name,name)
        z.writestr('README.md',addons.markdown(record,record['contract'])+'\nZIP này chỉ chứa source/header và README/LICENSE, có thể nộp trực tiếp với SDK v3.\n')
    return out.getvalue()

@router.get('/experimental/guide')
def guide():
    return FileResponse(Path(__file__).resolve().parents[1]/'docs/EXPERIMENTAL.md',media_type='text/plain; charset=utf-8')

@router.get('/rulesets')
def listing(request:Request,auth=Depends(active_principal)):
    return request.app.state.rulesets.listing(False)
@router.get('/admin/rulesets')
def admin_list(request:Request,auth=Depends(admin_principal)):
    return request.app.state.rulesets.listing(True)
@router.get('/rulesets/schema')
def schema(auth=Depends(active_principal)):
    return addons.definition_schema()
@router.post('/admin/experimental')
def toggle(body:Toggle,request:Request,idempotency_key:str=Header(),auth=Depends(admin_principal)):
    return request.app.state.rulesets.toggle(auth['user']['id'],body.model_dump(),idempotency_key)
@router.post('/admin/rulesets',status_code=201)
def create(body:Definition,request:Request,idempotency_key:str=Header(),auth=Depends(admin_principal)):
    return request.app.state.rulesets.create(auth['user']['id'],body.model_dump(),idempotency_key)
@router.post('/admin/rulesets/import',status_code=201)
def import_package(body:ImportPackage,request:Request,idempotency_key:str=Header(),auth=Depends(admin_principal)):
    return request.app.state.rulesets.import_package(auth['user']['id'],body.model_dump(),idempotency_key)

@router.get('/rulesets/{ident}')
def detail(ident:str,request:Request,auth=Depends(active_principal)):
    return request.app.state.rulesets.detail(ident,auth['user']['role']=='admin')

@router.post('/rulesets/{ident}/preview')
def play(ident:str,body:Play,request:Request,auth=Depends(active_principal)):
    record=request.app.state.rulesets.detail(ident,auth['user']['role']=='admin')
    return addons.preview(record['definition'],body.actions)

@router.get('/rulesets/{ident}/package.json')
def export_package(ident:str,request:Request,auth=Depends(active_principal)):
    record=request.app.state.rulesets.detail(ident,auth['user']['role']=='admin')
    return Response(json.dumps(addons.package(record),ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="ruleset-{ident[:8]}-v{record["version"]}.json"'})

@router.get('/rulesets/{ident}/kit.zip')
def kit(ident:str,request:Request,auth=Depends(active_principal)):
    record=request.app.state.rulesets.detail(ident,auth['user']['role']=='admin')
    root=Path(__file__).resolve().parents[1];out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('ruleset.json',json.dumps(addons.package(record),ensure_ascii=False,indent=2))
        z.writestr('contract.json',json.dumps(record['contract'],ensure_ascii=False,indent=2))
        z.writestr('definition.schema.json',json.dumps(addons.definition_schema(),ensure_ascii=False,indent=2))
        z.writestr('README.md',addons.markdown(record,record['contract']))
        z.write(root/'docs/EXPERIMENTAL.md','PROTOCOL.md')
        z.write(root/'docs/RULESET-ADDONS.md','RULESET-ADDONS.md')
        z.writestr('starter.zip',starter_bytes(root,record))
    return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="ruleset-{ident[:8]}-v{record["version"]}-sdk-v3.zip"'})

@router.get('/rulesets/{ident}/starter.zip')
def ruleset_starter(ident:str,request:Request,auth=Depends(active_principal)):
    record=request.app.state.rulesets.detail(ident,auth['user']['role']=='admin')
    return Response(starter_bytes(Path(__file__).resolve().parents[1],record),media_type='application/zip',
                    headers={'Content-Disposition':f'attachment; filename="ruleset-{ident[:8]}-starter-v3.zip"'})
@router.post('/admin/rulesets/preview')
def preview(body:Preview,auth=Depends(admin_principal)):
    experimental.validate(body.definition)
    return addons.preview(body.definition,body.actions)
@router.post('/admin/rulesets/{ident}/{verb}')
def mutate(ident:str,verb:str,body:Change,request:Request,idempotency_key:str=Header(),auth=Depends(admin_principal)):
    from arena.protocol import Fault
    if verb not in ('edit','validate','publish','release','clone','archive'):raise Fault('not_found','Unknown ruleset operation')
    if verb=='edit' and body.definition is None:raise Fault('invalid_request','definition required')
    return request.app.state.rulesets.mutate(auth['user']['id'],ident,verb,body.model_dump(),idempotency_key)
@router.get('/experimental/starter.zip')
def starter(auth=Depends(active_principal)):
    root=Path(__file__).resolve().parents[1];out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(root/'sdk/experimental-bot.cpp','bot.cpp')
        for name in ('experimental.hpp','json.hpp','LICENSE.MIT'):z.write(root/'sdk'/name,name)
        z.write(root/'docs/EXPERIMENTAL.md','README.md')
    return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="ottv2-experimental-v3.zip"'})
