"""GET allowlist on a read-only connection; no controller lease or background services."""
from contextlib import asynccontextmanager
import secrets
import sqlite3
import threading
import time

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from arena.eta import Timing
from arena.job_queue import DurableJobQueue
from arena.protocol import Fault
from arena.service import Service
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import RequestBoundary, ERROR_STATUS
from arena_api import arena_routes, auth_routes, competition_routes
from arena_api.competitions import Competitions
from arena_api.elo_scheduler import EloScheduler
from arena_api.github import GitHub
from arena_api.settings import Settings

# The installer emits this exact list into nginx; query values never select an upstream.
PATHS=('/api/v1/ui/read','/api/v1/auth/me','/api/v1/teams/me','/api/v1/team/elo',
       '/api/v1/submissions','/api/v1/jobs','/api/v1/dashboard')


def create_app(settings=None):
    settings=settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        store=Store.__new__(Store)
        store.root,store.lock=settings.data.resolve(),threading.RLock()
        store.db=sqlite3.connect((store.root/'arena.sqlite').as_uri()+'?mode=ro',uri=True,check_same_thread=False,isolation_level=None)
        store.db.execute('PRAGMA query_only=ON');store.db.execute('PRAGMA busy_timeout=5000')
        accounts=Accounts.__new__(Accounts);accounts.store,accounts.db=store,store.db
        accounts.ttl,accounts.idle=settings.session_seconds,settings.session_idle_seconds
        accounts.admin_ttl,accounts.admin_idle=settings.admin_session_seconds,settings.admin_session_idle_seconds
        accounts.read_only=True
        service=Service.__new__(Service);service.store=store
        service.pending=DurableJobQueue.__new__(DurableJobQueue);service.pending.store=store
        service.timing=Timing.__new__(Timing);service.timing.store=store
        epoch=store.db.execute('SELECT epoch_ms FROM controller_clock WHERE id=1').fetchone()
        service.lease_epoch_ms=epoch[0] if epoch else time.time_ns()//1_000_000-time.monotonic_ns()//1_000_000
        comp=Competitions.__new__(Competitions)
        comp.store,comp.db,comp.accounts,comp.service=store,store.db,accounts,service
        comp.elo=EloScheduler(comp)
        github=GitHub.__new__(GitHub)
        github.store,github.db,github.a,github.service=store,store.db,accounts,service
        github.settings,github.clock=settings,lambda:time.time_ns()//1_000_000
        app.state.store,app.state.accounts,app.state.service=store,accounts,service
        app.state.competitions,app.state.github=comp,github
        app.state.clock_id=secrets.token_hex(16)
        try:yield
        finally:store.db.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.settings=settings
    app.add_middleware(RequestBoundary,settings=settings)
    for router in (arena_routes.router,auth_routes.router,competition_routes.router):
        app.router.routes.extend(route for route in router.routes if route.path in PATHS and route.methods=={'GET'})
    def error(request,code,detail):
        return {'error':{'code':code,'detail':detail},'request_id':getattr(request.state,'request_id',None)}
    @app.exception_handler(Fault)
    async def fault(request,exc):
        headers={'Retry-After':str(getattr(exc,'retry_after',900))} if exc.code=='rate_limited' else None
        return JSONResponse(error(request,exc.code,exc.detail),status_code=ERROR_STATUS.get(exc.code,400),headers=headers)
    @app.exception_handler(RequestValidationError)
    async def validation(request,exc):
        detail=[{'loc':e['loc'],'type':e['type'],'message':e['msg']} for e in exc.errors()]
        return JSONResponse(error(request,'invalid_request',detail),status_code=422)
    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return JSONResponse(error(request,'not_found' if exc.status_code==404 else 'http_error',exc.detail),status_code=exc.status_code)
    return app


def nginx_config(settings,directory):
    import re
    from pathlib import Path
    directory=Path(directory)
    servers=''.join(f'    server 127.0.0.1:{4183+i} max_fails=1 fail_timeout=2s;\n' for i in range(settings.read_workers))
    servers+='    server 127.0.0.1:4182'+(' backup' if settings.read_workers else '')+';\n'
    (directory/'nginx-read-upstream.conf').write_text('upstream arena_read {\n'+servers+'}\n'
        'map $request_method $arena_read_target { default http://127.0.0.1:4182; GET http://arena_read; }\n')
    pattern='^('+'|'.join(re.escape(path) for path in PATHS)+')$'
    (directory/'nginx-read-routes.conf').write_text('location ~ '+pattern+' {\n'
        '    proxy_pass $arena_read_target;\n    proxy_http_version 1.1;\n'
        '    proxy_set_header Host $http_host;\n    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
        '    proxy_set_header X-Forwarded-Proto $scheme;\n    proxy_set_header Connection "";\n'
        '    proxy_next_upstream error timeout http_502 http_503 http_504;\n'
        '    proxy_next_upstream_tries 3;\n    proxy_connect_timeout 1s;\n'
        '    proxy_buffering off;\n    proxy_read_timeout 75s;\n}\n')
    for name in ('nginx-read-upstream.conf','nginx-read-routes.conf'):
        (directory/name).chmod(0o644)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--nginx-dir',required=True);args=parser.parse_args()
    settings=Settings();nginx_config(settings,args.nginx_dir);print(settings.read_workers)
