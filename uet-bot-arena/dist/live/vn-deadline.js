import {tr,locale} from './i18n.js';
import {esc,select} from './view.js';

const localParts=ms=>new Date(ms+7*3600000).toISOString().slice(0,16).split('T');
export function vnDeadlineText(ms){
 if(ms==null)return tr('Chưa đặt hạn.');
 const d=new Date(ms),options={timeZone:'Asia/Ho_Chi_Minh'};
 const time=new Intl.DateTimeFormat(locale(),{...options,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(d);
 const weekday=new Intl.DateTimeFormat(locale(),{...options,weekday:'long'}).format(d).replace(/^Thứ/,'thứ');
 const date=new Intl.DateTimeFormat(locale(),{...options,day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
 return tr('Hạn: ')+time+' '+weekday+' '+date+tr(' (giờ Việt Nam)');
}
export function vnDeadlineField(name,ms=null){
 const [day,time]=ms==null?['','00:00']:localParts(ms);
 const times=Array.from({length:48},(_,i)=>String(Math.floor(i/2)).padStart(2,'0')+':'+(i%2?'30':'00'));
 if(!times.includes(time))times.push(time); // Preserve a previously saved minute when editing an older deadline.
 const label=name==='team-deadline'?'Hạn lập đội (giờ Việt Nam)':'Hạn nộp (giờ Việt Nam)';
 return `<div class="vn-deadline field" data-vn-deadline="${esc(name)}"><span>${tr(label)}</span><div class="form-grid"><label class="field"><span>${tr('Ngày')}</span><input class="input" name="${esc(name)}-date" type="date" value="${esc(day)}"></label><label class="field"><span>${tr('Giờ')}</span>${select(name+'-time',times.map(t=>[t,t]),time)}</label></div><p class="muted" data-vn-deadline-preview="${esc(name)}" ${name==='submission_deadline'?'data-ladder-duration':''}>${vnDeadlineText(ms)}</p></div>`;
}
export function readVnDeadline(source,name){
 const get=key=>source.get?source.get(key):source.elements[key]?.value;
 const day=get(name+'-date'),time=get(name+'-time');
 if(!day)return null;
 if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||!/^([01]\d|2[0-3]):[0-5]\d$/.test(time||''))throw new Error(tr('Hạn không hợp lệ.'));
 const ms=Date.parse(day+'T'+time+':00+07:00');
 if(!Number.isFinite(ms)||localParts(ms).join('T')!==day+'T'+time)throw new Error(tr('Hạn không hợp lệ.'));
 return ms;
}
export function updateVnDeadlinePreviews(form){
 if(!form)return;
 for(const p of form.querySelectorAll('[data-vn-deadline-preview]')){
  try{p.textContent=vnDeadlineText(readVnDeadline(form,p.dataset.vnDeadlinePreview));}
  catch{p.textContent=tr('Hạn không hợp lệ.');}
 }
}
