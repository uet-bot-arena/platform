import test from 'node:test';
import assert from 'node:assert/strict';
import {boardPage,moveMarkup} from '../dist/live/board.js';
import {renderRules} from '../dist/live/rules.js';
import {announcementsPage} from '../dist/live/notices-ui.js';
import {adminOperations} from '../dist/live/admin-ui.js';
import {page} from '../dist/live/pages.js';
const evil='"><img src=x onerror=alert(1)>';
const safe=html=>{assert.doesNotMatch(html,/<img|<script|class="[^"]*" onerror=/);assert.match(html,/&lt;img/);};
test('board names, captured sides, actions and notices are text in HTML templates',()=>{
 safe(boardPage({title:evil,red:evil,blue:evil,back:'home'}));
 safe(moveMarkup({type:'move',side:evil,from:evil,to:evil,kind:'rock',captured:{side:evil,kind:'paper'},ply:1},{result:null},{[evil]:evil}));
 safe(announcementsPage({announcements:[{id:'a',title:evil,body:evil,created_unix_ms:1}]}));
 safe(renderRules('# '+evil+'\n\n[click](javascript:alert(1))\n\n```html\n'+evil+'\n```'));
});
test('BTC duplicate warning escapes names and the public limits page needs no session',()=>{
 safe(adminOperations({accounts:[],adminJobs:[],adminSubmissions:[],duplicates:[{bot_hash:'a'.repeat(64),teams:[{name:evil}]}]}));
 const html=page({route:'limits',user:null});assert.match(html,/Giới hạn và cách báo lỗi/);assert.match(html,/30 bản mới mỗi giờ/);
});
