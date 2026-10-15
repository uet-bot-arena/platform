import {select} from './view.js';

export const matchControls=[['60000','Bullet · 1 phút / đội'],['300000','Đấu thường · 5 phút / đội']];
export const timeLabel=p=>p?.match_ms?`${p.match_ms===60000?'Bullet · 1 phút':'Đấu thường · 5 phút'} / đội`:`${(p?.turn_ms||15000)/1000}s / lượt (luật cũ)`;
export const timeQuery=p=>p?.match_ms?'match='+p.match_ms:'turn='+(p?.turn_ms||15000);
export function timeField(p={match_ms:300000}) {
  const legacy=!p.match_ms&&p.turn_ms;
  return `<label class="field"><span>${legacy?'Thời gian (giải cũ)':'Tổng thời gian mỗi đội'}</span>${select(legacy?'turn_ms':'match_ms',legacy?[15000,30000,60000,300000].map(v=>[String(v),v/1000+' giây / lượt (luật cũ)']):matchControls,String(legacy?p.turn_ms:p.match_ms||300000))}</label>`;
}
export function timeBody(data) {
  if(data.get('match_ms'))return {match_ms:Number(data.get('match_ms'))};
  if(data.get('turn_ms'))return {turn_ms:Number(data.get('turn_ms'))};
  throw new Error('Chọn thể thức thời gian trước khi gửi.');
}
export function remainingClocks(clocks,turn,now,live) {
  if(!clocks)return null;
  const result={...clocks};
  if(live&&turn&&Number.isFinite(now))result[turn.side]=Math.max(0,result[turn.side]-Math.max(0,now-turn.estimated_started_unix_ms));
  return result;
}
export function clockText(ms) {
  if(!Number.isFinite(ms))return '—';
  const tenths=Math.ceil(Math.max(0,ms)/100),seconds=Math.floor(tenths/10);
  return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}.${tenths%10}`;
}
