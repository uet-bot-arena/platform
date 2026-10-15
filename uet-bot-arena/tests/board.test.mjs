import assert from 'node:assert/strict';
import test from 'node:test';
import {describeTransition,moveDescription,moveMarkup,countPieces} from '../dist/live/board.js';

const names={red:'Đội Một',blue:'Đội Hai'};
function position(to_move='red',ply=0){return {board:Array.from({length:9},()=>Array(9).fill(null)),to_move,ply,result:null};}
function place(state,square,side,kind){state.board[9-Number(square[1])][square.charCodeAt(0)-97]={side,kind};}
function move(state,from,to){const next=structuredClone(state),r=9-Number(from[1]),c=from.charCodeAt(0)-97;
  next.board[9-Number(to[1])][to.charCodeAt(0)-97]=next.board[r][c];next.board[r][c]=null;
  next.ply++;next.to_move=state.to_move==='red'?'blue':'red';return next;}

test('move history names the side that moved, with correct board coordinates',()=>{
  for(const side of ['red','blue']){
    const before=position(side,12);place(before,'a8',side,'rock');
    const after=move(before,'a8','b7'),action=describeTransition(before,after);
    assert.deepEqual(action,{type:'move',ply:13,side,kind:'rock',from:'a8',to:'b7',captured:null});
    assert.match(moveDescription(action,after,names),new RegExp(names[side]+'.*Búa a8 → b7'));
  }
});

test('capture identifies both pieces and changes only the victim inventory',()=>{
  const before=position('blue',23);place(before,'e5','blue','paper');place(before,'d4','red','rock');
  place(before,'a1','red','scissors');place(before,'i9','blue','rock');
  const after=move(before,'e5','d4'),action=describeTransition(before,after);
  assert.deepEqual(action.captured,{side:'red',kind:'rock'});
  assert.equal(action.kind,'paper');
  assert.equal(moveDescription(action,after,names),'Nước 24 · Đội Hai (Xanh) · Lá e5 → d4 · ăn Búa của Đội Một (Đỏ)');
  assert.deepEqual(countPieces(after),{red:{rock:0,paper:0,scissors:1},blue:{rock:1,paper:1,scissors:0}});
  assert.equal(countPieces(before).red.rock,1,'reading history must not mutate the earlier snapshot');
  assert.match(moveMarkup(action,after,names),/capture-symbol/);
  assert.match(moveMarkup(action,after,names),/Ăn Búa/);
});

test('timeout, resignation and wall limit snapshots never invent a move',()=>{
  for(const reason of ['timeout','resignation','wall_limit']){
    const before=position('red',10);place(before,'a8','red','rock');
    const after=structuredClone(before);after.result={winner:reason==='wall_limit'?null:'blue',reason};
    const action=describeTransition(before,after);
    assert.deepEqual(action,{type:'end',ply:10});
    assert.match(moveDescription(action,after,names),/^Kết thúc · /);
    assert.doesNotMatch(moveMarkup(action,after,names),/move-action|move-capture/);
  }
});

test('a winning or drawing final move retains its move and capture details',()=>{
  const before=position('red',17);place(before,'g8','red','rock');place(before,'h9','blue','scissors');
  const after=move(before,'g8','h9');after.result={winner:'red',reason:'type_eliminated'};
  const action=describeTransition(before,after);
  assert.equal(action.type,'move');assert.equal(action.captured.kind,'scissors');
  assert.match(moveDescription(action,after,names),/Búa g8 → h9.*ăn Kéo.*Đội Một \(Đỏ\) thắng/);
  after.result={winner:null,reason:'threefold_repetition'};
  assert.match(moveDescription(action,after,names),/Búa g8 → h9.*Hòa · Lặp thế 3 lần/);
});

test('initial and incomplete history do not fabricate missing actions',()=>{
  assert.equal(describeTransition(null,position()).type,'start');
  const before=position('red',12);place(before,'b2','red','paper');
  const after=move(before,'b2','c3');after.ply=15;
  assert.deepEqual(describeTransition(before,after),{type:'snapshot',ply:15});
  assert.deepEqual(describeTransition(null,after),{type:'snapshot',ply:15});
  after.result={winner:'blue',reason:'timeout'};
  assert.deepEqual(describeTransition(null,after),{type:'end',ply:15});
});

test('team names are escaped in move HTML',()=>{
  const before=position();place(before,'b2','red','scissors');const after=move(before,'b2','c3');
  const html=moveMarkup(describeTransition(before,after),after,{red:'<img src=x onerror="alert(1)">',blue:'B'});
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);assert.match(html,/&quot;/);
});
