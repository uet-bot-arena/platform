import assert from 'node:assert/strict';
import test from 'node:test';
import {remainingClocks,clockText,timeField,timeLabel,timeBody,timeQuery,competitionProfile,thinkingTime,thinkingText} from '../dist/live/time-control.js';
import {setLanguage} from '../dist/live/i18n.js';
import {jobErrorExplanation} from '../dist/live/result.js';
import {boardPage} from '../dist/live/board.js';

test('Elo replay uses the official bank rather than the unused competition profile',()=>{
  const profile={match_ms:300000},competition={config:{format:'elo',profile,official:{match_ms:10000}}};
  const actual=competitionProfile(competition);
  assert.equal(timeLabel(actual),'Mỗi bot 10 giây cho cả ván');
  assert.match(boardPage({title:'Cup',red:'A',blue:'B',back:'home',profile:actual,format:'double_round_robin'}),/Vòng tròn hai lượt · Mỗi bot 10 giây cho cả ván/);
  assert.equal(profile.match_ms,300000);
  assert.equal(competitionProfile({config:{format:'elo',profile}}).match_ms,10000);
  assert.equal(competitionProfile({config:{format:'swiss',profile}}),profile);
});

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
  assert.equal(timeLabel({match_ms:60000}),'Mỗi bot 1 phút cho cả ván');
  assert.equal(timeLabel({match_ms:300000}),'Mỗi bot 5 phút cho cả ván');
  assert.equal(timeLabel({id:'ottv2-check-500ms-v1',turn_ms:500}),'Kiểm tra · 0,5 giây / mẫu');
  assert.equal(timeQuery({match_ms:300000}),'match=300000');
  assert.equal(clockText(60000),'1:00.0');assert.equal(clockText(59901),'0:59.90');
  assert.equal(clockText(59899),'0:59.89');assert.equal(clockText(-50),'0:00.00');
  assert.equal(clockText(10000),'0:10.00');assert.equal(clockText(9999.5),'0:09.99');
  assert.match(jobErrorExplanation({kind:'benchmark',profile:{match_ms:60000},error:{code:'timeout',stage:'turn'}}),/72 mẫu dùng chung/);
  assert.equal(jobErrorExplanation({kind:'benchmark',profile:{match_ms:60000},error:{code:'timeout',stage:'startup'}}),'','startup remains a separate 2s allowance');
});

test('thinking time follows the mover bank and omits missing or inconsistent old clocks',()=>{
  assert.equal(thinkingTime({red:10000,blue:10000},{red:9999.5,blue:10000},'red'),.5);
  assert.equal(thinkingTime(null,{red:10000},'red'),null);
  assert.equal(thinkingTime({red:10000},{red:10001},'red'),null);
  assert.equal(thinkingTime({red:10000},{red:9000},null),null);
  assert.equal(thinkingText(.5),'0,5 ms');assert.equal(thinkingText(1250),'1,25 s');
  setLanguage('en');
  try{assert.equal(thinkingText(.5),'0.5 ms');assert.equal(timeLabel({match_ms:10000}),'Each bot gets 10 seconds for the whole game');}
  finally{setLanguage('vi');}
});
