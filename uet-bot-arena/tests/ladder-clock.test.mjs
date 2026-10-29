import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {roundBoundary,schedulePreview,ladderInterval,ladderPreview} from '../dist/live/ladder-clock.js';
import {readLadderOptions,ladderEdit,ladderFields,readTournamentOptions} from '../dist/live/tournament-options.js';
import {setLanguage} from '../dist/live/i18n.js';

test('shared Python/JS schedule vectors agree under TZ=UTC',()=>{
 const rows=JSON.parse(readFileSync(new URL('../../arena-backend/tests/fixtures/ladder-clock.json',import.meta.url),'utf8'));
 for(const row of rows)assert.deepEqual(schedulePreview(row.ladder,row.start_ms,row.deadline_ms),row.expected,row.name);
 const first=Date.parse('2026-11-05T08:10:00+07:00'),config={round_minutes:135,first_round_ms:first};
 assert.equal(roundBoundary(first,config,{next:true}),first+135*60000);
 assert.equal(roundBoundary(first-1,config,{next:true}),first);
});
test('2 hours 15 minutes and 08:10 Vietnam serialize fixed absolute milliseconds',()=>{
 const f=new FormData();f.set('format','elo');f.set('submission_deadline-date','2026-11-05');f.set('submission_deadline-hour','23');f.set('submission_deadline-minute','17');
 f.set('ladder-hours','2');f.set('ladder-minutes','15');f.set('first_round_ms-date','2026-11-05');f.set('first_round_ms-hour','08');f.set('first_round_ms-minute','10');
 const value=readTournamentOptions(f);assert.deepEqual(value.ladder,{round_minutes:135,first_round_ms:Date.parse('2026-11-05T01:10:00Z')});
 assert.equal(value.submission_deadline,Date.parse('2026-11-05T16:17:00Z'));
 const text=ladderPreview(value.ladder,value.submission_deadline,Date.parse('2026-11-05T07:00:00+07:00'));
 for(const t of ['08:10','10:25','12:40','14:55','17:10','7 đợt'])assert.ok(text.includes(t),text);
 for(const [hours,minutes] of [['0','4'],['168','1'],['2','60'],['1.5','0']]){f.set('ladder-hours',hours);f.set('ladder-minutes',minutes);assert.throws(()=>readLadderOptions(f),/5 phút tới 7 ngày/);}
 f.set('ladder-hours','168');f.set('ladder-minutes','0');assert.equal(readLadderOptions(f).round_minutes,10080);
});
test('unchanged legacy schedule is omitted from edits, and explicit changes send the new interval',()=>{
 const f=new FormData();f.set('ladder-hours','5');f.set('ladder-minutes','0');assert.deepEqual(ladderEdit(f,{round_hours:5}),{});
 f.set('ladder-minutes','15');assert.deepEqual(ladderEdit(f,{round_hours:5}),{ladder:{round_minutes:315}});
 assert.match(ladderFields(),/name="ladder-hours"[^>]+min="0" max="168"/);assert.match(ladderFields(),/name="ladder-minutes"/);assert.match(ladderFields(),/first_round_ms-minute/);
});
test('interval labels and preview are Vietnamese/English without host timezone dependence',()=>{
 for(const [language,label] of [['vi','1 giờ 45 phút'],['en','1 hour 45 minutes']]){setLanguage(language);assert.equal(ladderInterval({round_minutes:105}),label);}
 const fields=ladderFields({round_minutes:5});assert.match(fields,/Open rounds every/);assert.match(fields,/First round at \(Vietnam time\)/);
 setLanguage('vi');
});
