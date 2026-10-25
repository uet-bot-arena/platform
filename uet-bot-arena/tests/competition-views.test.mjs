import {participantData} from '../dist/live/page-data.js';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {competitionFilter,competitionSummary,participationLabel,competitionRoute,teamCompetitions} from '../dist/live/competition-ui.js';
import {setLanguage} from '../dist/live/i18n.js';

test('competition filter keeps scope in URL and omits archived competitions',()=>{
 assert.equal(competitionRoute('admin?tab=accounts','cup'), 'admin?tab=accounts&competition=cup');
 assert.equal(competitionRoute('users?competition=cup',''), 'users');
 const html=competitionFilter({competitions:[{id:'cup',title:'Demo'},{id:'old',title:'Archived',archived_ms:1}],selectedCompetition:'cup'});
 assert.match(html,/name="competition-scope"/);assert.match(html,/selected[^>]*>Demo/);assert.doesNotMatch(html,/Archived/);
});
test('shared state labels distinguish active, excluded, waiting and missing requirements in VI and EN',()=>{
 assert.equal(participationLabel({state:'active'}),'Đang đấu');assert.match(participationLabel({state:'ineligible',reasons:['team_size','repository','passed_bot']}),/Chưa đủ 4 người; Chưa gắn repo; Chưa có bản Đạt/);
 setLanguage('en');try{assert.equal(participationLabel({state:'excluded'}),'Excluded');assert.match(participationLabel({state:'eligible'}),/Eligible/);}finally{setLanguage('vi');}
});
test('team competition panel shows rank, Elo, points and safe open links',()=>{
 const html=teamCompetitions({user:{team_id:'mine'},teamCompetitions:{items:[{id:'cup',title:'<script>',state:'active',rank:2,rating:1501.5,points:4,played:6,status:'completed'}]}});
 for(const text of ['Giải của đội','Elo 1501.50','4 điểm','Đang đấu','Mở giải'])assert.ok(html.includes(text));assert.doesNotMatch(html,/<script>/);
 const empty=teamCompetitions({user:{},competitions:[{id:'open',title:'Open',status:'open'},{id:'closed',title:'Closed',status:'completed'}]});assert.match(empty,/Cần đội đủ 4 người/);assert.match(empty,/Open/);assert.doesNotMatch(empty,/Closed/);
});
test('admin competition summary exposes scoped counts, reasons and progress',()=>{
 const html=competitionSummary({competitionSummary:{id:'cup',title:'Demo',format:'elo',status:'open',counts:{active:4,eligible:1,ineligible:2,excluded:0},reasons:{repository:2},fixtures:{completed:12,waiting:4,attention:0},queue:{queued:4},progress:{current:{phase:'ladder',number:2},next_round_ms:1}}});
 for(const text of ['Đang đấu','Đủ điều kiện, chờ đợt sau','Chưa gắn repo: 2','12 / 4 / 0','Ladder 2'])assert.ok(html.includes(text));
});

test('a person without a team reads open competitions from the public snapshot',async()=>{
 const calls=[],files=[];
 const api={request:async p=>{calls.push(p);return {team:null};},snapshot:async p=>{files.push(p);return {items:[{id:'open',title:'Open',status:'open'}]};}};
 const data=await participantData(api,{},'team',[],new URLSearchParams());
 assert.deepEqual(calls,['/users/me']);assert.deepEqual(files,['competitions.json']);assert.match(teamCompetitions(data),/Open/);
});
