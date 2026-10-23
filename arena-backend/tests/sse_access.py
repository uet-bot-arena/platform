"""Real HTTP: per-person stream quota, disconnect cleanup and <=15 s revocation."""
import asyncio
import json
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
import uvicorn
from grader_browser import ROOT,PASSWORD
from arena.conf import Secrets
from arena.config import match_profile
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.app import create_app
from arena_api.settings import Settings


async def check(origin,app,issued,job):
    async with httpx.AsyncClient(base_url=origin,timeout=25) as client,httpx.AsyncClient(base_url=origin,timeout=25) as admin:
        login=await client.post('/api/v1/auth/login',json=dict(username='sse-user',password=PASSWORD));assert login.status_code==200
        login=await admin.post('/api/v1/auth/login',json=dict(username='organizer',password=PASSWORD));assert login.status_code==200
        admin.headers['X-CSRF-Token']=login.json()['csrf_token']
        async def open_stream():
            context=client.stream('GET','/api/v1/jobs/'+job['id']+'/events',headers={'Accept':'text/event-stream'})
            response=await context.__aenter__()
            return context,response
        streams=[await open_stream() for _ in range(4)]
        assert all(response.status_code==200 for _,response in streams)
        denied,response=await open_stream();assert response.status_code==429;await denied.__aexit__(None,None,None)
        await streams[0][0].__aexit__(None,None,None)
        until=time.monotonic()+3
        while len(app.state.broadcast.streams)==4 and time.monotonic()<until:await asyncio.sleep(.05)
        streams[0]=await open_stream();assert streams[0][1].status_code==200
        revoked_at=time.monotonic()
        answer=await admin.post('/api/v1/admin/accounts/'+issued['account']['id']+'/lock',json=dict(locked=True));assert answer.status_code==200
        found=False
        async for line in streams[1][1].aiter_lines():
            if line=='event: auth_expired':found=True;break
        seconds=time.monotonic()-revoked_at
        assert found and seconds<=15,(found,seconds)
        for context,_ in streams:await context.__aexit__(None,None,None)
        until=time.monotonic()+3
        while app.state.broadcast.streams and time.monotonic()<until:await asyncio.sleep(.05)
        assert not app.state.broadcast.streams
        return dict(open_streams=4,fifth_http=429,replacement_http=200,revoked_seconds=round(seconds,3),remaining_slots=0)


def main():
    with tempfile.TemporaryDirectory(prefix='sse-access-') as tmp:
        store=Store(tmp);Accounts(store).bootstrap_admin('organizer',PASSWORD);store.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        origin='http://127.0.0.1:'+str(sock.getsockname()[1])
        app=create_app(Settings(data=Path(tmp),origin=origin,secrets=Secrets({})),start_worker=False)
        server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        try:
            until=time.monotonic()+30
            while not server.started and time.monotonic()<until:time.sleep(.05)
            assert server.started
            issued=app.state.operations.create_team(dict(team='SSE fixture',email='sse@vnu.test',phone='',confirmed=True,members=[dict(name='Fixture '+str(i),student_id='SSE0000'+str(i)) for i in range(4)]),'sse-user',PASSWORD,'test')
            with app.state.store.transaction():app.state.store.db.execute('UPDATE accounts SET must_change=0 WHERE id=?',(issued['account']['id'],))
            job=app.state.store.create('job',issued['account']['team_id'],dict(kind='match',status='playing',profile=match_profile(60000).public(),submission_ids=[]))
            result=asyncio.run(check(origin,app,issued,job))
            (ROOT/'test-results/m1/g8/sse-access.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)
        finally:
            server.should_exit=True;thread.join(30)


if __name__=='__main__':main()
