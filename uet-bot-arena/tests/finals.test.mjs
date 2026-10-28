import {test} from 'node:test';
import assert from 'node:assert/strict';
import {finalsCards,finalsForm,finalsPage,waitingStage} from '../dist/live/finals.js';
import {page} from '../dist/live/pages.js';
import {publicRoute} from '../dist/live/access.js';
import {exportForm,importedCards} from '../dist/live/imported-results.js';
const final={id:'f',name:'Final <unsafe>',status:'groups',teams:[],slots:{},profile:{match_ms:300000},started_rounds:['groups'],podium:[],pause_between_rounds:true};
test('public graph escapes names and contains fixed bracket slots and third place',()=>{
 const html=finalsPage({final,query:new URLSearchParams()});assert.doesNotMatch(html,/<unsafe>/);assert.match(html,/TK1|Tứ kết 1/);assert.match(html,/Tranh hạng ba/);assert.doesNotMatch(html,/data-command=/);assert.equal(publicRoute('finals',['f']),true);
 assert.match(finalsCards({finals:[final]}),/#\/finals\/f/);
});
test('projector bypasses sidebar; imported events never expose admin actions',()=>{
 const html=page({route:'finals',final,query:new URLSearchParams('chieu=1')});assert.match(html,/finals-projection/);assert.doesNotMatch(html,/sidebar|topbar/);
 const admin=finalsPage({final:{...final,status:'draft'},query:new URLSearchParams(),user:{role:'admin'}});assert.match(admin,/start-groups/);assert.match(admin,/data-command="stop"/);
 const imported=finalsPage({final,query:new URLSearchParams(),user:{role:'admin'},importedFinal:{imported_ms:1,sha256:'a'.repeat(64)}});assert.doesNotMatch(imported,/data-command=/);
});
test('form has sixteen online ranks, pause option, default five-minute clock; stage waits for every cup',()=>{
 const html=finalsForm({finalTeams:[]});assert.equal((html.match(/name="rank-/g)||[]).length,16);assert.match(html,/value="300000" selected/);assert.match(html,/pause_between_rounds/);
 const f={...final,slots:Object.fromEntries(['TK1','TK2','TK3','TK4'].map(k=>[k,{status:'locked'}]))};assert.equal(waitingStage(f),'quarter');delete f.slots.TK4;assert.equal(waitingStage(f),null);
});
test('export automatically selects completed native finals; imports link back to bracket',()=>{
 assert.match(exportForm({competitions:[],finals:[{...final,status:'completed'}]}),/name="finals" value="f"/);
 assert.doesNotMatch(exportForm({competitions:[],finals:[final]}),/name="finals"/);
 assert.match(importedCards({importedEvents:[{id:'e',name:'LAN',competitions:[],finals:[final]}]}),/#\/finals\/f/);
});
test('imported rank and score fields remain text even if the JSON contains HTML',()=>{
 const attack='<img src=x onerror=alert(1)>',row={team_id:'t',name:'Team',rank:attack,points:attack};
 const f={...final,teams:[{team_id:'t',name:'Team'}],slots:{A:{status:'completed',competition_id:'c',rows:[row]},TK1:{status:'completed',competition_id:'k',team_ids:['t'],rows:[row]}}};
 const html=finalsPage({final:f,query:new URLSearchParams()});assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});
