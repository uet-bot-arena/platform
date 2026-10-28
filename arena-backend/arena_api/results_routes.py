from datetime import datetime
from fastapi import APIRouter,Depends,Request,Response
from starlette.concurrency import run_in_threadpool
from arena.protocol import Fault
from arena_api.dependencies import admin_principal

router=APIRouter(prefix='/api/v1',tags=['results'])

def publication_admin(request: Request):
    try:return admin_principal(request)
    except Fault as exc:
        if exc.code=='unauthorized':raise Fault('forbidden','Administrator required') from None
        raise

@router.get('/admin/results-export',summary='Xuất dữ liệu công khai và replay của các giải đã hoàn tất')
def export(request: Request,name: str,competitions: str,auth=Depends(publication_admin)):
    raw=request.app.state.results.export(auth['user']['id'],name,competitions.split(','))
    return Response(raw,media_type='application/gzip',headers={'Content-Disposition':'attachment; filename="ket-qua-'+datetime.now().strftime('%Y%m%d-%H%M')+'.json.gz"'})

@router.post('/admin/results-import',summary='Xem trước hoặc xác nhận nạp kết quả bằng SHA-256')
async def ingest(request: Request,preview: int=0,confirm: str | None=None,auth=Depends(publication_admin)):
    if request.headers.get('content-type','').split(';')[0]!='application/gzip':raise Fault('invalid_request','Cần file application/gzip')
    if preview!=1 and confirm is None:raise Fault('invalid_request','Xem trước rồi xác nhận SHA của file')
    raw=await request.body()
    return await run_in_threadpool(request.app.state.results.ingest,auth['user']['id'],raw,None if preview==1 else confirm)

@router.post('/admin/results-import/{ident}/hide',summary='Ẩn sự kiện đã nạp; không sửa dữ liệu giải online')
def hide(ident: str,request: Request,auth=Depends(publication_admin)):
    return request.app.state.results.hide(auth['user']['id'],ident)
