import test from 'node:test';
import assert from 'node:assert/strict';
import {readTournamentOptions} from '../dist/live/tournament-options.js';
import {boardPage,describeTransition} from '../dist/live/board.js';
import {policyLabel} from '../dist/live/result.js';

test('Swiss accepts large capacity and sends normalized rectangular rules',()=>{
  const fields=new Map(Object.entries({format:'swiss',max_teams:'200',rounds:'10','custom-basic':'on',
    'board-rows':'10','board-cols':'12','base-red':'b1','base-blue':'','eliminate-types':'2','base-capture':'on',placement:''}));
  assert.deepEqual(readTournamentOptions(fields),{format:'swiss',rounds:10,basic_rules:{rows:10,cols:12,
    bases:{red:'b1',blue:'l10'},eliminate_types:2,base_capture:true,placement:'auto'}});
  fields.set('format','double_round_robin');
  assert.throws(()=>readTournamentOptions(fields),/32/);
  fields.set('format','swiss');fields.set('base-red','m10');
  assert.throws(()=>readTournamentOptions(fields),/hợp lệ/);
});

test('rectangular replay renders every square and preserves multi-digit coordinates',()=>{
  const html=boardPage({title:'Test',red:'A',blue:'B',back:'home',basicRules:{rows:10,cols:12,bases:{red:'b1',blue:'l10'}}});
  assert.equal((html.match(/data-square=/g)||[]).length,120);
  assert.match(html,/base-blue[^>]*data-square="l10"/);
  const previous={board:Array.from({length:10},()=>Array(12).fill(null)),to_move:'red',ply:0,result:null};
  previous.board[0][10]={side:'red',kind:'rock'};
  const next=structuredClone(previous);next.board[0][11]=next.board[0][10];next.board[0][10]=null;next.to_move='blue';next.ply=1;
  const move=describeTransition(previous,next);
  assert.equal(move.from,'k10');assert.equal(move.to,'l10');
  assert.match(policyLabel({adjudication:{id:'ottv2-match-v2'},profile:{board_area:120,max_plies:759,job_wall_ms:1200000,match_ms:60000}}),/759 nước/);
});
