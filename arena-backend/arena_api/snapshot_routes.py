"""Serve only the publisher's named public artifacts; never directory browsing."""
import gzip
import hashlib
import re
from fastapi import APIRouter, Request, Response
from arena.protocol import Fault

router=APIRouter()
ID=r'[A-Za-z0-9_-]{1,100}'
PUBLIC_PATH=re.compile(rf'(?:competitions|queue|featured|announcements|imported)\.json|(?:competitions|fixtures)/{ID}\.json|standings/{ID}\.(?:json|csv)|(?:standings|fixtures)/{ID}/{ID}\.json')


@router.api_route('/snapshots/{name:path}',methods=['GET','HEAD'],include_in_schema=False)
def snapshot(name: str,request: Request):
    if not PUBLIC_PATH.fullmatch(name):
        raise Fault('not_found','Snapshot not found')
    settings=request.app.state.settings
    root=(settings.publisher.output_dir or settings.data/'snapshots').resolve()
    path=root/(name+'.gz')
    if not path.is_file():
        path=request.app.state.results.snapshot_path(name)
        if path is None:raise Fault('not_found','Snapshot not found')
        root=request.app.state.results.root
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise Fault('not_found','Snapshot not found; publisher may not have run yet')
    raw=path.read_bytes()
    encoded='gzip' in request.headers.get('Accept-Encoding','')
    data=raw if encoded else gzip.decompress(raw)
    etag='"'+hashlib.sha256(data).hexdigest()+'"'
    headers={'ETag':etag,'Vary':'Accept-Encoding','Cache-Control':'public, max-age=1, s-maxage=5'}
    if request.headers.get('If-None-Match')==etag:
        return Response(status_code=304,headers=headers)
    if encoded:
        headers['Content-Encoding']='gzip'
    headers['Content-Length']=str(len(data))
    if name.endswith('.csv'):
        headers['Content-Disposition']='attachment; filename="standings.csv"'
    return Response(b'' if request.method=='HEAD' else data,media_type='text/csv' if name.endswith('.csv') else 'application/json',headers=headers)
