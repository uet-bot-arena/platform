import assert from 'node:assert/strict';
import {test} from 'node:test';
import {publicRoute} from '../dist/live/access.js';
import {page} from '../dist/live/pages.js';

const comp={id:'cup',title:'Guest cup',status:'open',archived_ms:null,
  config:{profile:{turn_ms:15000},max_teams:16},fixture_counts:{completed:1,attention:0},
  entries:[{team_id:'one',name:'Team One',version:'v1.0.0',accepted_ms:1}]};
const base={user:null,loading:false,ids:[],competitions:[comp],competition:comp,
  fixtures:[{id:'game',competition_id:'cup',ordinal:1,red_team_id:'one',blue_team_id:'two',status:'completed',result:{winner:'red',reason:'base_capture'}}],
  standings:{items:[{team_id:'one',name:'Team One',version:'v1',played:1,wins:1,draws:0,losses:0,points:3,rank:1}],scoring:{win:3,draw:1,loss:0}},
  submissions:[],jobs:[]};
test('guest routing opens official views but protects practice and management',()=>{
  for(const route of ['home','competitions','live','schedule','standings','guide'])assert.equal(publicRoute(route),true);
  for(const route of ['arena','replay']){
    assert.equal(publicRoute(route,['cup','game']),true);
    assert.equal(publicRoute(route,['practice','job']),false);
  }
  for(const route of ['jobs','team','submissions','benchmark','practice','admin','operations','audit','change-password'])assert.equal(publicRoute(route),false);
});
test('all public pages render for guests with no team or admin controls',()=>{
  for(const route of ['home','competitions','live','schedule','standings']){
    const html=page({...base,route});
    assert.match(html,/<strong>Khách<\/strong>/);
    assert.doesNotMatch(html,/href="#\/(submissions|practice|admin|operations)"/);
    assert.doesNotMatch(html,/id="(entry|competition)-form"/);
    assert.doesNotMatch(html,/data-action="(competition-action|diagnostic)"/);
    assert.doesNotMatch(html,/undefined|NaN/);
  }
  const detail=page({...base,route:'competitions',ids:['cup']});
  assert.match(detail,/tư cách khách/);
  assert.match(detail,/#\/replay\/cup\/game/);
  assert.doesNotMatch(detail,/id="entry-form"|Đến benchmark/);
  assert.match(page({...base,route:'home'}),/Xem trận không cần đăng nhập/);
});
test('team entry and admin operations remain available to signed-in accounts',()=>{
  assert.match(page({...base,route:'competitions',ids:['cup'],user:{role:'team',team_id:'one'}}),/Đến benchmark/);
  assert.match(page({...base,route:'competitions',user:{role:'admin'}}),/id="competition-form"/);
});
