import test from 'node:test';
import assert from 'node:assert/strict';
import {matchingPeople,availableTeams} from '../dist/live/team-search.js';
import {peopleAdminPage,teamChoiceButtons} from '../dist/live/people-ui.js';
import {adminTeamsBody,managedTeams} from '../dist/live/admin-ui.js';
import {tournamentOptions,rankingPreview,readTournamentOptions,updateTournamentForm} from '../dist/live/tournament-options.js';
import {shell} from '../dist/live/view.js';
import {setLanguage} from '../dist/live/i18n.js';
const people=Array.from({length:53},(_,i)=>({id:'u'+i,role:'student',display_name:'Người '+i,email:`${22025500+i}@vnu.test`,team_id:i===26?'t':null}));
const teamChoices=[{id:'t',name:'NHÓM 5',roster:[{id:'u26',display_name:'Captain',email:'22025526@vnu.test',captain:1}],member_count:4,team_size:4},{id:'free',name:'Đội còn chỗ',roster:[{display_name:'A < B',captain:1}],member_count:3,team_size:4}];
test('people search by MSSV and team, membership filter, fifty-row cap and remaining hint',()=>{
 const s={people,teamChoices,peopleSearch:'22025526'};
 assert.deepEqual(matchingPeople(s).map(p=>p.id),['u26']);assert.deepEqual(matchingPeople({...s,peopleSearch:'NHÓM 5'}).map(p=>p.id),['u26']);
 assert.equal(matchingPeople({...s,peopleSearch:'',peopleTeamFilter:'without'}).length,52);
 const html=peopleAdminPage({...s,peopleSearch:''});assert.match(html,/Còn 3 người/);assert.match(html,/tìm trong 53 người/);assert.match(html,/person-create-team/);assert.doesNotMatch(html,/admin-membership-form|name="user-id"/);
});
test('team finder offers only teams with room, searches member email and caps at eight',()=>{
 assert.deepEqual(availableTeams({people,teamChoices}).map(t=>t.id),['free']);assert.equal(availableTeams({people,teamChoices},'22025526').length,0);
 const html=teamChoiceButtons({people,teamChoices});assert.match(html,/3\/4 người/);assert.match(html,/A &lt; B/);assert.doesNotMatch(html,/<select|<datalist/);
 assert.equal(availableTeams({teamChoices:Array.from({length:20},(_,i)=>({...teamChoices[1],id:String(i)}))}).length,8);
});
test('team list unifies personal teams and common accounts, searches member MSSV and account username',()=>{
 const accounts=[{id:'a',username:'doi5',team_id:'shared',team:{name:'ĐỘI THỬ 5',members:[]}}];
 const s={accounts,people,teamChoices,adminTab:'accounts',adminSearch:'22025526'};
 assert.equal(managedTeams(s).length,3);let html=adminTeamsBody(s);assert.match(html,/NHÓM 5/);assert.doesNotMatch(html,/ĐỘI THỬ 5/);assert.match(html,/student-team-detail|Chỉnh thành viên/);assert.match(html,/Đội sinh viên/);
 html=adminTeamsBody({...s,adminSearch:'doi5'});assert.match(html,/ĐỘI THỬ 5/);assert.match(html,/Tài khoản chung/);
 assert.doesNotMatch(adminTeamsBody({...s,adminVisibility:'deleted'}),/NHÓM 5/);
});
test('competition exposes Elo settings, separates capacity and selection, and publishes a warning for non-LAN counts',()=>{
 const fields=new FormData();fields.set('format','elo');fields.set('submission_deadline-date','2026-11-05');fields.set('submission_deadline-hour','12');fields.set('submission_deadline-minute','30');fields.set('finalists','4');fields.set('max_teams','1000');
 const html=tournamentOptions();assert.ok(html.indexOf('name="finalists"')<html.indexOf('<summary>Nâng cao</summary>'));assert.ok(html.indexOf('name="ladder-hours"')<html.indexOf('<summary>Nâng cao</summary>'));
 assert.match(rankingPreview(fields),/4 đội đầu vào chung kết/);assert.match(rankingPreview(fields),/chỉ nhận đúng 16 đội/);assert.equal(readTournamentOptions(fields).official.finalists,4);
 fields.set('finalists','16');assert.doesNotMatch(rankingPreview(fields),/chỉ nhận đúng 16 đội/);
 const elements={format:{value:'elo'},max_teams:{value:''},rounds:{}},nodes=new Map(),form={id:'competition-form',dataset:{},elements,querySelector:q=>{if(!nodes.has(q))nodes.set(q,{querySelectorAll:()=>[]});return nodes.get(q);}};
 const Original=globalThis.FormData;globalThis.FormData=class{get(){return null;}};
 try{updateTournamentForm(form);assert.equal(elements.max_teams.value,'1000');assert.equal(nodes.get('[data-elo-options]').hidden,false);elements.format.value='double_round_robin';updateTournamentForm(form);assert.equal(elements.max_teams.value,'16');assert.equal(nodes.get('[data-elo-options]').hidden,true);form.dataset.capacityEdited='true';elements.max_teams.value='24';elements.format.value='elo';updateTournamentForm(form);assert.equal(elements.max_teams.value,'24');}finally{globalThis.FormData=Original;}
});
test('organizer menu has private checks and practice, without participant team or GitHub screens',()=>{
 const html=shell({user:{role:'admin',username:'btc5'}},'');assert.match(html,/#\/benchmark/);assert.match(html,/#\/practice/);assert.doesNotMatch(html,/#\/submissions|#\/team\"|#\/matching/);
 setLanguage('en');try{assert.match(tournamentOptions(),/Advanced/);assert.match(rankingPreview(new Map([['finalists','4']])),/LAN finals currently require exactly 16 teams/);}finally{setLanguage('vi');}
});
