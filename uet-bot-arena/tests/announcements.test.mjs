import assert from 'node:assert/strict';
import {test} from 'node:test';
import {startAnnouncements} from '../dist/live/announcements.js';
import {announcementsPage} from '../dist/live/notices-ui.js';

test('banner uses text nodes, preserves unchanged links and refreshes within one minute',async()=>{
 class Element {children=[];hidden=true;append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}}
 const target=new Element(),old={document:globalThis.document,setTimeout:globalThis.setTimeout,addEventListener:globalThis.addEventListener};let interval;
 const payload='<img src=x onerror=alert(1)>';
 let data={items:[{id:'notice',revision:1,title:payload,body:payload}]};
 Object.assign(globalThis,{document:{getElementById:()=>target,createElement:()=>new Element(),addEventListener(){}},setTimeout:(fn,ms)=>interval=ms,addEventListener(){}});
 try{
  const refresh=startAnnouncements({snapshot:async name=>{assert.equal(name,'announcements.json');return data;}});
  await new Promise(setImmediate);
  assert.equal(target.hidden,false);assert.equal(target.children[0].textContent,payload);
  assert.equal(target.children[1].textContent,payload);assert.equal(target.children[2].href,'#/announcements');
  assert.equal(target.innerHTML,undefined);assert.ok(interval<=60000);
  const link=target.children[2];await refresh();assert.equal(target.children[2],link);
  data={items:[]};await refresh();assert.equal(target.hidden,true);
 }finally{for(const [key,value] of Object.entries(old)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
});

test('notice page escapes content and gives posting controls only to BTC',()=>{
 const item={id:'safe-id',title:'<svg/onload=alert(1)>',body:'<img src=x onerror=alert(1)>',revision:1,archived_ms:null,created_unix_ms:1};
 const guest=announcementsPage({user:null,announcements:[item]});
 assert.doesNotMatch(guest,/<svg\/onload|<img src|announcement-form/);assert.match(guest,/&lt;img/);
 const admin=announcementsPage({user:{role:'admin'},adminAnnouncements:[item]});
 assert.match(admin,/announcement-form/);assert.match(admin,/notice-visibility/);
});
