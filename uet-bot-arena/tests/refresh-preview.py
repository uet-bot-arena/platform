"""Opt-in browser regression fixture; synthetic API, no live account/database."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

DIST = Path(__file__).resolve().parents[1] / 'dist'
PROBE = r'''
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function ready(selector){
  for(let i=0;i<200;i++){const node=document.querySelector(selector);if(node)return node;await sleep(25);}
  throw new Error('Missing '+selector);
}
async function counts(){return (await fetch('/__qa/polls')).json();}
async function idleCheck(){
  const main=document.getElementById('main'),shell=document.querySelector('.app-shell');
  const button=document.querySelector('[data-action="refresh"]');button.focus();
  const before=await counts();let mutations=0;
  const observer=new MutationObserver(records=>mutations+=records.length);
  observer.observe(document.getElementById('app'),{subtree:true,childList:true,attributes:true,characterData:true});
  await sleep(12000);observer.disconnect();
  const after=await counts();
  return {noRequests:before.requests===after.requests,mutations,
    mainPreserved:main===document.getElementById('main'),shellPreserved:shell===document.querySelector('.app-shell'),
    focusPreserved:document.activeElement===button};
}
(async()=>{
  const result={};
  try{
    await ready('[data-key="job-b"] a');result.homeIdle=await idleCheck();
    const main=document.getElementById('main'),shell=document.querySelector('.app-shell');
    const original=document.querySelector('[data-key="job-a"]'),focused=document.querySelector('[data-key="job-b"] a');
    focused.focus();document.querySelector('[data-action="refresh"]').click();
    await ready('[data-key="job-c"]');await sleep(100);
    result.manualRefresh={mainPreserved:main===document.getElementById('main'),shellPreserved:shell===document.querySelector('.app-shell'),
      oldJobPreserved:original===document.querySelector('[data-key="job-a"]'),
      focusPreserved:focused===document.activeElement,newJobVisible:true,
      updatedStatus:document.querySelector('[data-key="job-a"] .badge')?.textContent,
      order:[...document.querySelectorAll('tbody tr')].map(n=>n.dataset.key)};
    location.hash='/standings';await ready('[name="competition-select"]');
    result.standingsIdle=await idleCheck();
    result.passed=[result.homeIdle,result.standingsIdle].every(x=>x.noRequests&&x.mutations===0&&x.mainPreserved&&x.shellPreserved&&x.focusPreserved)
      &&result.manualRefresh.mainPreserved&&result.manualRefresh.shellPreserved&&result.manualRefresh.oldJobPreserved&&result.manualRefresh.focusPreserved
      &&result.manualRefresh.order.join(',')==='job-c,job-b,job-a';
  }catch(e){result.error=e.message;result.passed=false;}
  const out=document.createElement('output');out.id='qa-result';out.textContent=JSON.stringify(result);document.body.append(out);
})();
'''


class Handler(BaseHTTPRequestHandler):
    polls = 0
    requests = 0
    def log_message(self, *args): pass
    def send(self, value, content_type='application/json'):
        raw = json.dumps(value).encode() if content_type=='application/json' else value.encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/v1/'):
            Handler.requests += 1
        if path=='/':
            return self.send((DIST/'live/index.html').read_text().replace('</body>',
                '<script type="module" src="/probe.js"></script></body>'), 'text/html')
        if path=='/probe.js': return self.send(PROBE,'text/javascript')
        if path=='/styles.css': return self.send((DIST/'styles.css').read_text(),'text/css')
        if path.startswith('/_ui/') and path.split('/')[-1] in {'time-control.js','app.js','access.js','experimental-ui.js', 'time-control.js','client.js','pages.js','view.js','board.js','patch.js','admin-ui.js','app.css','rules.js','rules.css','result.js','follow.js','standings.js','standings.css','bot-guide.js','bot-guide.md','bot-example.cpp','ottv2-input.jsonl'}:
            file = DIST/'live'/path.split('/')[-1]
            mime = {'.css':'text/css','.js':'text/javascript','.md':'text/plain','.cpp':'text/plain','.jsonl':'text/plain'}
            return self.send(file.read_text(encoding='utf-8'),mime[file.suffix])
        if path=='/api/v1/platform': return self.send({'version':'test'})
        if path=='/api/v1/auth/me': return self.send({'user':{'username':'fixture-team','role':'team','must_change_password':False,'team_id':'team'},'csrf_token':'synthetic'})
        if path=='/api/v1/teams/me': return self.send({'name':'UI Regression'})
        if path=='/api/v1/submissions': return self.send({'items':[]})
        if path=='/api/v1/competitions': return self.send({'items':[]})
        if path=='/api/v1/jobs':
            Handler.polls += 1
            def job(ident,status):
                return {'id':ident,'kind':'benchmark','status':status,'created_unix_ms':1791644400000}
            items=[job('a','queued'),job('b','completed')]
            if Handler.polls>=2: items=[job('c','benchmarking'),job('b','completed'),job('a','queued')]
            if Handler.polls>=2: items[-1]['status']='completed'
            return self.send({'items':items})
        if path=='/api/v1/dashboard':
            return self.send({'counts':{'submissions':2,'benchmarks':3 if Handler.polls>=3 else 2,
                'matches':0,'active_jobs':0 if Handler.polls>=4 else 1}})
        if path=='/__qa/polls': return self.send({'polls':Handler.polls,'requests':Handler.requests})
        self.send_response(404);self.end_headers()


if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print(f'http://127.0.0.1:{server.server_port}/#/home',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
