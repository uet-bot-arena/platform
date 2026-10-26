import test from 'node:test';
import assert from 'node:assert/strict';
import {personalAuthPage,accountTeamPage,matchingPage,peopleAdminPage} from '../dist/live/people-ui.js';
import {page} from '../dist/live/pages.js';
import {publicRoute} from '../dist/live/access.js';

test('personal registration verifies email before showing a password and keeps Spam guidance',()=>{
  let html=personalAuthPage({route:'register'});
  assert.match(html,/email-request-form/);assert.doesNotMatch(html,/personal-password-form/);assert.match(html,/Spam/);
  html=personalAuthPage({route:'register',emailRequest:{id:'a',verified_ms:1,verification_token:'private-test-proof'}});
  assert.match(html,/personal-password-form/);assert.doesNotMatch(html,/private-test-proof/);assert.match(html,/autocomplete="new-password"/);
  assert.match(personalAuthPage({route:'recover'}),/Quên mật khẩu/);
  for(const route of ['register','recover'])assert.match(personalAuthPage({route}),/class="table-actions people-auth-links"><a[^>]+>.*?<\/a><a[^>]+>.*?<\/a><\/div>/);
  assert.ok(publicRoute('register'));assert.ok(publicRoute('recover'));assert.equal(publicRoute('matching'),false);
});
test('captain controls, invite state and member names are escaped',()=>{
  const s={user:{id:'captain'},personal:{profile:{display_name:'<img src=x>',role:'student'},team:{name:'<svg>',captain_id:'captain',team_size:4,invite_code:'ABCD2345',invite_enabled:false,roster:[{id:'captain',captain:1,display_name:'<script>x</script>'},{id:'member',display_name:'Member'}]}}};
  const html=accountTeamPage(s);
  assert.match(html,/Đã tắt/);assert.match(html,/team-captain/);assert.match(html,/team-remove/);assert.match(html,/team\?invite=ABCD2345/);
  assert.doesNotMatch(html,/<script>|<svg>|<img src=x>/);assert.match(html,/&lt;svg&gt;/);
  const member=accountTeamPage({...s,user:{id:'member'}});
  assert.doesNotMatch(member,/team-remove|team-captain|team-rotate/);
});
test('matching renders only safe contact text without a request-to-join action',()=>{
  const html=matchingPage({matching:{team_size:4,people:[{display_name:'<script>x</script>',class_name:'K71',language:'cpp17',introduction:'Hi',contact:'<a href=javascript:evil>bad</a>'}],teams:[{name:'Team',count:2}]}});
  assert.match(html,/2 người/);assert.doesNotMatch(html,/<script>|javascript:evil>bad|Xin vào/);assert.match(html,/&lt;a href=/);
});
test('admin can promote only staff and sees explicit merge confirmation',()=>{
  const html=peopleAdminPage({people:[{id:'a',display_name:'Student',role:'student'},{id:'b',display_name:'Teacher',role:'staff'}],teamChoices:[{id:'team',name:'Đội kiểm thử',roster:[]}],teamPolicy:{revision:1},teamMerges:{items:[{id:'p',people:[{display_name:'<script>x</script>'}],team_name:'New'}]}});
  assert.equal((html.match(/data-action="person-judge"/g)||[]).length,1);
  assert.match(html,/Xác nhận ghép/);assert.match(html,/admin-membership-form/);assert.doesNotMatch(html,/<script>/);
});
test('a guest team never appears in the ranking chart',()=>{
  const html=page({route:'standings',ids:[],user:null,competitions:[],competition:{id:'x',title:'Cup'},standings:{items:[{team_id:'guest',name:'Guest',is_guest:true,rank:null,points:99,wins:33,draws:0,losses:0,played:33}]}});
  assert.match(html,/Đội khách không được xếp hạng/);assert.doesNotMatch(html,/score-table-guest|score-graph-guest/);
});
