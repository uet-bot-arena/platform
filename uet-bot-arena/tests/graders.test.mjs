import assert from 'node:assert/strict';
import {test} from 'node:test';
import {gradersPage,graderRows,graderTokenModal} from '../dist/live/graders-ui.js';
import {readTournamentOptions} from '../dist/live/tournament-options.js';

const g=(extra)=>({id:'g1',name:'PC 301',platform:'windows',backend:'windows',slots:2,running:1,jobs_done:5,
  runtime_id:'sha256:'+'a'.repeat(64),version:'0.7.0-m1',last_seen_ms:1,online:true,revoked_ms:null,...extra});

test('grader list shows status, platform, slots and never a token',()=>{
  const html=gradersPage({graders:{items:[g(),g({id:'g2',name:'WSL',platform:'linux',backend:'bwrap',online:false}),
    g({id:'g3',name:'Cũ',revoked_ms:5,online:false}),g({id:'g4',name:'Mới',platform:null,online:false})],queue:{official:2,practice:7}},origin:'http://127.0.0.1:4181'});
  assert.match(html,/Online/);assert.match(html,/Offline/);assert.match(html,/Đã thu hồi/);assert.match(html,/Chưa kết nối/);
  assert.match(html,/Windows · AppContainer/);assert.match(html,/Linux · bubblewrap/);
  assert.match(html,/<div class="stat-value">1<\/div>/,'one machine online');
  assert.match(html,/<div class="stat-value">2<\/div>/,'two slots online');
  assert.match(html,/--hub http:\/\/127\.0\.0\.1:4181 --slots 2 --backend windows/);
  assert.doesNotMatch(html,/uag_|token_hash|undefined|NaN/);
  assert.equal((html.match(/data-action="revoke-grader"/g)||[]).length,3,'no revoke button for an already revoked grader');
});

test('grader names are escaped everywhere',()=>{
  const evil='<img src=x onerror=alert(1)>';
  const html=graderRows([g({name:evil})]).join('');
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
  assert.doesNotMatch(graderTokenModal({name:evil,token:'uag_x'}),/<img/);
});

test('token modal shows the one-time token and a copy action',()=>{
  const html=graderTokenModal({name:'PC 301',token:'uag_abc'});
  assert.match(html,/uag_abc/);assert.match(html,/copy-grader-token/);assert.match(html,/lần duy nhất/);
});

test('competition form sends the grader platform, default windows',()=>{
  assert.equal(readTournamentOptions(new Map([['format','swiss']])).grader_platform,'windows');
  assert.equal(readTournamentOptions(new Map([['format','swiss'],['grader_platform','linux']])).grader_platform,'linux');
});
