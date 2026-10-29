import test from 'node:test';
import assert from 'node:assert/strict';
import {githubPanel,githubProgress,githubGuide} from '../dist/live/github-ui.js';
import {readTournamentOptions} from '../dist/live/tournament-options.js';

const github={repo:null,repo_code:'ab12cd',repo_prefix:'ott2026-',commits:[],deadlines:[],install_url:'https://github.com/apps/uet-bot-arena/installations/new',starter_url:'https://github.com/uet-bot-arena/ott-bot-starter'};
test('only captain can attach, everyone sees exact repo name and no ZIP controls',()=>{
  const s={user:{id:'captain'},personal:{team:{captain_id:'captain'}},github};
  const html=githubPanel(s);assert.match(html,/github-attach-form/);assert.match(html,/ott2026-ab12cd/);assert.doesNotMatch(html,/type="file"|submission-form/);
  assert.doesNotMatch(githubPanel({...s,user:{id:'member'}}),/github-attach-form/);
  assert.doesNotMatch(githubPanel({...s,github:{...github,repo:{owner:'owner',name:'repo',state:'connected'}}}),/github-attach-form/);
});
test('commit message, errors and names are escaped; progress keeps queue/build/sample stages',()=>{
  const html=githubPanel({github:{...github,repo:{owner:'owner',name:'repo',error:'<img onerror=x>'},commits:[{sha:'a'.repeat(40),message:'<script>x</script>',state:'fail',error:'<svg>',checks:[]}]}});
  assert.doesNotMatch(html,/<script>|<svg>|<img onerror/);assert.match(html,/&lt;script&gt;/);
  assert.equal(githubProgress({checks:[{state:'checking',phase:'queued',job_id:'a',queue_position:3,queue_total:8}]}),'Đang chờ · vị trí 3/8');
  assert.equal(githubProgress({checks:[{state:'checking',phase:'building',job_id:'a'}]}),'Đang biên dịch');
  assert.equal(githubProgress({checks:[{state:'checking',phase:'benchmarking',job_id:'a',samples:42}]}),'Đang kiểm tra 42/72');
});
test('deadline preview and locked GitHub time remain distinct; guide explains rollback and git history',()=>{
  let html=githubPanel({github:{...github,previews:[{sha:'a'.repeat(40),deadline:1800000000000}]}});assert.match(html,/Nếu hết hạn lúc này/);
  html=githubPanel({github:{...github,deadlines:[{state:'locked',selected_sha:'b'.repeat(40),pushed_ms:1800000000000}]}});assert.match(html,/Đã chốt: bbbbbbb/);assert.match(html,/giờ GitHub/);
  assert.match(githubGuide(),/Only select repositories/);assert.match(githubGuide(),/năm lần push/);assert.match(githubGuide(),/git checkout/);
});
test('competition form serializes Vietnam deadline independently of browser timezone to UTC milliseconds',()=>{
  const data=new FormData();data.set('submission_deadline-date','2026-10-24');data.set('submission_deadline-hour','12');data.set('submission_deadline-minute','30');
  assert.equal(readTournamentOptions(data).submission_deadline,new Date('2026-10-24T12:30:00+07:00').getTime());
  data.set('submission_deadline-date','bad');assert.throws(()=>readTournamentOptions(data),/Hạn không hợp lệ/);
});
