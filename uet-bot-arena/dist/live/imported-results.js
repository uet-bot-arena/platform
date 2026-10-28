import {tr} from './i18n.js';
import {esc,panel,note,link,button,field,submit,errors,table,date} from './view.js';
import {formats} from './competition-ui.js';

export function importedCards(s){
  const items=s.importedEvents||[];
  if(!items.length)return '';
  return panel(tr('Kết quả từ hub chung kết'),`<div class="real-card-grid">${items.map(event=>`<article class="real-card"><h3>${esc(event.name)}</h3><p>${tr('Nạp lúc ')}${date(event.imported_ms)}</p>${(event.finals||[]).map(f=>link('finals/'+f.id,f.name)).join('')}<ol>${event.competitions.map(c=>`<li>${link('competitions/'+c.id,c.title)}</li>`).join('')}</ol>${s.user?.role==='admin'?button(tr('Ẩn'),'hide-imported',`data-id="${esc(event.id)}"`,'small danger-button'):''}</article>`).join('')}</div>`);
}
export function importedBanner(event){
  return panel(tr('Kết quả từ hub chung kết'),note(tr('Kết quả nhập từ hub LAN lúc ')+date(event.imported_ms)+' · SHA '+esc(event.sha256.slice(0,12))));
}
export function resultsTools(s){
  return s.user?.role==='admin'?`<div class="page-actions">${button(tr('Xuất kết quả lên web'),'results-export')}${button(tr('Nạp kết quả từ hub LAN'),'results-import')}</div>`:'';
}
export function exportForm(s){
  const cups=s.competitions.filter(c=>c.status==='completed'&&!c.archived_ms);
  const finals=(s.finals||[]).filter(f=>f.status==='completed'&&!(s.importedEvents||[]).some(e=>(e.finals||[]).some(x=>x.id===f.id)));
  return `<form id="results-export-form" class="real-form">${field(tr('Tên sự kiện'),'event_name','required','text',tr('Chung kết UET Bot Arena ')+new Date().getFullYear())}${finals.length?`<h3>${tr('Chung kết tự động (gồm mọi bảng/cặp)')}</h3>${finals.map(f=>`<label class="check-field"><input type="checkbox" name="finals" value="${esc(f.id)}">${esc(f.name)}</label>`).join('')}`:''}${cups.map(c=>`<label class="check-field"><input type="checkbox" name="competitions" value="${esc(c.id)}">${esc(c.title)}</label>`).join('')}${note(tr('Chỉ xuất giải đã hoàn tất, theo thứ tự bạn chọn.'))}${errors()}${submit(tr('Tải file kết quả'))}</form>`;
}
export function importForm(preview=null){
  const summary=preview?`<h3>${esc(preview.name)}</h3><p class="mono">SHA ${esc(preview.sha256)}</p>${table([tr('Giải đấu'),tr('Thể thức'),tr('Số đội'),tr('Số ván'),tr('Số replay')],preview.competitions.map(c=>`<tr><td>${esc(c.title)}</td><td>${esc(tr(formats[c.format]||c.format))}</td><td>${esc(c.teams)}</td><td>${esc(c.games)}</td><td>${esc(c.replays)}</td></tr>`))}${button(tr('Đăng lên web'),'results-confirm','','primary')}`:'';
  return `<form id="results-import-form" class="real-form">${field(tr('File kết quả .json.gz'),'results','required accept=".gz,application/gzip"','file')}${note(tr('Xem trước không công bố dữ liệu. Chỉ nạp dữ liệu công khai, không nhập tài khoản hoặc mã bot.'))}${errors()}${submit(tr('Kiểm tra & xem trước'))}</form>${summary}`;
}
export function importedEventFor(items,route,ids,q){
  const cid=['competitions','arena','replay'].includes(route)?ids[0]:q.get('competition');
  return items.find(e=>e.competitions.some(c=>c.id===cid))||null;
}
