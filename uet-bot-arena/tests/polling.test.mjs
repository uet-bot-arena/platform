import test from 'node:test';
import assert from 'node:assert/strict';
import {visiblePoll,githubPollMs,VisibleSource} from '../dist/live/polling.js';

class Document {hidden=false;handlers=new Set();addEventListener(_,fn){this.handlers.add(fn);}removeEventListener(_,fn){this.handlers.delete(fn);}change(hidden){this.hidden=hidden;for(const fn of this.handlers)fn();}}
test('readers stop while hidden, resume immediately and use 3s only while checking/recent push',async()=>{
 const doc=new Document(),old=globalThis.setTimeout,timers=[];let calls=0;
 globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return 0;};
 try{
  let github={commits:[]};const poll=visiblePoll(async()=>{calls++;},()=>githubPollMs(github,200000),{document:doc,immediate:true});
  await new Promise(setImmediate);assert.equal(calls,1);assert.equal(timers.at(-1).ms,30000);
  doc.change(true);await timers.at(-1).fn();assert.equal(calls,1);
  github={commits:[{state:'checking'}]};doc.change(false);await new Promise(setImmediate);
  assert.equal(calls,2);assert.equal(timers.at(-1).ms,3000);assert.ok(timers.at(-1).ms<=5000);
  assert.equal(githubPollMs({commits:[{state:'pass',pushed_ms:199999}]},200000),3000);
  assert.equal(githubPollMs({commits:[{state:'pass',pushed_ms:1}]},200000),30000);
  poll.close();assert.equal(doc.handlers.size,0);
 }finally{globalThis.setTimeout=old;}
});
test('private SSE closes when hidden and resumes from its last event',()=>{
 const doc=new Document(),sources=[];
 class Source {constructor(path){this.path=path;sources.push(this);}addEventListener(type,fn){this[type]=fn;}close(){this.closed=true;}}
 const feed=new VisibleSource('/api/v1/jobs/job/events?after=0',{document:doc,Source});let count=0;
 feed.addEventListener('arena',()=>count++);sources[0].arena({lastEventId:'7'});assert.equal(count,1);
 doc.change(true);assert.equal(sources[0].closed,true);assert.equal(sources.length,1);
 doc.change(false);assert.equal(sources[1].path,'/api/v1/jobs/job/events?after=7');
 feed.close();assert.equal(doc.handlers.size,0);
});
