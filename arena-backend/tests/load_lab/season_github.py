"""Local HTTP GitHub fixture; the real intake/client still parse every response."""
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import tarfile
import threading
from urllib.parse import urlsplit
import zipfile


class GitHubFixture:
    def __init__(self):
        self.repositories = {}
        self.statuses = []
        self.errors = []
        self.requests = 0
        self.polls = Counter()
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, code, data=None, headers=None):
                raw = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(code)
                for key, value in (headers or {}).items(): self.send_header(key, value)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)

            def do_POST(self):
                self.route('POST')

            def do_GET(self):
                self.route('GET')

            def route(self, method):
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    body = json.loads(self.rfile.read(size)) if size else None
                    with fixture.lock:
                        fixture.requests += 1
                        code, data, headers = fixture.request(method, self.path, dict(self.headers), body)
                    self.respond(code, data, headers)
                except Exception as exc:
                    fixture.errors.append(type(exc).__name__+': '+str(exc))
                    self.respond(500, dict(error='Fixture assertion failed'))

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def add(self, owner, name, archive, pushed_ms):
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            files = {n: bundle.read(n) for n in bundle.namelist()}
        sha = hashlib.sha1(files['bot.cpp']).hexdigest()
        root, bot = hashlib.sha1((sha+'root').encode()).hexdigest(), hashlib.sha1((sha+'bot').encode()).hexdigest()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            for path, data in files.items():
                item = tarfile.TarInfo(f'repo-{sha}/bot/{path}'); item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        row = dict(id=len(self.repositories)+100, owner=owner, name=name, sha=sha,
            root=root, bot=bot, archive=output.getvalue(), pushed_ms=pushed_ms,
            tree=[dict(path=n, sha=hashlib.sha1(data).hexdigest(), type='blob', mode='100644') for n,data in files.items()])
        with self.lock: self.repositories[owner, name] = row
        return row

    def request(self, method, path, headers, body):
        headers = {k.lower():v for k,v in headers.items()}
        parts = urlsplit(path).path.strip('/').split('/')
        authorization = headers.get('authorization', '')
        if parts[:2] == ['app', 'installations']:
            assert method == 'POST' and parts[-1] == 'access_tokens'
            token = authorization.removeprefix('Bearer ')
            payload = token.split('.')[1]
            assert json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))['iss'] == '123456'
            return 201, dict(token='fixture-installation-token', expires_at='2099-01-01T00:00:00Z'), {}
        if parts[0] != 'repos':
            owner,name,kind,sha = parts
            assert kind == 'tar.gz' and not authorization, 'Codeload received a bearer token'
            row = self.repositories[owner,name]; assert sha == row['sha']
            return 200,row['archive'],{}
        owner,name = parts[1:3]; suffix = parts[3:]; row = self.repositories[owner,name]
        if suffix == ['installation']: return 200,dict(id=row['id']),{}
        assert authorization == 'Bearer fixture-installation-token'
        if not suffix: return 200,dict(id=row['id'],private=True,full_name=owner+'/'+name,default_branch='main'),{}
        if suffix[0] == 'commits':
            assert suffix[1] in ('main',row['sha'])
            if suffix[1] == 'main':self.polls[row['id']] += 1
            if suffix[1] == 'main' and headers.get('if-none-match') == row['sha']: return 304,b'',{}
            return 200,dict(sha=row['sha'],commit=dict(tree=dict(sha=row['root']),message='Season fixture',committer=dict(date='2099-01-01T00:00:00Z'))),{'ETag':row['sha']}
        if suffix[:2] == ['git','trees']:
            tree = [dict(path='bot',sha=row['bot'],type='tree',mode='040000')] if suffix[2] == row['root'] else row['tree']
            assert suffix[2] in (row['root'],row['bot'])
            return 200,dict(tree=tree,truncated=False),{}
        if suffix[0] == 'tarball':
            assert suffix[1] == row['sha']
            return 302,b'',{'Location':f"https://codeload.github.com/{owner}/{name}/tar.gz/{row['sha']}"}
        if suffix == ['activity']:
            pushed = datetime.fromtimestamp(row['pushed_ms']/1000,timezone.utc).isoformat().replace('+00:00','Z')
            return 200,[dict(after=row['sha'],timestamp=pushed,ref='refs/heads/main')],{}
        if suffix[0] == 'statuses':
            assert method == 'POST' and suffix[1] == row['sha']
            self.statuses.append(dict(repo=row['id'],sha=row['sha'],state=body['state']))
            return 201,dict(id=len(self.statuses)),{}
        raise AssertionError('Unhandled fixture path: '+path)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(10)
