import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ArenaAPI} from '../dist/live/client.js';
import {PublicFeed,publicRequest,snapshotPath} from '../dist/live/public-client.js';
import {page} from '../dist/live/pages.js';

test('guests use public files; signed-in and private reads keep authenticated API',async()=>{
 const original=globalThis.fetch,calls=[];
 globalThis.fetch=async(url,options)=>{calls.push([url,options]);return new Response(JSON.stringify({items:[]}),{headers:{'Content-Type':'application/json'}});};
 try{
  const api=new ArenaAPI();await api.request('/competitions');
  assert.equal(calls[0][0],'/snapshots/competitions.json');assert.equal(calls[0][1].credentials,'omit');
  api.csrf='test-only';await api.request('/competitions');
  assert.equal(calls[1][0],'/api/v1/competitions');
  api.csrf=null;await api.request('/jobs');assert.equal(calls[2][0],'/api/v1/jobs');
 }finally{globalThis.fetch=original;}
});

test('all guest viewers share one featured file request per refresh interval',async()=>{
 const api=new ArenaAPI();let calls=0;api.snapshot=async()=>{calls++;return {boards:[]};};
 await Promise.all(Array.from({length:100},()=>api.featured()));assert.equal(calls,1);
 api.featuredValue.at-=2000;await api.featured();assert.equal(calls,2);
});

test('static replay respects cursor and public watch combines published fixtures',async()=>{
 const api={snapshot:async name=>name.startsWith('competitions/')?{id:'cup',status:'running'}:name==='fixtures/cup.json'?{items:[{id:'f'}]}:{attempt_id:'a',next_after:3,items:[{seq:1},{seq:3}]}};
 assert.deepEqual((await publicRequest(api,'/competitions/cup/fixtures/f/events?after=1')).items,[{seq:3}]);
 assert.deepEqual(await publicRequest(api,'/competitions/cup/watch'),{competition_id:'cup',status:'running',fixtures:[{id:'f'}]});
 assert.equal(snapshotPath('/competitions/cup/fixtures/f/events'),'fixtures/cup/f.json');
 assert.equal(snapshotPath('/admin/jobs'),null);
});

test('polled board deduplicates events, announces retry and releases its timer',async()=>{
 let data={attempt_id:'a',next_after:2,items:[{seq:1,type:'state'},{seq:2,type:'completed'}]};
 const api={featured:async()=>({boards:[{competition_id:'cup',fixture_id:'f',...data}]})};
 const feed=new PublicFeed(api,'/competitions/cup/fixtures/f/events?after=1'),events=[],attempts=[],retries=[];
 feed.addEventListener('arena',e=>events.push(JSON.parse(e.data).seq));
 feed.addEventListener('attempt',e=>attempts.push(JSON.parse(e.data).attempt_id));
 feed.addEventListener('attempt_changed',()=>retries.push(true));
 await new Promise(setImmediate);clearTimeout(feed.timer);
 assert.deepEqual(events,[2]);assert.deepEqual(attempts,['a']);
 await feed.tick();clearTimeout(feed.timer);assert.deepEqual(events,[2]);
 data={...data,attempt_id:'b'};await feed.tick();assert.deepEqual(retries,[true]);
 feed.close();assert.equal(feed.closed,true);
});

test('large board offers a separate team lookup and public queue reveals no grader status',()=>{
 const base={route:'standings',ids:[],loading:false,user:null,competitions:[],competition:{id:'cup',title:'Ladder',entries:[{team_id:'t',name:'Fixture <script>'}],config:{format:'elo'}},standings:{total:101,items:[],ranking:'elo',progress:{rounds:[]},provisional:true}};
 const html=page(base);assert.match(html,/id="standings-lookup"/);assert.match(html,/100 đội đầu/);assert.doesNotMatch(html,/<script>/);
 const queue=page({...base,route:'queue',queue:{waiting:{official:3,checks:4,practice:5},running:0,slots:0,generated_ms:1}});
 assert.match(queue,/Đang chờ kiểm tra/);assert.doesNotMatch(queue,/máy chấm.*tắt|ETA/i);
});


test('featured summary fetches full static history without dropping intermediate replay moves',async()=>{
 const events=[1,2,3].map(seq=>({seq,type:'state',data:{}}));let reads=0;
 const api={featured:async()=>({boards:[{competition_id:'cup',fixture_id:'f',attempt_id:'a',summary_only:true,next_after:3,items:[events[2]]}]}),snapshot:async()=>{reads++;return {attempt_id:'a',next_after:3,items:events};}};
 const feed=new PublicFeed(api,'/competitions/cup/fixtures/f/events'),seen=[];
 feed.addEventListener('arena',e=>seen.push(JSON.parse(e.data).seq));
 await new Promise(setImmediate);clearTimeout(feed.timer);
 assert.deepEqual(seen,[1,2,3]);assert.equal(reads,1);
 await feed.tick();clearTimeout(feed.timer);assert.deepEqual(seen,[1,2,3]);assert.equal(reads,1);feed.close();
});


test('guest watching a non-featured active board signals interest without SSE',async()=>{
  const calls=[];
  const api={featured:async()=>({boards:[]}),snapshot:async()=>({fixture:{status:'queued'},items:[],attempt_id:'job',next_after:0}),request:async path=>{calls.push(path);return {ok:true};}};
  const feed=new PublicFeed(api,'/competitions/c/fixtures/f/events');
  await new Promise(resolve=>setTimeout(resolve,20));feed.close();
  assert.deepEqual(calls,['/competitions/c/fixtures/f/live-interest']);
});
