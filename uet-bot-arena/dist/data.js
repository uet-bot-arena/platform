export const teams = [
  { id: 'byte', name: 'Byte Knights', tag: 'BK', group: 'A', color: '#176c60', played: 5, win: 4, draw: 1, loss: 0, points: 13, form: ['W','W','D','W','W'], rank: 1 },
  { id: 'null', name: 'Null Pointers', tag: 'NP', group: 'A', color: '#a16f4a', played: 5, win: 3, draw: 1, loss: 1, points: 10, form: ['W','L','W','D','W'], rank: 2 },
  { id: 'stack', name: 'Stack Overflow', tag: 'SO', group: 'B', color: '#586a9b', played: 5, win: 3, draw: 0, loss: 2, points: 9, form: ['L','W','W','L','W'], rank: 3 },
  { id: 'alpha', name: 'Alpha Coders', tag: 'AC', group: 'B', color: '#987441', played: 5, win: 2, draw: 2, loss: 1, points: 8, form: ['D','W','D','W','L'], rank: 4 },
  { id: 'bit', name: 'Bit by Bit', tag: 'BB', group: 'A', color: '#9b5b74', played: 5, win: 2, draw: 1, loss: 2, points: 7, form: ['W','L','D','L','W'], rank: 5 },
  { id: 'ctrl', name: 'Ctrl + Win', tag: 'CW', group: 'B', color: '#447d8b', played: 5, win: 1, draw: 2, loss: 2, points: 5, form: ['D','L','W','D','L'], rank: 6 },
  { id: 'hello', name: 'Hello World', tag: 'HW', group: 'A', color: '#7a8660', played: 5, win: 1, draw: 1, loss: 3, points: 4, form: ['L','L','W','D','L'], rank: 7 },
  { id: 'loop', name: 'Infinite Loop', tag: 'IL', group: 'B', color: '#857196', played: 5, win: 0, draw: 2, loss: 3, points: 2, form: ['L','D','L','L','D'], rank: 8 }
];
export const teamById = id => teams.find(t => t.id === id) || teams[0];
export const matches = [
  { id: 'M024', a: 'byte', b: 'null', group: 'A', round: 6, status: 'live', time: '14:00', turn: 24, viewers: 128 },
  { id: 'M025', a: 'stack', b: 'alpha', group: 'B', round: 6, status: 'live', time: '14:00', turn: 18, viewers: 64 },
  { id: 'M026', a: 'bit', b: 'hello', group: 'A', round: 6, status: 'paused', time: '14:00', turn: 12, viewers: 32 },
  { id: 'M027', a: 'ctrl', b: 'loop', group: 'B', round: 6, status: 'scheduled', time: '14:30', turn: 0, viewers: 0 },
  { id: 'M028', a: 'byte', b: 'hello', group: 'A', round: 7, status: 'scheduled', time: '15:00', turn: 0, viewers: 0 },
  { id: 'M029', a: 'null', b: 'bit', group: 'A', round: 7, status: 'scheduled', time: '15:00', turn: 0, viewers: 0 },
  { id: 'M030', a: 'stack', b: 'ctrl', group: 'B', round: 7, status: 'scheduled', time: '15:30', turn: 0, viewers: 0 },
  { id: 'M031', a: 'alpha', b: 'loop', group: 'B', round: 7, status: 'scheduled', time: '15:30', turn: 0, viewers: 0 },
  { id: 'M020', a: 'byte', b: 'bit', group: 'A', round: 5, status: 'finished', time: '13:00', turn: 42, viewers: 0, score: '1 – 0' },
  { id: 'M016', a: 'byte', b: 'hello', group: 'A', round: 4, status: 'finished', time: '11:00', turn: 38, viewers: 0, score: '1 – 0' },
  { id: 'M012', a: 'byte', b: 'null', group: 'A', round: 3, status: 'finished', time: '10:30', turn: 60, viewers: 0, score: '½ – ½' }
];
export const initialMembers = [
  { name: 'Nguyễn Minh Anh', initials: 'MA', email: 'minhanh@vnu.edu.vn', role: 'Đội trưởng', color: '#dcebe6' },
  { name: 'Trần Đức Huy', initials: 'DH', email: 'duchuy@vnu.edu.vn', role: 'Thành viên', color: '#ede4d7' },
  { name: 'Lê Hoàng Nam', initials: 'HN', email: 'hoangnam@vnu.edu.vn', role: 'Thành viên', color: '#e3e7f2' }
];
export const initialSubmissions = [
  { id: 'SUB-008', version: 'v1.3.0', file: 'byte-knights-v1.3.zip', size: '128 KB', date: '10/10/2026 · 12:42', status: 'accepted', active: true, note: 'Cải thiện đánh giá thế trận và move ordering.' },
  { id: 'SUB-007', version: 'v1.2.0', file: 'byte-knights-v1.2.zip', size: '116 KB', date: '09/10/2026 · 21:18', status: 'accepted', active: false, note: 'Thêm iterative deepening.' },
  { id: 'SUB-006', version: 'v1.1.1', file: 'byte-knights-v1.1.1.zip', size: '104 KB', date: '09/10/2026 · 18:05', status: 'rejected', active: false, note: 'Thiếu file bot.py trong thư mục gốc.' }
];
export const nav = [
  ['home', 'Tổng quan', 'grid'], ['live', 'Trực tiếp', 'radio'], ['schedule', 'Lịch thi đấu', 'calendar'], ['standings', 'Bảng xếp hạng', 'trophy'], ['team', 'Đội của tôi', 'users'], ['submissions', 'Nộp bot', 'upload'], ['benchmark','Test & benchmark','code']
];
export const pageTitles = { home: 'Tổng quan', live: 'Trực tiếp', schedule: 'Lịch thi đấu', standings: 'Bảng xếp hạng', team: 'Đội của tôi', submissions: 'Nộp bot', arena: 'Phòng thi đấu', replay: 'Xem lại trận đấu', guide: 'Tài liệu & thể lệ', benchmark: 'Test & benchmark', admin: 'Tài khoản đội thi' };
export function matchesFor(state) {
  if(!state.drawConfirmed)return matches;
  return [...matches.filter(m=>m.round!==7),...state.drawPairs.map((pair,i)=>({id:`DRAW0${i+1}`,a:pair[0],b:pair[1],group:teamById(pair[0]).group,round:7,status:'scheduled',time:i<2?'15:00':'15:30',turn:0,viewers:0}))];
}
export const matchFor = (state,id) => matchesFor(state).find(m=>m.id===id);

// A deterministic visual demonstration, not the authoritative OTTv2 rules engine.
export const basePieces = [
  [0,2,'r','blue'],[0,4,'p','blue'],[0,6,'s','blue'],[1,2,'s','blue'],[1,4,'r','blue'],[1,6,'p','blue'],[2,1,'p','blue'],[2,4,'s','blue'],[2,7,'r','blue'],
  [8,2,'s','green'],[8,4,'r','green'],[8,6,'p','green'],[7,2,'p','green'],[7,4,'s','green'],[7,6,'r','green'],[6,1,'r','green'],[6,4,'p','green'],[6,7,'s','green']
];
export function boardAt(step = 0) {
  const pieces = basePieces.map(p => [...p]);
  const moves = [];
  for (let n=0; n<step; n++) {
    const side = n % 2 ? 'blue' : 'green';
    const start = side === 'blue' ? 0 : 9;
    const index = start + (Math.floor(n / 2) % 9);
    const p = pieces[index];
    const from = `${'abcdefghi'[p[1]]}${9-p[0]}`;
    const next = p[0] + (side === 'blue' ? 1 : -1);
    if (next >= 1 && next <= 7 && !pieces.some(x=>x!==p && x[0]===next && x[1]===p[1])) p[0] = next;
    else {
      const col = (p[1]+1)%9;
      if (!pieces.some(x=>x!==p && x[0]===p[0] && x[1]===col)) p[1]=col;
    }
    moves.push({ side, piece:p[2], from, to:`${'abcdefghi'[p[1]]}${9-p[0]}`, duration:`${(0.2+(n%7)*0.13).toFixed(2)}s` });
  }
  return {pieces, moves};
}
