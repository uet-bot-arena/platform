import test from 'node:test';
import assert from 'node:assert/strict';
import {sampleAdmin} from '../dist/live/sample-bots.js';
import {timeLabel,timeField,timeBody} from '../dist/live/time-control.js';

test('one built-in starter has no level or source-upload form and escapes names',()=>{
  const html=sampleAdmin({sampleBots:[{id:'builtin',name:'<script>',runtime:'cpp17',enabled:true}]});
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('không tính Elo'));
  assert.ok(!html.includes('bot-level'));
  assert.ok(!html.includes('type="file"'));
  assert.ok(!html.includes('Mức độ'));
});

test('10-second ladder bank is selectable, labeled and serialized as ten seconds',()=>{
  assert.equal(timeLabel({match_ms:10000}),'Ladder · 10 giây / đội');
  assert.match(timeField({match_ms:10000}),/value="10000" selected/);
  assert.deepEqual(timeBody(new Map([['match_ms','10000']])),{match_ms:10000});
});
