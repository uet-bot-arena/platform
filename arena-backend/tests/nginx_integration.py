"""Actual loopback nginx + Uvicorn + Argon2, 100 identities from one NAT."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
import uvicorn
from arena.store import Store
from arena_api.accounts import Accounts,token_hash
from arena_api.app import create_app
from arena_api.settings import Settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--nginx',type=Path,default=Path.home()/'platform-test-nginx/root/usr/sbin/nginx')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='arena-nginx-') as tmp:
        root=Path(tmp)
        store=Store(root);accounts=Accounts(store)
        accounts.bootstrap_admin('organizer','nginx-test-only-password')
        # Separate identities sharing one test-only password, verified by real Argon2.
        template=accounts.one("SELECT password_hash,created_ms FROM accounts WHERE username='organizer'")
        with store.lock,store.db:
            store.db.executemany("INSERT INTO accounts(id,username,password_hash,role,team_id,must_change,created_ms) VALUES(?,?,?,'admin',NULL,0,?)",
                [(str(i),f'nat{i:04}',template['password_hash'],template['created_ms']) for i in range(100)])
        store.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(256)
        backend=sock.getsockname()[1]
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        app=create_app(Settings(data=root,origin=origin,trusted_proxies=('127.0.0.1/32',)),start_worker=False)
        server=uvicorn.Server(uvicorn.Config(app,access_log=False,log_level='warning',proxy_headers=False,limit_concurrency=1024))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        config=root/'nginx.conf'
        config.write_text('''daemon off;
pid '''+str(root/'nginx.pid')+''';
error_log '''+str(root/'error.log')+''';
events { worker_connections 1024; }
http {
  access_log off;
  limit_req_zone $binary_remote_addr zone=arena_login:10m rate=10r/s;
  server { listen 127.0.0.1:'''+str(port)+'''; limit_req_status 429;
    location / { limit_req zone=arena_login burst=100 nodelay;
      proxy_pass http://127.0.0.1:'''+str(backend)+''';
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-For $remote_addr;
    }
  }
}
''')
        nginx=None
        try:
            end=time.monotonic()+30
            while not server.started and time.monotonic()<end:time.sleep(.05)
            assert server.started
            nginx=subprocess.Popen([str(args.nginx),'-p',str(root)+'/', '-c',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            time.sleep(.2)
            assert nginx.poll() is None, nginx.communicate()[1].decode()
            started=time.perf_counter();statuses=[]
            with httpx.Client(base_url=origin,timeout=15) as client:
                for i in range(100):
                    response=client.post('/api/v1/auth/login',json={'username':f'nat{i:04}','password':'nginx-test-only-password'},headers={'X-Forwarded-For':'198.51.100.99'})
                    statuses.append(response.status_code)
                    assert response.status_code==200,response.text
                elapsed=time.perf_counter()-started
                assert elapsed<60,elapsed
                for _ in range(9):
                    assert client.post('/api/v1/auth/login',json={'username':'nat0000','password':'wrong-password'}).status_code==401
                assert client.post('/api/v1/auth/login',json={'username':'nat0000','password':'wrong-password'}).status_code==429
            with app.state.store.lock:
                assert app.state.accounts.one('SELECT count FROM rate_limits WHERE bucket=?',(token_hash('login-ip:127.0.0.1'),))['count']==110
                assert not app.state.accounts.one('SELECT count FROM rate_limits WHERE bucket=?',(token_hash('login-ip:198.51.100.99'),))
            report={'nginx':subprocess.check_output([str(args.nginx),'-v'],stderr=subprocess.STDOUT,text=True).strip(),
                'accounts':100,'successful_logins':statuses.count(200),'elapsed_seconds':elapsed,'account_bruteforce':'429 after limit',
                'forwarded_header':'edge overwrites spoofed client header; socket IP counted'}
            output=ROOT/'test-results';output.mkdir(exist_ok=True)
            (output/'nginx.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
        finally:
            if nginx:
                nginx.terminate()
                try:nginx.communicate(timeout=10)
                except subprocess.TimeoutExpired:nginx.kill();nginx.communicate()
            server.should_exit=True;thread.join(timeout=30)


if __name__=='__main__':main()
