import test from 'node:test';
import assert from 'node:assert/strict';
import {page} from '../dist/live/pages.js';
import {practiceDefaults,quickPractice} from '../dist/live/quick-practice.js';
import {peopleAdminPage} from '../dist/live/people-ui.js';
import {updateTournamentForm,tournamentOptions} from '../dist/live/tournament-options.js';
import {setLanguage} from '../dist/live/i18n.js';
const base={route:'practice',ids:[],user:{role:'admin'},platform:{practice_upload:true},submissions:[],jobs:[],sampleBots:[{id:'starter',name:'Bot mẫu',protocol_version:2}]};
const source=(id,time)=>({id,version:'thu-'+id,channel:'practice',runtime:'python3',protocol_version:2,created_unix_ms:time});
const check=(id,status='completed',passed=true)=>({id:'job-'+id,kind:'benchmark',submission_ids:[id],status,benchmark_summary:{passed,samples:72}});
test('empty practice page starts with quick upload and has no dead-end empty-bot message',()=>{
 for(const role of ['admin','student']){
  const html=page({...base,user:{role}});assert.match(html,/quick-practice-form/);assert.match(html,/practice-file/);assert.doesNotMatch(html,/Chưa có bot|id="practice-form"/);assert.match(html,/\.py\/\.c\/\.cpp\/\.zip/);
 }
});
test('practice selects newest passed source and starter without filtering which sources may fight',()=>{
 const s={...base,submissions:[source('older',1),source('latest',3),source('checking',4),source('bad',5)],jobs:[check('older'),check('latest'),check('checking','benchmarking'),check('bad','completed',false)]};
 assert.deepEqual(practiceDefaults(s),{red:'latest',blue:'sample:starter'});
 const html=page(s),red=html.match(/<select[^>]+name="red_submission_id".*?<\/select>/s)[0],blue=html.match(/<select[^>]+name="blue_submission_id".*?<\/select>/s)[0];
 assert.match(red,/<option value="latest" selected/);assert.match(blue,/<option value="sample:starter" selected/);assert.match(red,/value="bad"/);
 for(const [id,label] of [['latest','Đạt'],['checking','Đang kiểm'],['bad','Lỗi']])assert.match(quickPractice(s),new RegExp(`data-key="practice-source-${id}"[^]*?${label}[^]*?jobs/job-${id}`));
 setLanguage('en');try{assert.match(page(s),/Paste code or select/);}finally{setLanguage('vi');}
});
test('Swiss rounds hide and disable outside Swiss, and Elo keeps supported board rules',()=>{
 assert.match(tournamentOptions(),/data-swiss-rounds hidden/);assert.match(tournamentOptions(),/name="custom-basic"/);
 const nodes=new Map(),form={id:'competition-form',dataset:{},elements:{format:{value:'elo'},max_teams:{},rounds:{}},querySelector:q=>{if(!nodes.has(q))nodes.set(q,{querySelectorAll:()=>[]});return nodes.get(q);}},Original=globalThis.FormData;globalThis.FormData=class{get(){return null;}};
 try{updateTournamentForm(form);assert.equal(nodes.get('[data-swiss-rounds]').hidden,true);assert.equal(form.elements.rounds.disabled,true);form.elements.format.value='swiss';updateTournamentForm(form);assert.equal(nodes.get('[data-swiss-rounds]').hidden,false);assert.equal(form.elements.rounds.disabled,false);}finally{globalThis.FormData=Original;}
});
test('captain row only offers account lock; members retain transfer and removal',()=>{
 const html=peopleAdminPage({people:[{id:'c',role:'staff',display_name:'Captain',team_id:'t'},{id:'m',role:'student',display_name:'Member',team_id:'t'}],teamChoices:[{id:'t',name:'Team',roster:[{id:'c',captain:1},{id:'m',captain:0}]}]});
 const rows=[...html.matchAll(/<tr><td>.*?<\/tr>/g)].map(x=>x[0]),captain=rows.find(r=>r.includes('<td>Captain</td>')),member=rows.find(r=>r.includes('<td>Member</td>'));
 assert.match(captain,/person-lock/);assert.doesNotMatch(captain,/person-remove|person-captain|person-judge/);assert.match(member,/person-remove/);assert.match(member,/person-captain/);
});
