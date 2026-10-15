import assert from 'node:assert/strict';
import test from 'node:test';
import {remainingClocks,clockText,timeField,timeLabel,timeBody,timeQuery} from '../dist/live/time-control.js';
import {jobErrorExplanation} from '../dist/live/result.js';

test('only the active bank runs, never resets or goes below zero',()=>{
  const bank={red:59000,blue:60000},turn={side:'red',estimated_started_unix_ms:1000};
  assert.deepEqual(remainingClocks(bank,turn,2250,true),{red:57750,blue:60000});
  assert.deepEqual(remainingClocks(bank,turn,100000,true),{red:0,blue:60000});
  assert.deepEqual(remainingClocks(bank,turn,2250,false),bank,'replay and terminal clocks stay frozen');
  assert.deepEqual(remainingClocks(bank,null,2250,true),bank,'between turns clocks stay frozen');
  assert.deepEqual(remainingClocks(bank,turn,500,true),bank,'clock skew cannot increase the bank');
  assert.deepEqual(bank,{red:59000,blue:60000},'snapshots remain immutable');
  assert.equal(remainingClocks(null,turn,2250,true),null,'legacy replay needs no fabricated bank');
});
test('new forms send bank controls and legacy pinned events remain readable',()=>{
  assert.match(timeField(),/name="match_ms"/);
  assert.doesNotMatch(timeField(),/name="turn_ms"|15 giây/);
  assert.match(timeField({turn_ms:15000}),/name="turn_ms"/);
  assert.throws(()=>timeBody(new FormData()),/Chọn thể thức/);
  const data=new FormData();data.set('match_ms','60000');assert.deepEqual(timeBody(data),{match_ms:60000});
  assert.match(timeLabel({match_ms:60000}),/Bullet.*1 phút.*đội/);
  assert.equal(timeQuery({match_ms:300000}),'match=300000');
  assert.equal(clockText(60000),'1:00.0');assert.equal(clockText(59901),'1:00.0');
  assert.equal(clockText(59899),'0:59.9');assert.equal(clockText(-50),'0:00.0');
  assert.match(jobErrorExplanation({kind:'benchmark',profile:{match_ms:60000},error:{code:'timeout',stage:'turn'}}),/72 mẫu dùng chung/);
  assert.equal(jobErrorExplanation({kind:'benchmark',profile:{match_ms:60000},error:{code:'timeout',stage:'startup'}}),'','startup remains a separate 2s allowance');
});
