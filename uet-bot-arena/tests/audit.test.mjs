import test from 'node:test';
import assert from 'node:assert/strict';
import {actionLabels,auditLabel,auditPath,auditPage} from '../dist/live/audit-ui.js';
import {ArenaAPI} from '../dist/live/client.js';
import {adminTeams,adminDashboard} from '../dist/live/admin-ui.js';
import {peopleAdminPage} from '../dist/live/people-ui.js';
import {setLanguage} from '../dist/live/i18n.js';

test('audit shows actor names, roles, IP, action labels and escaped target fallbacks',()=>{
 const html=auditPage({audit:[{actor_username:'btc<unsafe>',actor_role:'admin',action:'auth.login',target:'target<unsafe>',target_label:'Named target',created_ms:1,ip:'113.185.x.x'},{actor_username:null,actor_role:null,action:'competition.result',target:'abcdefghijk',created_ms:1,ip:null},{actor_username:'student',actor_role:'student',action:'new.unknown',target:'id',created_ms:1}]});
 for(const value of ['Nhật ký hệ thống','BTC','Hệ thống','Sinh viên','113.185.x.x','Đăng nhập','Ghi kết quả ván','new.unknown','Named target','abcdefgh','title="target&lt;unsafe&gt;"'])assert.ok(html.includes(value),value);
 assert.doesNotMatch(html,/<unsafe>/);assert.equal(auditLabel('ruleset:edit:uuid'),'Sửa luật bổ sung');
});
test('audit filters preserve backend pagination and send role/group/literal query',async()=>{
 const s={auditRole:'team',auditGroup:'bot',auditSearch:'name & 113.185'};const path=auditPath(s),q=new URL(path,'http://test').searchParams;
 assert.equal(q.get('role'),'team');assert.equal(q.get('group'),'bot');assert.equal(q.get('q'),'name & 113.185');assert.equal(auditPath({}),'/admin/audit');
 const calls=[],api=new ArenaAPI();api.request=async path=>{calls.push(path);return {items:[]};};await api.all(path);
 const sent=new URL(calls[0],'http://test').searchParams;assert.equal(sent.get('limit'),'100');assert.equal(sent.get('offset'),'0');assert.equal(sent.get('q'),s.auditSearch);
 const html=auditPage(s);for(const name of ['audit-role','audit-group','audit-search'])assert.match(html,new RegExp('name="'+name+'"'));
});
test('all current action labels have English translations; unknown actions stay unchanged',()=>{
 setLanguage('en');for(const [code,label] of Object.entries(actionLabels))assert.notEqual(auditLabel(code),label,code);
 assert.match(auditPage({audit:[]}),/System audit log|Action group/);assert.equal(auditLabel('new.unknown'),'new.unknown');setLanguage('vi');
});
test('competition scope shares the filter row after tabs and preserves registration status',()=>{
 const s={accounts:[],people:[],recoveries:[],registrations:[],competitions:[],adminTab:'accounts',adminVisibility:'active'};
 for(const tab of ['accounts','registrations','recoveries']){
  const html=adminTeams({...s,adminTab:tab}),row=html.match(/<div class="admin-filters team-filters">([\s\S]*?)<\/div>/)[1];
  assert.ok(html.indexOf('class="tabs"')<html.indexOf('class="admin-filters'));
  assert.ok(row.indexOf('competition-scope')<row.indexOf('admin-search'));
  assert.equal((row.match(/<label/g)||[]).length,tab==='accounts'?3:2);
  if(tab==='registrations')assert.match(html,/name="registration-status"/);
 }
 const summary={title:'Cup',format:'elo',status:'open',counts:{active:0,eligible:0,ineligible:0,excluded:0},reasons:{},fixtures:{completed:0,waiting:0,attention:0},queue:{queued:0},progress:{}};
 assert.match(adminDashboard({selectedCompetition:'c',competitionSummary:summary}),/class="admin-filters scope-filter"><label class="field competition-filter"/);
 assert.match(peopleAdminPage({people:[],teamChoices:[]}),/class="admin-filters people-filters"><label class="field competition-filter"/);
});
