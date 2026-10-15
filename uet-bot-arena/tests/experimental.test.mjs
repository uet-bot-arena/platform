import assert from 'node:assert/strict';
import test from 'node:test';
import {rulesetField,botRulesetField,botRulesetSelection,ruleEditor,rulePreviewMarkup} from '../dist/live/experimental-ui.js';
import {addonPage as experimentalPage} from '../dist/live/addon-ui.js';
import {jobErrorExplanation} from '../dist/live/result.js';
import {jobDetail,page} from '../dist/live/pages.js';
import {moveDescription,moveMarkup} from '../dist/live/board.js';
import {publicRoute} from '../dist/live/access.js';

const d={schema_version:1,name:'Energy <Dash>',energy:{cap:3,initial:1},zones:[{square:'c5',kind:'energy',gain:1}],modifiers:[{id:'dash',filter:{kinds:['scissors']},pattern:'orthogonal',range:2,cost:2,cooldown:1}],shield:{enabled:true,cost:2,duration:2},victory:{base_capture:true,type_eliminated:true,no_legal_moves:true,control_target:0}};
const published={id:'a'.repeat(32),status:'published',version:1,revision:3,hash:'f'.repeat(64),definition:d};

test('v3 job forms never silently default to classic and explain an empty catalog',()=>{
 const s={submissions:[{id:'v3',version:'v3.0.0',protocol_version:3},{id:'v2',version:'v1.0.0'}],rulesetCatalog:{enabled:true,items:[]}};
 let html=botRulesetField(s,['v3']);
 assert.match(html,/<option value="" selected>Chọn bộ luật/);
 assert.doesNotMatch(html,/<option value="classic" selected/);
 assert.match(html,/chưa có ruleset được công bố/);
 assert.throws(()=>botRulesetSelection(s,['v3'],''),/Chưa chọn bộ luật/);
 html=botRulesetField(s,['v2']);assert.match(html,/<option value="classic" selected/);
 assert.deepEqual(botRulesetSelection(s,['v2'],'classic'),{});
 // A dual-protocol v3 starter can still explicitly choose classic.
 assert.deepEqual(botRulesetSelection(s,['v3'],'classic'),{});
 s.rulesetCatalog.items=[published];
 assert.deepEqual(botRulesetSelection(s,['v3'],published.id),{ruleset_id:published.id});
 assert.throws(()=>botRulesetSelection(s,['v3','v2'],published.id),/tất cả bot/);
 html=botRulesetField(s,['v3'],published.id);assert.match(html,/value="a{32}" selected/);
 s.rulesetCatalog.enabled=false;
 assert.throws(()=>botRulesetSelection(s,['v3'],published.id),/đang tắt/);
 html=botRulesetField(s,['v3'],published.id);assert.match(html,/<option value="" selected/);assert.doesNotMatch(html,/value="a{32}"/);
});
test('actual startup crash explains v3 versus classic without misdiagnosing other crashes',()=>{
 const job={id:'failed',kind:'benchmark',submission_ids:['v3'],status:'failed',profile:{turn_ms:15000},error:{code:'crash',stage:'startup',detail:'Bot exited or closed stdout'},report:{sandbox_metrics:[{stderr:'This bot requires SDK v3 / Experimental.\n'}]}};
 assert.match(jobErrorExplanation(job),/tác vụ này chạy luật classic/);
 const html=jobDetail({job});assert.match(html,/Không cần nộp lại ZIP/);assert.match(html,/OTT v2 tiêu chuẩn · protocol v2/);assert.match(html,/Chọn luật &amp; chạy lại/);
 assert.equal(jobErrorExplanation({...job,ruleset:published}),'');
 assert.equal(jobErrorExplanation({...job,report:{sandbox_metrics:[{stderr:'other crash'}]}}),'');
 const base={route:'benchmark',user:{role:'team'},submissions:[{id:'v3',version:'v3.0.0',protocol_version:3}],jobs:[],rulesetCatalog:{enabled:true,items:[]},selectedSubmission:'v3'};
 assert.match(page(base),/Chọn bộ luật để chạy bot/);
 assert.match(page({...base,route:'practice'}),/Chọn bộ luật để chạy bot/);
});

test('experimental choices require the flag and a published version',()=>{
 assert.equal(publicRoute('experimental'),false);
 let html=rulesetField({rulesetCatalog:{enabled:false,items:[published]}},published.id,true);
 assert.match(html,/name="use-experimental"\s+disabled/);assert.doesNotMatch(html,/value="a{32}"/);
 html=rulesetField({rulesetCatalog:{enabled:true,items:[published,{...published,id:'draft',status:'draft'}]}},published.id);
 assert.match(html,/Experimental/);assert.match(html,/value="a{32}" selected/);assert.doesNotMatch(html,/value="draft"/);
 assert.match(html,/Energy &lt;Dash&gt;/);
});
test('rule editor maps 81 cells, filters and immutable versions',()=>{
 const html=ruleEditor(d,published);
 assert.equal((html.match(/class="rule-map-cell/g)||[]).length,81);
 assert.match(html,/name="mod-0-scissors" checked/);assert.doesNotMatch(html,/name="mod-0-rock" checked/);
 assert.match(html,/disabled>Dùng Tạo phiên bản mới/);assert.match(html,/zone-energy/);
 assert.doesNotMatch(html,/<Dash>/);
 const page=experimentalPage({user:{role:'admin'},rulesetCatalog:{enabled:false,items:[published],presets:{energy:d}}});
 assert.match(page,/Bật Ruleset add-on/);assert.match(page,/Tạo phiên bản mới/);assert.match(page,/Xem thử trên server/);
});
test('preview and semantic skill history preserve resources, blocks and outcomes',()=>{
 const state={board:Array.from({length:9},()=>Array(9).fill(null)),ply:4,to_move:'red',result:null,energy:{red:1,blue:2},control:{red:3,blue:0}};
 const result={observation:{state,zones:d.zones,legal_actions:[{id:'a0',type:'shield',from:'c5',energy_cost:2}]},validation:{summary:'ok',warnings:[]},events:[]};
 assert.match(rulePreviewMarkup(result),/Khiên · c5/);assert.match(rulePreviewMarkup(result),/Đỏ · Năng lượng 1 · Kiểm soát 3/);
 const skill={type:'ability',ply:4,side:'blue',kind:'paper',square:'e5',ability:'shield',effects:[{type:'control_gained',side:'blue',square:'e5',amount:1}]};
 assert.match(moveMarkup(skill,state,{blue:'<Team>'}),/Khiên · e5/);assert.match(moveMarkup(skill,state,{blue:'<Team>'}),/&lt;Team&gt;/);
 state.result={winner:'blue',reason:'control_target'};
 assert.match(moveDescription(skill,state),/Đạt mục tiêu kiểm soát/);
 const attack={type:'move',ply:5,side:'red',kind:'scissors',from:'d4',to:'e5',ability:'dash',blocked:true,captured:null};
 const html=moveMarkup(attack,state);assert.match(html,/Khiên chặn/);assert.doesNotMatch(html,/move-capture/);
});
