import {test} from 'node:test';
import assert from 'node:assert/strict';
import {quickPractice,readPracticeFile,recentMatch,recentMatchPanel,submissionLabel} from '../dist/live/quick-practice.js';
import {gradersPage} from '../dist/live/graders-ui.js';
test('quick practice is opt-in from platform and hidden on LAN',()=>{
 assert.equal(quickPractice({platform:{practice_upload:false}}),'');
 assert.equal(quickPractice({platform:{practice_upload:true,team_zip_upload:true}}),'');
 const html=quickPractice({platform:{practice_upload:true},submissions:[{id:'s',version:'thu-1234567',channel:'practice'}],jobs:[{id:'j',kind:'benchmark',submission_ids:['s']}]});
 for(const value of ['quick-practice-form','practice-file','practice-example','thu-1234567','#/jobs/j'])assert.ok(html.includes(value));
 assert.match(submissionLabel({version:'thu-1234567',channel:'practice'}),/Thử nhanh/);
});
test('file picker reads and detects language and rejects oversized files',async()=>{
 globalThis.FileReader=class{readAsText(file){this.result=file.code;this.onload();}};
 assert.deepEqual(await readPracticeFile({name:'test.py',size:8,code:'print(1)'}),{runtime:'python3',code:'print(1)'});
 await assert.rejects(readPracticeFile({name:'test.py',size:200001}));
 await assert.rejects(readPracticeFile({name:'test.zip',size:5}));
 delete globalThis.FileReader;
});
test('recent finished match uses at most two snapshots and shared board',async()=>{
 const calls=[],state={board:Array.from({length:9},()=>Array(9).fill(null))};state.board[0][0]={side:'blue',kind:'rock'};
 const c={id:'c',fixture_counts:{completed:1},config:{},entries:[{team_id:'a',name:'<A>'},{team_id:'b',name:'B'}]};
 const f={id:'f',status:'completed',ordinal:1,red_team_id:'a',blue_team_id:'b',result:{winner:'red',reason:'base_capture'}};
 const api={snapshot:async p=>{calls.push(p);return p==='fixtures/c.json'?{items:[{...f,id:'old',ordinal:2,completed_ms:1},{...f,completed_ms:2}]}:{items:[{data:{state}}]};}};
 const value=await recentMatch(api,[c]);assert.equal(calls.length,2);
 const html=recentMatchPanel(value);assert.match(html,/board-piece blue/);assert.match(html,/#\/replay\/c\/f/);assert.doesNotMatch(html,/<A>/);
 assert.equal(recentMatchPanel(null),'');assert.equal(await recentMatch(api,[]),null);assert.equal(calls.length,2);
});
test('review admin sees graders without executable controls',()=>{
 const html=gradersPage({canManageGraders:false,graders:{items:[{id:'g',name:'PC',platform:'windows',slots:2}],queue:{}}});
 assert.match(html,/organizer/);assert.match(html,/nhịp tim mỗi 5 giây/);assert.match(html,/disabled title="Chỉ organizer"/);
 assert.doesNotMatch(html,/copy-grader-controls|grader\/download|install-grader|grader-latest/);
});
