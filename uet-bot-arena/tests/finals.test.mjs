import {test} from 'node:test';
import assert from 'node:assert/strict';
import {finalCard,finalsCards,finalsForm,finalsPage,waitingStage,finalPairDetail} from '../dist/live/finals.js';
import {page,compCards} from '../dist/live/pages.js';
import {publicRoute} from '../dist/live/access.js';
import {exportForm} from '../dist/live/imported-results.js';
import {setLanguage} from '../dist/live/i18n.js';
import {backLink} from '../dist/live/view.js';
import {boardPage} from '../dist/live/board.js';
const final={id:'f',name:'Final <unsafe>',status:'groups',teams:[],slots:{},profile:{match_ms:300000},started_rounds:['groups'],podium:[],pause_between_rounds:true};
test('projector buttons belong only to native organiser detail; projector always has an exit',()=>{
 for(const user of [null,{role:'team'},{role:'admin'}]){
  assert.doesNotMatch(finalCard(final,undefined,user?.role==='admin'),/chieu=1/);
  assert.doesNotMatch(finalCard(final,{imported_ms:1,id:'e'},user?.role==='admin'),/chieu=1/);
  const native=finalsPage({final,user,query:new URLSearchParams()});assert.equal(native.includes('chieu=1'),user?.role==='admin');
  assert.doesNotMatch(finalsPage({final,user,importedFinal:{imported_ms:1,sha256:'a'.repeat(64)},query:new URLSearchParams()}),/chieu=1/);
 }
 const html=finalsPage({final,query:new URLSearchParams('chieu=1')});assert.match(html,/class="projection-exit" href="#\/finals\/f">× Thoát chiếu/);assert.doesNotMatch(html,/← Giải đấu/);
});
test('detail parents use fixed links for competitions, finals, checks and practice',()=>{
 const state={route:'competitions',ids:['c'],competitions:[],finals:[],jobs:[],submissions:[],fixtures:[],user:null,query:new URLSearchParams(),competition:{id:'c',title:'Cup',status:'completed',entries:[],fixture_counts:{completed:0},config:{format:'double_round_robin',profile:{match_ms:300000}}}};
 assert.match(page(state),/href="#\/competitions">← Giải đấu/);
 assert.match(page({...state,finals:[{...final,slots:{A:{competition_id:'c'}}}]}),/href="#\/finals\/f">← Final &lt;unsafe&gt;/);
 assert.match(finalsPage({final,query:new URLSearchParams()}),/href="#\/competitions">← Giải đấu/);
 for(const [kind,route,label] of [['benchmark','submissions','Nộp bot'],['match','practice','Luyện tập']]){
  const html=page({...state,route:'jobs',job:{id:'j',kind,status:'queued',submission_ids:['s'],profile:{match_ms:300000}}});assert.ok(html.includes(`href="#/${route}">← ${label}`));
 }
 assert.match(boardPage({title:'Replay',red:'A',blue:'B',back:'finals/f'}),/href="#\/finals\/f" class="btn secondary">Quay lại/);
 assert.equal(backLink('competitions','<parent>'),'<a class="text-link" href="#/competitions">← &lt;parent&gt;</a>');
 setLanguage('en');assert.match(finalsPage({final,query:new URLSearchParams('chieu=1')}),/× Exit projector view/);setLanguage('vi');
});
test('one card per final suppresses child cups for every role and keeps standalone imports',()=>{
 const f={...final,status:'completed',podium:['t'],teams:[{team_id:'t',name:'Winner'}],slots:{A:{competition_id:'child'}}};
 const cup={id:'regular',title:'Regular',status:'running',created_ms:1,config:{format:'double_round_robin',max_teams:4,profile:{match_ms:300000}}};
 for(const user of [null,{role:'team'},{role:'admin'}]){
  const html=compCards({user,competitions:[cup,{...cup,id:'child',title:'Must hide'}],finals:[f],importedEvents:[{id:'e',imported_ms:2,finals:[f],competitions:[{id:'child',title:'Must hide'},{id:'single',title:'Standalone',format:'double_round_robin',teams:4}]}]});
  assert.equal((html.match(/data-key="final-f"/g)||[]).length,1);assert.doesNotMatch(html,/Must hide|Kết quả từ hub chung kết/);assert.match(html,/Standalone/);assert.ok(html.indexOf('competition-regular')<html.indexOf('final-f'));
  assert.equal(html.includes('hide-imported'),user?.role==='admin');
 }
 assert.match(finalCard(f),/<h3>Final &lt;unsafe&gt;<\/h3>/);assert.doesNotMatch(finalCard(f),/<h3><a/);
});
test('pair detail uses snapshot games and explains new tie rules in both languages safely',()=>{
 const f={...final,teams:[{team_id:'a',name:'Alpha'},{team_id:'b',name:'<unsafe>'}],slots:{TK1:{competition_id:'c',team_ids:['a','b'],status:'completed',rows:[{team_id:'a',points:1},{team_id:'b',points:1}],result:{winner:'a',reason:'thắng nhanh hơn',detail:{winner_plies:44,loser_plies:64}},games:[{fixture_id:'g',ordinal:1,red_team_id:'a',blue_team_id:'b',winner:'red',reason:'base_capture',plies:44,pieces:{red:5,blue:4}}]}}};
 for(const language of ['vi','en']){
  setLanguage(language);const html=finalPairDetail(f,'TK1');assert.match(html,/#\/replay\/c\/g/);assert.doesNotMatch(html,/<unsafe>/);assert.match(html,/44 \/ 64/);assert.match(html,language==='vi'?/thắng nhanh hơn/:/faster win/);
  const graph=finalsPage({final:f,query:new URLSearchParams()});assert.match(graph,/data-action="final-pair"/);assert.match(graph,/<dialog/);assert.doesNotMatch(graph,/preserveAspectRatio|finals-rules/);
 }
 setLanguage('vi');
});
test('finals listing has a create button without the form and waiting slots do not click',()=>{
 const html=finalsPage({finals:[],user:{role:'admin'}});assert.match(html,/data-action="finals-create"/);assert.doesNotMatch(html,/id="finals-form"/);
 assert.doesNotMatch(finalsPage({final,query:new URLSearchParams()}),/data-action="final-pair"/);
 assert.match(finalsPage({final,query:new URLSearchParams()}),/Chờ đội thua Bán kết 1 và Bán kết 2/);
});
test('public graph escapes names and contains fixed bracket slots and third place',()=>{
 const html=finalsPage({final,query:new URLSearchParams()});assert.doesNotMatch(html,/<unsafe>/);assert.match(html,/TK1|Tứ kết 1/);assert.match(html,/Tranh hạng ba/);assert.doesNotMatch(html,/data-command=/);assert.equal(publicRoute('finals',['f']),true);
 assert.match(finalsCards({finals:[final]}),/#\/finals\/f/);
});
test('projector bypasses sidebar; imported events never expose admin actions',()=>{
 const html=page({route:'finals',final,query:new URLSearchParams('chieu=1')});assert.match(html,/finals-projection/);assert.doesNotMatch(html,/sidebar|topbar/);
 const admin=finalsPage({final:{...final,status:'draft'},query:new URLSearchParams(),user:{role:'admin'}});assert.match(admin,/start-groups/);assert.match(admin,/data-command="stop"/);
 const imported=finalsPage({final,query:new URLSearchParams(),user:{role:'admin'},importedFinal:{imported_ms:1,sha256:'a'.repeat(64)}});assert.doesNotMatch(imported,/data-command=/);
 assert.match(imported,/aaaaaaaaaaaa/);
 const importedProjection=page({route:'finals',final,query:new URLSearchParams('chieu=1'),importedFinal:{imported_ms:1,sha256:'a'.repeat(64)}});
 assert.match(importedProjection,/finals-projection/);assert.doesNotMatch(importedProjection,/aaaaaaaaaaaa/);
});
test('form has sixteen online ranks, pause option, default five-minute clock; stage waits for every cup',()=>{
 const html=finalsForm({finalTeams:[]});assert.equal((html.match(/name="rank-/g)||[]).length,16);assert.match(html,/value="300000" selected/);assert.match(html,/pause_between_rounds/);
 const f={...final,slots:Object.fromEntries(['TK1','TK2','TK3','TK4'].map(k=>[k,{status:'locked'}]))};assert.equal(waitingStage(f),'quarter');delete f.slots.TK4;assert.equal(waitingStage(f),null);
});
test('export automatically selects completed native finals; imports link back to bracket',()=>{
 assert.match(exportForm({competitions:[],finals:[{...final,status:'completed'}]}),/name="finals" value="f"/);
 assert.doesNotMatch(exportForm({competitions:[],finals:[final]}),/name="finals"/);
 assert.match(compCards({competitions:[],finals:[final],importedEvents:[{id:'e',name:'LAN',imported_ms:1,competitions:[],finals:[final]}]}),/#\/finals\/f/);
});
test('imported rank and score fields remain text even if the JSON contains HTML',()=>{
 const attack='<img src=x onerror=alert(1)>',row={team_id:'t',name:'Team',rank:attack,points:attack};
 const f={...final,teams:[{team_id:'t',name:'Team'}],slots:{A:{status:'completed',competition_id:'c',rows:[row]},TK1:{status:'completed',competition_id:'k',team_ids:['t'],rows:[row]}}};
 const html=finalsPage({final:f,query:new URLSearchParams()});assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});
