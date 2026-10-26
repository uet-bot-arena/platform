import assert from 'node:assert/strict';
import {test} from 'node:test';
import {page} from '../dist/live/pages.js';
import {setLanguage} from '../dist/live/i18n.js';
import {uuid} from '../dist/live/client.js';

test('idempotency keys work when HTTP LAN has no randomUUID',()=>{
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'crypto'),native=globalThis.crypto;
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{getRandomValues:native.getRandomValues.bind(native)}});
  try{const a=uuid(),b=uuid();assert.match(a,/^[a-f0-9]{32}$/);assert.notEqual(a,b);}finally{Object.defineProperty(globalThis,'crypto',descriptor);}
});

test('LAN ZIP form replaces GitHub only when enabled, in both languages',()=>{
  try{for(const lang of ['vi','en']){
    setLanguage(lang);
    const state={route:'submissions',ids:[],user:{team_id:'t'},jobs:[],submissions:[],platform:{team_zip_upload:true}};
    const html=page(state);
    for(const name of ['zip-form','name="version"','name="runtime"','name="source"','type="file"'])assert.ok(html.includes(name));
    assert.match(html,lang==='vi'?/Hub chung kết không có GitHub/:/finals hub has no GitHub/);
    assert.doesNotMatch(html,/github-attach-form/);
    assert.doesNotMatch(page({...state,platform:{team_zip_upload:false}}),/zip-form/);
  }}finally{setLanguage('vi');}
});

test('only organizer sees add-team form before lock and LAN points use 1 and half',()=>{
  const competition={id:'cup',title:'LAN group',status:'draft',config:{format:'double_round_robin',max_teams:4,scoring:{win:1,draw:.5,loss:0},profile:{match_ms:300000}},entries:[],fixture_counts:{completed:0},revision:1};
  const state={route:'competitions',ids:['cup'],user:{role:'admin'},competition,entryTeams:[{id:'t',name:'<team>'}],jobs:[],fixtures:[],submissions:[]};
  let html=page(state);assert.match(html,/admin-entry-form/);assert.match(html,/&lt;team&gt;/);assert.match(html,/Thắng 1đ/);assert.doesNotMatch(html,/Thắng 3đ/);
  assert.doesNotMatch(page({...state,user:{role:'student'}}),/admin-entry-form/);
  assert.doesNotMatch(page({...state,competition:{...competition,status:'locked'}}),/admin-entry-form/);
});
