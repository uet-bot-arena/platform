import {tr} from './i18n.js';
import {esc,panel,note,select,submit,errors,link,date} from './view.js';
import {boardMarkup} from './board.js';
import {resultLabel} from './result.js';

export const submissionLabel=x=>(x.channel==='practice'?tr('Thử nhanh')+' · ':'')+x.version;
export function quickPractice(s){
  if(!s.platform?.practice_upload||s.platform.team_zip_upload)return '';
  const rows=(s.submissions||[]).filter(x=>x.channel==='practice').map(x=>{
    const job=(s.jobs||[]).find(j=>j.kind==='benchmark'&&j.submission_ids.includes(x.id));
    return `<li><code>${esc(x.version)}</code> ${job?link('jobs/'+job.id,tr('Log kiểm tra')):''}</li>`;
  }).join('');
  return panel(tr('Thử nhanh không cần GitHub'),`<form id="quick-practice-form" class="real-form"><label class="field"><span>${tr('Ngôn ngữ')}</span>${select('runtime',[['cpp17','C++17'],['c11','C11'],['python3','Python 3']])}</label><label class="field"><span>${tr('Mã nguồn')}</span><textarea name="code" rows="16" class="practice-code" required spellcheck="false"></textarea></label><label class="field"><span>${tr('Chọn file')}</span><input type="file" id="practice-file" accept=".cpp,.c,.py,.zip"></label><button type="button" class="btn secondary" data-action="practice-example">${tr('Điền bot mẫu C++')}</button>${note(tr('Bản thử nhanh chỉ để kiểm 72 mẫu và luyện tập (đấu bot mẫu hoặc bản khác của đội). Giải đấu chỉ nhận commit GitHub.'))}${note(tr('Tối đa 10 bản thử nhanh mỗi giờ; file mã tối đa 200.000 byte hoặc ZIP tối đa 1 MiB.'))}${note(s.user?.role==='admin'?tr("Bot thử của BTC chỉ thuộc tài khoản hiện tại, không được dùng để đăng ký giải."):tr('Khi vào đội, Thử nhanh và Luyện tập dùng dữ liệu của đội; các bản cá nhân cũ không chuyển sang đội và không còn hiện ở đây.'))}${errors()}${submit(tr('Nạp và kiểm tra'))}</form>${rows?'<ul>'+rows+'</ul>':''}`);
}
export async function readPracticeFile(file){
  if(file?.name.toLowerCase().endsWith('.zip')){if(file.size>1024*1024)throw new Error(tr('ZIP thử nhanh tối đa 1 MiB'));return {zip:file};}
  const runtime={cpp:'cpp17',c:'c11',py:'python3'}[file?.name.split('.').pop().toLowerCase()];
  if(!runtime||file.size>200000)throw new Error(tr('Chọn file .cpp, .c hoặc .py tối đa 200.000 byte.'));
  const code=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error(tr('Không đọc được file.')));reader.readAsText(file,'UTF-8');});
  if(new TextEncoder().encode(code).length>200000)throw new Error(tr('Chọn file .cpp, .c hoặc .py tối đa 200.000 byte.'));
  return {code,runtime};
}
export async function recentMatch(api,competitions){
  const c=competitions.filter(c=>!c.archived_ms&&c.fixture_counts?.completed).sort((a,b)=>(b.recent_completed?.completed_ms||b.completed_ms||b.created_ms)-(a.recent_completed?.completed_ms||a.completed_ms||a.created_ms))[0];
  if(!c)return null;
  const fixtures=await api.snapshot('fixtures/'+c.id+'.json');
  const f=fixtures.items.filter(f=>f.status==='completed').sort((a,b)=>(b.completed_ms||0)-(a.completed_ms||0)||b.ordinal-a.ordinal)[0];
  if(!f)return null;
  const replay=await api.snapshot(`fixtures/${c.id}/${f.id}.json`);
  const state=replay.items.map(e=>e.data?.state).filter(Boolean).at(-1);
  return state?{competition:c,fixture:f,state}:null;
}
export function recentMatchPanel(value,competitions=[]){
  if(!value)return '';
  const {competition:c,fixture:f,state}=value;
  const name=side=>c.entries.find(e=>e.team_id===f[side+'_team_id'])?.name||f[side+'_team_id'];
  const total=competitions.filter(c=>!c.archived_ms).reduce((n,c)=>n+(c.fixture_counts?.completed||0),0);
  return panel(tr('Một trận vừa đấu'),`<p class="muted">${esc(c.title)} · ${tr('Trận ')}${esc(f.ordinal)} · ${tr('xong lúc ')}${esc(date(f.completed_ms))} · ${esc(state.ply)} ${tr('nước')}</p><div class="recent-match"><div class="recent-board">${boardMarkup(state,c.config.basic_rules?.definition)}</div><div><p><span class="side-dot red"></span>${esc(name('red'))} · ${tr('Đỏ')}<br><span class="side-dot blue"></span>${esc(name('blue'))} · ${tr('Xanh')}</p><p><strong>${esc(resultLabel(f.result,name))}</strong></p>${link(`replay/${c.id}/${f.id}`,tr('Xem lại cả trận'),'primary')}<p class="muted">${tr('Các giải trên web đã đấu ')}${esc(total)}${tr(' ván')} · <a class="text-link" href="#/schedule">${tr('Xem lịch thi đấu ›')}</a></p></div></div>`);
}
