import test from 'node:test';
import assert from 'node:assert/strict';
import {APIError,ArenaAPI} from '../dist/live/client.js';

test('automatic API reads carry poll header through pagination, manual reads do not',async()=>{
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(url,options)=>{calls.push({url,...options});return Response.json({items:[],total:0});};
  try{
    const api=new ArenaAPI();api.admin=true;
    await api.request('/auth/me');await api.request('/ui/personal?view=team&poll=1',{poll:true});
    await api.all('/competitions',{poll:true});
    assert.equal(calls[0].headers['X-Arena-Poll'],undefined);
    assert.deepEqual(calls.slice(1).map(c=>c.headers['X-Arena-Poll']),['1','1']);
  }finally{globalThis.fetch=original;}
});

test('plain-text admission 503 explains overload and retains retry metadata',()=>{
  const response=new Response('Service Unavailable',{status:503,headers:{'Retry-After':'2','X-Request-ID':'test-request'}});
  const error=new APIError(response,'Service Unavailable');
  assert.equal(error.code,'server_busy');
  assert.equal(error.status,503);
  assert.match(error.message,/quá nhiều kết nối/);
  assert.equal(error.retryAfter,'2');
  assert.equal(error.requestId,'test-request');
});

test('structured worker 503 keeps its specific diagnosis',()=>{
  const error=new APIError(new Response('',{status:503}),{error:{code:'worker_unavailable'},request_id:'worker-request'});
  assert.equal(error.code,'worker_unavailable');
  assert.match(error.message,/Máy chấm/);
  assert.equal(error.requestId,'worker-request');
});

test('failed file download handles non-JSON 503 and performs no automatic retry',async()=>{
  const original=globalThis.fetch;
  let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response('Service Unavailable',{status:503,headers:{'Content-Type':'text/plain'}});};
  try{
    await assert.rejects(()=>new ArenaAPI().blob('/starter.zip'),error=>error instanceof APIError&&error.code==='server_busy');
    assert.equal(calls,1);
  }finally{globalThis.fetch=original;}
});

test('non-JSON gateway failure is reported as a server error',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>new Response('<html>Bad gateway</html>',{status:502,headers:{'Content-Type':'text/html'}});
  try{
    await assert.rejects(()=>new ArenaAPI().request('/jobs'),error=>error instanceof APIError&&error.code==='server_error'&&/Máy chủ/.test(error.message));
  }finally{globalThis.fetch=original;}
});
