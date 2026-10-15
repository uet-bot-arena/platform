"""Browser fixture for live fixture handoff; synthetic server, no real accounts/DB."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'arena-backend'))
from arena import engine

PROBE = r'''
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ready(selector){for(let i=0;i<300;i++){const e=document.querySelector(selector);if(e)return e;await sleep(25);}throw Error(selector);}
(async()=>{const result={};try{
  const toggle=await ready('#follow-competition');await ready('#live-board .board-square');
  const board=document.getElementById('live-board'),main=document.getElementById('main'),shell=document.querySelector('.app-shell');
  const cells=[...board.children];toggle.click();await sleep(4200);
  result.optOut=location.hash.endsWith('/first')&&!toggle.checked;
  toggle.click();await sleep(300);
  result.switched=location.hash.endsWith('/second');
  result.boardPreserved=board===document.getElementById('live-board')&&cells.every((cell,i)=>cell===board.children[i]);
  result.shellPreserved=main===document.getElementById('main')&&shell===document.querySelector('.app-shell');
  result.players=[...document.querySelectorAll('.match-player > strong')].map(n=>n.textContent);
  document.querySelector('[data-action="board-first"]').click();
  result.reviewDisablesAuto=!toggle.checked;toggle.click();await sleep(2500);
  result.completed=document.getElementById('competition-follow-message').textContent.includes('hoàn tất');
  document.getElementById('competition-review').click();await sleep(500);
  result.replayFixed=!document.getElementById('follow-competition')&&location.hash==='#/replay/fixture-cup/second';
  result.passed=result.optOut&&result.switched&&result.boardPreserved&&result.shellPreserved&&result.reviewDisablesAuto&&result.completed&&result.replayFixed&&result.players.join(',')==='Fixture Blue,Fixture Red';
}catch(e){result.error=e.message;result.passed=false;}
const out=document.createElement('output');out.id='qa-result';out.textContent=JSON.stringify(result);document.body.append(out);
})();
'''

class Handler(BaseHTTPRequestHandler):
    started = None
    def log_message(self, *args): pass
    def send(self, value, kind='application/json'):
        raw = (json.dumps(value) if kind=='application/json' else value).encode()
        self.send_response(200); self.send_header('Content-Type', kind)
        self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def event(self, kind, data):
        self.wfile.write(('event: '+kind+'\ndata: '+json.dumps(data)+'\n\n').encode()); self.wfile.flush()
    def stream(self):
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
    def snapshot(self):
        elapsed = time.monotonic()-Handler.started if Handler.started else 0
        result={'winner':None,'reason':'threefold_repetition'}
        rows=[{'id':name,'competition_id':'fixture-cup','ordinal':i+1,'red_team_id':'red' if i==0 else 'blue',
          'blue_team_id':'blue' if i==0 else 'red','status':'completed' if elapsed>=(3 if i==0 else 6) else 'queued' if i==0 or elapsed>=3 else 'pending',
          'result':result if elapsed>=(3 if i==0 else 6) else None} for i,name in enumerate(('first','second'))]
        return {'competition_id':'fixture-cup','status':'completed' if elapsed>=6 else 'running','fixtures':rows}
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/':return self.send((ROOT/'dist/live/index.html').read_text().replace('</body>','<script type="module" src="/probe.js"></script></body>'),'text/html')
        if path=='/probe.js':return self.send(PROBE,'text/javascript')
        if path=='/styles.css':return self.send((ROOT/'dist/styles.css').read_text(),'text/css')
        if path.startswith('/_ui/'):
            file=ROOT/'dist/live'/path.removeprefix('/_ui/')
            if file.parent==ROOT/'dist/live' and file.is_file():return self.send(file.read_text(),'text/css' if file.suffix=='.css' else 'text/javascript')
        comp={'id':'fixture-cup','title':'Fixture · Tự chuyển trận','status':self.snapshot()['status'],
          'entries':[{'team_id':side,'name':'Fixture '+side.title(),'version':'v0.0.0'} for side in ('red','blue')]}
        if path=='/api/v1/platform':return self.send({'version':'fixture'})
        if path=='/api/v1/auth/me':return self.send({'user':{'username':'fixture','role':'team','team_id':'red','must_change_password':False},'csrf_token':'fixture'})
        if path=='/api/v1/teams/me':return self.send({'name':'Fixture Red'})
        if path in ('/api/v1/submissions','/api/v1/jobs'):return self.send({'items':[]})
        if path=='/api/v1/competitions':return self.send({'items':[comp]})
        if path=='/api/v1/competitions/fixture-cup':return self.send(comp)
        if path.endswith('/standings'):return self.send({'items':[]})
        if path=='/api/v1/competitions/fixture-cup/fixtures':return self.send({'items':self.snapshot()['fixtures']})
        if path=='/api/v1/time':
            now=time.time_ns()//1_000_000
            return self.send({'server_received_unix_ms':now,'server_sent_unix_ms':now,'clock_id':'fixture'})
        try:
            if path.endswith('/watch'):
                if Handler.started is None:Handler.started=time.monotonic()
                self.stream();self.event('competition',self.snapshot())
                for deadline in (3.1,6.1):
                    time.sleep(max(0,Handler.started+deadline-time.monotonic()));self.event('competition',self.snapshot())
                return
            if path.endswith('/events'):
                second='/second/' in path
                state=engine.initial_state()
                if second:
                    for move in ({'from':'a2','to':'b3'},{'from':'b9','to':'a9'}):state=engine.apply(state,move)
                events=[{'seq':1,'type':'state','data':{'state':state,'counts':engine.counts(state)}}]
                terminal=self.snapshot()['fixtures'][1 if second else 0]['status']=='completed'
                final=engine.finish(deepcopy(state),None,'threefold_repetition')
                finish=[{'seq':2,'type':'state','data':{'state':final,'counts':engine.counts(final)}},
                    {'seq':3,'type':'completed','data':{'result':final['result']}}]
                if 'text/event-stream' not in self.headers.get('Accept',''):
                    return self.send({'attempt_id':'attempt-second' if second else 'attempt-first','items':events+(finish if terminal else []),'next_after':3 if terminal else 1})
                self.stream();time.sleep(max(0,(Handler.started or time.monotonic())+(6 if second else 3)-time.monotonic()))
                for event in finish:self.event('arena',event)
                return
        except (BrokenPipeError,ConnectionResetError):return
        self.send_response(404);self.end_headers()

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print(f'http://127.0.0.1:{server.server_port}/#/arena/fixture-cup/first',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
