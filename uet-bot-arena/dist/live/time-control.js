import {tr,html as l10n,locale} from './i18n.js';
import {select} from './view.js';

export const matchControls=[['10000',tr('10 giây mỗi bot (mặc định)')],['60000',tr('1 phút mỗi bot')],['300000',tr('5 phút mỗi bot')]];
export const competitionProfile=c=>c.config.format==='elo'?{...c.config.profile,match_ms:c.config.official?.match_ms??10000}:c.config.profile;
export const timeLabel=p=>p?.id?.startsWith('ottv2-check-500ms-v1')?tr('Kiểm tra · 0,5 giây / mẫu'):p?.match_ms?tr('Mỗi bot ')+(p.match_ms===60000?tr('1 phút'):p.match_ms===300000?tr('5 phút'):l10n`${p.match_ms/1000} giây`)+tr(' cho cả ván'):l10n`${(p?.turn_ms||15000)/1000}s / lượt (luật cũ)`;
export const timeQuery=p=>p?.match_ms?'match='+p.match_ms:'turn='+(p?.turn_ms||15000);
export function timeField(p={match_ms:300000}) {
  const legacy=!p.match_ms&&p.turn_ms;
  return `<label class="field"><span>${legacy?tr('Thời gian (giải cũ)'):tr('Quỹ thời gian mỗi bot cho cả ván')}</span>${select(legacy?'turn_ms':'match_ms',legacy?[15000,30000,60000,300000].map(v=>[String(v),v/1000+tr(' giây / lượt (luật cũ)')]):matchControls,String(legacy?p.turn_ms:p.match_ms||300000))}</label>`;
}
export function timeBody(data) {
  if(data.get('match_ms'))return {match_ms:Number(data.get('match_ms'))};
  if(data.get('turn_ms'))return {turn_ms:Number(data.get('turn_ms'))};
  throw new Error(tr('Chọn thể thức thời gian trước khi gửi.'));
}
export function remainingClocks(clocks,turn,now,live) {
  if(!clocks)return null;
  const result={...clocks};
  if(live&&turn&&Number.isFinite(now))result[turn.side]=Math.max(0,result[turn.side]-Math.max(0,now-turn.estimated_started_unix_ms));
  return result;
}
export function clockText(ms) {
  if(!Number.isFinite(ms))return '—';
  if(ms<60000){const hundredths=Math.floor(Math.max(0,ms)/10);return `${Math.floor(hundredths/6000)}:${String(Math.floor(hundredths/100)%60).padStart(2,'0')}.${String(hundredths%100).padStart(2,'0')}`;}
  const tenths=Math.ceil(Math.max(0,ms)/100),seconds=Math.floor(tenths/10);
  return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}.${tenths%10}`;
}
export function thinkingTime(previous,current,side){
  if(!side||!Number.isFinite(previous?.[side])||!Number.isFinite(current?.[side]))return null;
  const elapsed=previous[side]-current[side];return elapsed>=0?elapsed:null;
}
export function thinkingText(ms){
  return ms<1000?ms.toLocaleString(locale(),{maximumFractionDigits:1})+' ms':(ms/1000).toLocaleString(locale(),{minimumFractionDigits:2,maximumFractionDigits:2})+' s';
}
