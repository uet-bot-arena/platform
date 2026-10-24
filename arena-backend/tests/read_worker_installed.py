"""Installed nginx/read workers: kill one while authenticated GETs continue."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time
from urllib.request import Request,urlopen


def main():
    db=sqlite3.connect('/var/lib/uet-arena/data/arena.sqlite')
    account=db.execute("SELECT id FROM accounts WHERE role='admin' AND locked=0 LIMIT 1").fetchone()[0]
    token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
    db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest,account,secrets.token_urlsafe(32),time.time_ns()//1_000_000+300000));db.commit()
    unit='uet-arena-read@4183'
    def pid():return subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip()
    before=pid();assert before!='0'
    def get(_):
        with urlopen(Request('http://127.0.0.1:4181/api/v1/auth/me',headers={'Cookie':'arena_web_session='+token}),timeout=10) as response:
            assert response.status==200
            assert json.load(response)['user']['id']==account
        time.sleep(.02)
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures=[pool.submit(get,i) for i in range(160)]
            subprocess.run(['systemctl','kill','--signal=KILL',unit],check=True)
            for future in futures:future.result()
        deadline=time.monotonic()+10
        while pid() in ('0',before):
            assert time.monotonic()<deadline,'read worker did not restart'
            time.sleep(.1)
        for port in (4183,4184):
            value=subprocess.check_output(['systemctl','show',f'uet-arena-read@{port}','-p','Slice','--value'],text=True).strip()
            assert value=='arena.slice'
        result=dict(requests=160,errors=0,restarted=pid()!=before,workers_in_shared_slice=2)
        Path('/root/g10b-read-check.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    finally:
        db.execute('DELETE FROM sessions WHERE token_hash=?',(digest,));db.commit();db.close()


if __name__=='__main__':main()
