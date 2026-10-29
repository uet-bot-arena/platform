import {tr,locale} from './i18n.js';
import {esc,select} from './view.js';

const localParts=ms=>new Date(ms+7*3600000).toISOString().slice(0,16).split('T');
export function vnDeadlineText(ms,prefix=tr('Hạn: ')){
 if(ms==null)return tr('Chưa đặt hạn.');
 const d=new Date(ms),options={timeZone:'Asia/Ho_Chi_Minh'};
 const time=new Intl.DateTimeFormat(locale(),{...options,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(d);
 const weekday=new Intl.DateTimeFormat(locale(),{...options,weekday:'long'}).format(d).replace(/^Thứ/,'thứ');
 const date=new Intl.DateTimeFormat(locale(),{...options,day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
 return prefix+time+' '+weekday+' '+date+tr(' (giờ Việt Nam)');
}
const preview=(name,ms)=>name==='first_round_ms'?(ms==null?tr('Để trống: lấy phút kế tiếp khi Mở giải; nếu đang mở thì lấy phút kế tiếp sau khi lưu.'):vnDeadlineText(ms,tr('Đợt đầu: '))):vnDeadlineText(ms);
export function vnDeadlineField(name,ms=null){
 const [day,time]=ms==null?['','00:00']:localParts(ms),[hour,minute]=time.split(':');
 const label=name==='team-deadline'?'Hạn lập đội (giờ Việt Nam)':name==='first_round_ms'?'Đợt đầu lúc (giờ Việt Nam)':'Hạn nộp (giờ Việt Nam)';
 const options=n=>Array.from({length:n},(_,i)=>{const v=String(i).padStart(2,'0');return [v,v];});
 return `<div class="vn-deadline field" data-vn-deadline="${esc(name)}"><span>${tr(label)}</span><div class="form-grid"><label class="field"><span>${tr('Ngày')}</span><input class="input" name="${esc(name)}-date" type="date" value="${esc(day)}"></label><div class="form-grid"><label class="field"><span>${tr('Giờ')}</span>${select(name+'-hour',options(24),hour)}</label><label class="field"><span>${tr('Phút')}</span>${select(name+'-minute',options(60),minute)}</label></div></div><p class="muted" data-vn-deadline-preview="${esc(name)}">${preview(name,ms)}</p></div>`;
}
export function readVnDeadline(source,name){
 const get=key=>source.get?source.get(key):source.elements[key]?.value;
 const day=get(name+'-date'),hour=get(name+'-hour'),minute=get(name+'-minute');
 if(!day)return null;
 if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||!/^([01]\d|2[0-3])$/.test(hour||'')||!/^([0-5]\d)$/.test(minute||''))throw new Error(tr('Hạn không hợp lệ.'));
 const time=hour+':'+minute,ms=Date.parse(day+'T'+time+':00+07:00');
 if(!Number.isFinite(ms)||localParts(ms).join('T')!==day+'T'+time)throw new Error(tr('Hạn không hợp lệ.'));
 return ms;
}
export function updateVnDeadlinePreviews(form){
 if(!form)return;
 for(const p of form.querySelectorAll('[data-vn-deadline-preview]')){
  try{p.textContent=preview(p.dataset.vnDeadlinePreview,readVnDeadline(form,p.dataset.vnDeadlinePreview));}
  catch{p.textContent=tr('Hạn không hợp lệ.');}
 }
}
