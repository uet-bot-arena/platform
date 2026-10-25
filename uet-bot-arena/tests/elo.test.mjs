import assert from 'node:assert/strict';
import {test} from 'node:test';
import {eloStandings,eloProgress,eloTeam} from '../dist/live/elo.js';
import {readTournamentOptions} from '../dist/live/tournament-options.js';
import {setLanguage} from '../dist/live/i18n.js';
import {page} from '../dist/live/pages.js';

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
 const f=new FormData();f.set('format','elo');f.set('submission_deadline','2026-12-01T12:00');
 const options=readTournamentOptions(f);
 assert.deepEqual(options.ladder,{round_hours:2});
 assert.deepEqual(options.official,{general_rounds:20,group_size:64,finalists:16,k:[[20,32],[null,16]]});
 f.delete('submission_deadline');assert.throws(()=>readTournamentOptions(f),/hạn nộp/);
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
