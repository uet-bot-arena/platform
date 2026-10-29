import {tr,html as l10n,locale} from './i18n.js';
import {date} from './view.js';

const MINUTE=60000,DAY=86400000,VN=7*3600000;
export const nextMinute=now=>(Math.floor(now/MINUTE)+1)*MINUTE;
export const ladderMinutes=ladder=>ladder.round_minutes??60*(ladder.round_hours??2);
export function roundBoundary(now,ladder,{next=false}={}){
 if(ladder.round_minutes==null){
  const hours=ladder.round_hours??2,local=now+VN,day=Math.floor(local/DAY),hour=Math.floor((local-day*DAY)/3600000/hours)*hours+(next?hours:0);
  return (day+(hour>=24?1:0))*DAY-VN+(hour>=24?0:hour*3600000);
 }
 const step=ladder.round_minutes*MINUTE,first=ladder.first_round_ms??nextMinute(now);
 return first+Math.max(next?0:-1,Math.floor((now-first)/step)+(next?1:0))*step;
}
export function schedulePreview(ladder,start,deadline){
 let first=roundBoundary(start,ladder);
 if(ladder.round_minutes!=null)first=Math.max(first,ladder.first_round_ms??nextMinute(start));
 if(first<start)first=roundBoundary(start,ladder,{next:true});
 if(deadline<=first)return {times:[],rounds:0};
 if(ladder.round_minutes!=null){const step=ladder.round_minutes*MINUTE,rounds=Math.ceil((deadline-first)/step);return {times:Array.from({length:Math.min(5,rounds)},(_,i)=>first+i*step),rounds};}
 const hours=ladder.round_hours??2,slots=Array.from({length:Math.ceil(24/hours)},(_,i)=>i*hours);
 const before=at=>{const local=at+VN,day=Math.floor(local/DAY),offset=local-day*DAY;return day*slots.length+slots.filter(h=>h*3600000<offset).length;};
 const times=[];for(let at=first;at<deadline&&times.length<5;at=roundBoundary(at,ladder,{next:true}))times.push(at);
 return {times,rounds:before(deadline)-before(start)};
}
export function ladderInterval(ladder={}){
 const minutes=ladderMinutes(ladder),hours=Math.floor(minutes/60),rest=minutes%60;
 const unit=(value,name)=>new Intl.NumberFormat(locale(),{style:'unit',unit:name,unitDisplay:'long'}).format(value);
 return [hours?unit(hours,'hour'):'',rest?unit(rest,'minute'):''].filter(Boolean).join(' ');
}
export function ladderPreview(ladder,deadline,start=Date.now()){
 if(!Number.isFinite(deadline))return tr('Chọn hạn nộp để ước tính số đợt ladder.');
 const value=schedulePreview(ladder,start,deadline),times=value.times.map(ms=>date(ms)).join(', ');
 return value.rounds?l10n`Đợt mở lúc ${times}${value.rounds>5?', …':''} (giờ Việt Nam) · tới hạn khoảng ${value.rounds} đợt`:tr('Không có mốc ladder trước hạn nộp.');
}
