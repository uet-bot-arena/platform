import assert from 'node:assert/strict';
import {test} from 'node:test';
import {eloCompetition,eloStandings,eloProgress,eloTeam} from '../dist/live/elo.js';
import {readTournamentOptions,tournamentOptions,ladderHours,ladderCalendar,ladderDuration} from '../dist/live/tournament-options.js';
import {setLanguage} from '../dist/live/i18n.js';
import {page} from '../dist/live/pages.js';

test('numeric ladder interval previews daily boundaries including the shorter last interval',()=>{
 assert.match(tournamentOptions(),/name="ladder-hours" type="number" min="1" max="24" step="1" value="2"/);
 assert.equal(ladderHours(2).length,12);assert.deepEqual(ladderHours(5),[0,5,10,15,20]);assert.deepEqual(ladderHours(24),[0]);
 assert.match(ladderCalendar(5),/00:00, 05:00, 10:00, 15:00, 20:00.*20:00 → 0:00 chỉ 4 giờ/);
 const start=Date.parse('2026-10-27T00:00:00+07:00');
 assert.deepEqual(ladderDuration(2,start+14*86400000,start),{days:14,rounds:168});
 assert.deepEqual(ladderDuration(5,start+86400000,start),{days:1,rounds:5});
 assert.deepEqual(ladderDuration(24,start+86400000,start),{days:1,rounds:1});
 assert.deepEqual(ladderDuration(5,start+86400000,start+20*3600000),{days:1/6,rounds:1});
 assert.equal(ladderDuration(0,start+86400000,start),null);
 setLanguage('en');try{assert.match(ladderCalendar(5),/last interval is shorter.*only 4 hours/);}finally{setLanguage('vi');}
});

test('round robin names the frozen rating from the stage that actually ran in either language',()=>{
 const standings={ranking:'round_robin_points',items:[],matrix:[],progress:{rounds:[{phase:'ladder'}]}};
 try{
  for(const [lang,ladder,general] of [['vi','Elo sau ladder','Elo vòng chung'],['en','Post-ladder Elo','General-stage Elo']]){
   setLanguage(lang);
   const render=rounds=>eloStandings({standings:{...standings,progress:{rounds}}});
   assert.match(render([{phase:'ladder'},{phase:'round_robin'}]),new RegExp(ladder));
   assert.doesNotMatch(render([{phase:'ladder'},{phase:'round_robin'}]),new RegExp(general));
   assert.match(render([{phase:'general'},{phase:'round_robin'}]),new RegExp(general));
  }
 }finally{setLanguage('vi');}
});

test('new Elo competition explains its fixed bank and hides the unrelated time input',()=>{
 const html=page({route:'competitions',ids:[],user:{role:'admin'},competitions:[]});
 assert.match(html,/data-elo-time>Ladder và xếp hạng chính thức: 10 giây mỗi bot/);
 assert.match(html,/data-competition-time hidden>/);
});

test('round robin table and both-color matrix link each exact game, escaping names',()=>{
 const state={competition:{id:'cup'},standings:{ranking:'round_robin_points',provisional:true,items:[
 {team_id:'a',name:'<script>',points:1.5,rank:1,played:2,head_to_head:0,wins:1,rating:1500},
 {team_id:'b',name:'B',points:.5,rank:2,played:2,head_to_head:0,wins:0,rating:1400},
 {team_id:'guest',name:'Guest hidden',is_guest:true,rating:1600}],matrix:[
 {id:'game1',red_team_id:'a',blue_team_id:'b',status:'completed',result:{winner:'red'}},
 {id:'game2',red_team_id:'b',blue_team_id:'a',status:'completed',result:{winner:null}}]}};
 const html=eloStandings(state);
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|Guest hidden|undefined|NaN/);
 assert.match(html,/#\/replay\/cup\/game1/);assert.match(html,/#\/replay\/cup\/game2/);
 assert.match(html,/ma trận|Ma trận/);assert.match(html,/Elo giữ nguyên/);
});
test('queued progress hides ETA while running has a range and fixed-boundary countdown',()=>{
 const p={server_ms:1000,next_round_ms:61000,current:{phase:'general',number:2,state:'queued',completed:0,total:20,started_ms:1000,eta:{lower_seconds:60,upper_seconds:120}}};
 assert.doesNotMatch(eloProgress(p),/còn khoảng/);assert.match(eloProgress(p),/Đang xếp hàng/);
 assert.match(eloProgress({...p,current:{...p.current,state:'running'}}),/1–2 phút/);
 assert.match(eloProgress(p),/00:01:00/);
});
test('team shows exact current and next SHA and only commits Elo after the full round',()=>{
 const html=eloTeam({user:{team_id:'a'},teamElo:{items:[{title:'Cup',competition_id:'cup',next_sha:'next',latest_sha:'push',check_state:'checking',current:{id:'r',phase:'ladder',number:1,status:'running',sha:'frozen',elo_before:1500,elo_after:null,games:[]}}]}});
 assert.match(html,/frozen/);assert.match(html,/next/);assert.match(html,/Đang kiểm tra/);assert.match(html,/chờ cả đợt xong/);
});
test('form serializes the approved 20/64/16 rule and two K tiers',()=>{
 const f=new FormData();f.set('format','elo');f.set('submission_deadline-date','2026-12-01');f.set('submission_deadline-time','12:00');
 const options=readTournamentOptions(f);
 assert.deepEqual(options.ladder,{round_hours:2});
 assert.deepEqual(options.official,{general_rounds:20,group_size:64,finalists:16,k:[[20,32],[null,16]]});
 f.delete('submission_deadline-date');assert.throws(()=>readTournamentOptions(f),/hạn nộp/);
});

test('team round-robin view explains frozen Elo while points are being played',()=>{
 const html=eloTeam({user:{team_id:'a'},teamElo:{items:[{title:'Cup',competition_id:'cup',ladder_open:false,current:{phase:'round_robin',number:1,status:'running',sha:'sha',elo_before:1600,elo_after:null,games:[]}}]}});
 assert.match(html,/Elo trước vòng tròn: 1600.00 · giữ nguyên/);
 assert.doesNotMatch(html,/chờ cả đợt xong/);
});

test('only BTC can exclude/restore and public table preserves a removed team without a rank',()=>{
 const data={ranking:'elo',items:[{team_id:'a',name:'Removed',rating:1500,rank:null,played:2,excluded:true}]};
 const publicHtml=eloStandings({standings:data});
 assert.match(publicHtml,/BTC loại khỏi xếp hạng/);assert.doesNotMatch(publicHtml,/data-action="elo-exclude"/);
 assert.match(eloStandings({user:{role:'admin'},standings:data}),/data-action="elo-exclude"/);
 assert.match(eloStandings({user:{role:'admin'},standings:data}),/Khôi phục/);
});

test('only BTC sees retry pairing on an open Elo competition with a pairing error',()=>{
 const competition={id:'cup',title:'Cup',status:'open',config:{format:'elo',ladder:{round_hours:1},official:{match_ms:10000,group_size:64,general_rounds:20,finalists:16},elo_error:'<bad pairing>'}};
 for(const [lang,label] of [['vi','Ghép lại đợt ladder'],['en','Retry ladder pairing']]){
  setLanguage(lang);
  const base={competition,standings:{items:[]}};
  const html=eloCompetition({...base,user:{role:'admin'}},'');assert.ok(html.includes(label));assert.match(html,/data-command="retry-pairing"/);assert.doesNotMatch(html,/<bad pairing>/);
  assert.doesNotMatch(eloCompetition({...base,user:{role:'student'}},''),/data-command="retry-pairing"/);
  assert.doesNotMatch(eloCompetition({...base,user:{role:'admin'},competition:{...competition,status:'running'}},''),/data-command="retry-pairing"/);
  assert.doesNotMatch(eloCompetition({...base,user:{role:'admin'},competition:{...competition,config:{...competition.config,elo_error:null}}},''),/data-command="retry-pairing"/);
 }
 setLanguage('vi');
});
