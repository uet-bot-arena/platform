import test from 'node:test';
import assert from 'node:assert/strict';
import {readVnDeadline,vnDeadlineField,vnDeadlineText} from '../dist/live/vn-deadline.js';
import {entryChecklist,eloJoinPanel,competitionBadge} from '../dist/live/competition-ui.js';
import {setLanguage} from '../dist/live/i18n.js';
import {page} from '../dist/live/pages.js';

test('Vietnam deadlines roundtrip saved values even with TZ=UTC',()=>{
 const form=new FormData();form.set('submission_deadline-date','2026-11-05');form.set('submission_deadline-time','23:00');
 const ms=readVnDeadline(form,'submission_deadline');assert.equal(ms,Date.parse('2026-11-05T16:00:00Z'));
 const html=vnDeadlineField('submission_deadline',ms);
 assert.match(html,/type="date" value="2026-11-05"/);assert.match(html,/value="23:00" selected/);
 assert.equal((html.match(/<option /g)||[]).length,48);assert.doesNotMatch(html,/datetime-local/);
 assert.match(vnDeadlineText(ms),/23:00 thứ Năm 05\/11\/2026/);
 assert.match(vnDeadlineField('team-deadline',Date.parse('2026-11-05T16:17:00Z')),/value="23:17" selected/);
 form.set('submission_deadline-date','2026-02-30');assert.throws(()=>readVnDeadline(form,'submission_deadline'));
});
test('student overview has a checklist instead of profile forms, including without a team',()=>{
 const base={user:{id:'a',role:'student',team_id:null},route:'home',ids:[],personal:{profile:{},team:null},competitions:[],submissions:[],jobs:[],platform:{practice_upload:true}};
 const html=page(base);assert.match(html,/Các bước vào giải/);assert.match(html,/0\/4/);assert.doesNotMatch(html,/personal-profile-form|personal-team-create/);
 const sub=page({...base,route:'submissions'});assert.match(sub,/quick-practice-form/);assert.match(sub,/cần đội đủ 4 người/);assert.doesNotMatch(sub,/github-attach-form|Đang tải kết nối GitHub/);
 for(const href of ['submissions','benchmark','practice'])assert.ok(html.includes('href="#/'+href+'"'));
 const c={id:'c',title:'Cup',status:'open',config:{format:'elo'}};
 const ready={...base,personal:{team:{member_count:4}},competitions:[c],github:{repo:{},commits:[{checks:[{state:'pass'}]}]},teamCompetitions:{items:[{id:'c',state:'eligible'}]}};
 assert.match(entryChecklist(ready),/Đã đủ các bước/);assert.doesNotMatch(entryChecklist(ready),/<ol/);
});
test('Elo participation uses aggregate counts, state and missing-step link, and translates open separately',()=>{
 const competition={id:'c',title:'Cup',status:'open',submission_deadline:Date.parse('2026-11-05T16:00:00Z'),config:{format:'elo',ladder:{round_hours:1}},participation_counts:{active:2,eligible:1,ineligible:3},elo:{next_round_ms:12345}};
 assert.match(eloJoinPanel({competition}),/Đăng nhập để tham gia/);
 const s={competition,user:{team_id:'t'},teamCompetitions:{items:[{id:'c',state:'ineligible',reasons:['team_size']}]}};
 assert.match(eloJoinPanel(s),/href="#\/team"/);assert.match(eloJoinPanel(s),/Đang đấu: 2/);
 for(const [lang,label] of [['vi','Đang nhận bài'],['en','Accepting submissions']]){setLanguage(lang);assert.ok(competitionBadge(competition).includes(label));}
 setLanguage('vi');assert.match(competitionBadge({...competition,config:{format:'swiss'}}),/Mở đăng ký/);
});
import {operationsBody,adminOperations} from '../dist/live/admin-ui.js';
test('Operations scopes both jobs and versions to participating teams, while All restores every team',()=>{
 const one={team_id:'a',team:{name:'Inside'}},two={team_id:'b',team:{name:'Outside'}};
 const jobs=['a','b','competition:cup'].map((team_id,i)=>({id:'job-'+i,team_id,kind:'benchmark',status:'completed',created_unix_ms:1}));
 const subs=['a','b'].map((team_id,i)=>({id:'sub-'+i,team_id,version:'v1.0.'+i,runtime:'python3',created_unix_ms:1}));
 const base={accounts:[one],adminJobs:jobs,adminSubmissions:subs,selectedCompetition:'cup',competitions:[{id:'cup',title:'Cup'}]};
 const html=adminOperations(base);assert.ok(html.indexOf('name="competition-scope"')<html.indexOf('name="ops-team"'));assert.match(html,/job-0/);assert.doesNotMatch(html,/job-1/);
 assert.doesNotMatch(operationsBody({...base,opsTab:'submissions'}),/sub-1/);
 assert.match(operationsBody({...base,accounts:[one,two],selectedCompetition:''}),/job-1/);
});
