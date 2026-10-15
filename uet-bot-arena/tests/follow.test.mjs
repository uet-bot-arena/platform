import assert from 'node:assert/strict';
import {CompetitionFollow} from '../dist/live/follow.js';

class Element extends EventTarget {checked=true;hidden=false;textContent='';href='';}
class Feed extends EventTarget {closed=false;close(){this.closed=true;}emit(type,data){const e=new Event(type);e.data=JSON.stringify(data);this.dispatchEvent(e);}}
function fixture(id,ordinal,status){return {id,ordinal,status};}
function setup(){
  const nodes=Object.fromEntries(['follow-competition','competition-follow-message','competition-next','competition-review'].map(id=>[id,new Element()]));
  globalThis.document={getElementById:id=>nodes[id]};
  const feed=new Feed(),switches=[];let expired=false;
  const follow=new CompetitionFollow({stream:path=>{assert.equal(path,'/competitions/cup/watch');return feed;}},
    {competitionId:'cup',fixtureId:'a',onSwitch:(next,previous)=>switches.push([previous,next.id]),onExpired:()=>expired=true});
  follow.start();
  const state=(status,fixtures)=>feed.emit('competition',{status,fixtures});
  return {follow,feed,nodes,switches,state,expired:()=>expired};
}
{
  const x=setup();x.state('running',[fixture('a',1,'queued'),fixture('b',2,'pending')]);
  assert.deepEqual(x.switches,[]);
  x.state('running',[fixture('a',1,'completed'),fixture('b',2,'pending')]);assert.deepEqual(x.switches,[]);
  x.state('running',[fixture('a',1,'completed'),fixture('b',2,'queued')]);assert.deepEqual(x.switches,[['a','b']]);
  x.state('running',[fixture('a',1,'completed'),fixture('b',2,'queued')]);assert.equal(x.switches.length,1);
  assert.equal(x.nodes['competition-review'].href,'#/replay/cup/b');
  x.state('completed',[fixture('a',1,'completed'),fixture('b',2,'completed')]);assert.ok(x.feed.closed);
  assert.match(x.nodes['competition-follow-message'].textContent,/hoàn tất/);x.follow.close();
}
{
  const x=setup();x.follow.pause();x.state('running',[fixture('a',1,'completed'),fixture('b',2,'queued')]);
  assert.deepEqual(x.switches,[]);assert.equal(x.nodes['follow-competition'].checked,false);
  assert.equal(x.nodes['competition-next'].href,'#/arena/cup/b');
  x.nodes['follow-competition'].checked=true;x.nodes['follow-competition'].dispatchEvent(new Event('change'));
  assert.deepEqual(x.switches,[['a','b']]);x.follow.close();
}
{
  const x=setup();x.state('completed',[fixture('a',1,'completed'),fixture('b',2,'completed'),fixture('c',3,'completed')]);
  assert.deepEqual(x.switches,[['a','c']]);assert.ok(x.feed.closed);x.follow.close();
}
{
  const x=setup();x.state('paused',[fixture('a',1,'attention'),fixture('b',2,'pending')]);
  assert.deepEqual(x.switches,[]);assert.match(x.nodes['competition-follow-message'].textContent,/tạm dừng/);
  x.state('running',[fixture('a',1,'attention'),fixture('b',2,'queued')]);assert.deepEqual(x.switches,[['a','b']]);
  x.feed.emit('auth_expired',{});assert.ok(x.expired());assert.ok(x.feed.closed);
  x.state('running',[fixture('b',2,'completed'),fixture('c',3,'queued')]);assert.equal(x.switches.length,1);
}
console.log('PASS follow: wait, switch once, stop, opt out/resume, fast finish, pause, fault, auth/disposal');
