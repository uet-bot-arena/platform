import assert from 'node:assert/strict';
import test from 'node:test';
import {addonPage,addonDetailPage,changeValue} from '../dist/live/addon-ui.js';
import {rulePreviewMarkup,ruleFingerprint,rulesetField} from '../dist/live/experimental-ui.js';
import {shell} from '../dist/live/view.js';

const d={schema_version:1,name:'Energy <Dash>',energy:{cap:3,initial:1},zones:[{square:'c5',kind:'energy',gain:1}],modifiers:[{id:'dash',filter:{kinds:['scissors']},pattern:'orthogonal',range:2,cost:2,cooldown:1}],shield:{enabled:true,cost:2,duration:2},victory:{base_capture:true,type_eliminated:true,no_legal_moves:true,control_target:0}};
const r={id:'a'.repeat(32),status:'published',version:2,hash:'b'.repeat(64),definition:d,release_notes:'Điều chỉnh <script>alert(1)</script>'};
const catalog={enabled:true,items:[r],presets:{energy:d},modules:[{id:'movement',name:'Mẫu đi',description:'Filter quân'}]};
const spec={hash:r.hash,compared_to:{name:'Energy v1',version:1},changes:[{label:'Mẫu đi · dash',before:null,after:d.modifiers[0]}],compatibility:{classic_bot:'Nâng SDK v3'},movement:{base:'8 hướng',cooldown:'Cooldown'},protocol:{transport:'JSON Lines',identity:'copy turn_id',legal_actions:'ID mỗi lượt',clock:'timing.remaining_ms'},examples:{ready:{type:'ready'},action:{payload:{action_id:'a0'}},observation_payload:{legal_actions:[]}}};

test('team catalog and navigation expose details, never admin creation operations',()=>{
 const s={route:'rulesets',user:{role:'team'},rulesetCatalog:catalog};
 const html=addonPage(s);
 assert.match(html,/Ruleset add-on/);assert.match(html,/rulesets\/a{32}/);
 assert.doesNotMatch(html,/addon-import-form|experimental-toggle|rule-open|rule-release|ruleset-form/);
 assert.match(shell(s,''),/href="#\/rulesets"/);assert.doesNotMatch(shell(s,''),/Luật experimental/);
});
test('admin has import, release, versioning and notes in its modular editor',()=>{
 const html=addonPage({route:'rulesets',user:{role:'admin'},rulesetCatalog:catalog,ruleEditing:{...r,status:'draft'}});
 assert.match(html,/addon-import-form/);assert.match(html,/name="release-notes"/);
 assert.match(html,/rule-release/);assert.match(html,/rule-clone/);
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>alert/);
});
test('details use definition for cost, objectives and provide exact SDK and benchmark choices',()=>{
 const html=addonDetailPage({user:{role:'team'},rulesetCatalog:catalog,ruleDetail:{...r,contract:spec}});
 assert.match(html,/Thay đổi so với Energy v1/);assert.match(html,/Kéo/);assert.match(html,/hồi 1 lượt/);
 assert.match(html,/payload\.legal_actions/);assert.match(html,/timing.remaining_ms/);
 assert.match(html,/rule-starter/);assert.match(html,/rule-kit/);assert.match(html,/benchmark\?ruleset=a{32}/);
 assert.doesNotMatch(html,/<script>alert/);assert.match(html,/Bắt đầu mô phỏng/);
 const archived=addonDetailPage({user:{role:'team'},rulesetCatalog:catalog,ruleDetail:{...r,status:'archived',contract:spec}});
 assert.doesNotMatch(archived,/Benchmark với luật này/);assert.match(archived,/đã ẩn/);
});
test('preview caps 32 moves and keeps team handlers separate from admin editor',()=>{
 const result={observation:{state:{board:Array.from({length:9},()=>Array(9).fill(null)),ply:32,to_move:'red',energy:{red:1,blue:1},control:{red:0,blue:0}},zones:[],legal_actions:[{id:'a0',type:'move',from:'a2',to:'a3',energy_cost:0}]},validation:{summary:'ok',warnings:[]},events:[]};
 const html=rulePreviewMarkup(result,32,'rule-play');
 assert.match(html,/data-action="rule-play-step" disabled/);assert.match(html,/rule-play-reset/);
 assert.match(html,/Đã đủ 32 bước/);assert.doesNotMatch(html,/data-action="rule-preview-step"/);
});
test('competition deep links select and enable their exact ruleset',()=>{
 const html=rulesetField({rulesetCatalog:catalog},r.id,true);
 assert.match(html,/name="use-experimental" checked/);assert.doesNotMatch(html,/data-experimental-select hidden/);
 assert.match(html,/value="a{32}" selected/);
 assert.equal(ruleFingerprint({x:1,y:{a:2,b:3}}),ruleFingerprint({y:{b:3,a:2},x:1}));
 assert.equal(changeValue(false),'Tắt');assert.match(changeValue(d.modifiers[0]),/Kéo/);
});
