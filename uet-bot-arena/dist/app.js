import {initialMembers, initialSubmissions, matches, matchFor, teams, teamById, boardAt} from './data.js';
import {renderPage} from './pages.js';
import {updateMatchView} from './match-view.js';
import {authState} from './auth-ui.js';
import {createAuthDemo} from './auth-demo.js';
import {exportBenchmark} from './benchmark-ui.js';
import {LANGUAGE_POLICY,availableRuntimes,submissionProfile} from './runtime-config.js';
import {icon, btn, avatar, badge, escapeHTML as esc} from './ui.js';
const STORAGE='uet-arena-demo-v1';
let saved={};
try{saved=JSON.parse(localStorage.getItem(STORAGE)||'{}');}catch{}
const validMembers=Array.isArray(saved.members)&&saved.members.length<=4&&saved.members.every(m=>typeof m.name==='string'&&typeof m.email==='string');
export const state={
  ...authState(saved),persistUser:!!saved.user,
  route:'home', detailId:'', menuOpen:false, user:saved.user&&typeof saved.user.name==='string'?{...saved.user,role:saved.user.role||'team'}:null,
  teamName:saved.teamName||'Byte Knights',teamMotto:saved.teamMotto||'Think ahead. Code together.',
  members:validMembers?saved.members:structuredClone(initialMembers),
  submissions:Array.isArray(saved.submissions)&&saved.submissions.every(s=>typeof s.id==='string'&&typeof s.version==='string')?saved.submissions:structuredClone(initialSubmissions),
  liveFilter:'all',scheduleFilter:'all',group:'all',round:'all',sort:'points',teamSearch:'',
  pendingFile:null,draftVersion:'',draftNote:'',uploadError:'',draftRuntime:LANGUAGE_POLICY.defaultRuntime,draftCodonConsent:false,
  liveStep:24,replayStep:0,replayTotal:42,demoPaused:false,replayPlaying:false,flipped:false,historyTab:'moves',
  authMode:'login',drawPairs:[],drawConfirmed:false
};
teams[0].name=state.teamName;
let modalFocus=null,toastTimer=null;
function persist(){teams[0].name=state.teamName;try{localStorage.setItem(STORAGE,JSON.stringify({user:state.persistUser?state.user:null,teamName:state.teamName,teamMotto:state.teamMotto,members:state.members,submissions:state.submissions,accounts:state.accounts,registrations:state.registrations,resets:state.resets,registrationReceipt:state.registrationReceipt}));}catch{}}
const auth=createAuthDemo({state,render,persist,modal,closeModal,toast,download});
function toast(message){const el=document.querySelector('#toast');el.textContent=message;el.className='toast show';clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),4500);}
function render({keepScroll=true}={}){
  const y=window.scrollY;
  const focused=document.activeElement;
  const focusId=focused?.id;
  const focusAction=focused?.dataset?.action,focusSquare=focused?.dataset?.square;
  const start=focused?.selectionStart,end=focused?.selectionEnd;
  document.querySelector('#app').innerHTML=renderPage(state);
  document.title=`${({home:'Tổng quan',live:'Trực tiếp',schedule:'Lịch thi đấu',standings:'Bảng xếp hạng',team:'Hồ sơ đội',submissions:'Nộp bot',benchmark:'Test & benchmark',arena:'Phòng thi đấu',replay:'Replay',login:'Đăng nhập',register:'Đăng ký đội thi',recover:'Cấp lại mật khẩu','change-password':'Đổi mật khẩu',admin:'Tài khoản đội thi',guide:'Tài liệu'})[state.route]||'Tổng quan'} · UET Bot Arena`;
  if(keepScroll)window.scrollTo(0,y);
  if(focusId){const next=document.getElementById(focusId);if(next){next.focus({preventScroll:true});if(typeof start==='number'&&next.setSelectionRange)next.setSelectionRange(start,end);}}
  else if(keepScroll&&focusAction)document.querySelector(`[data-action="${CSS.escape(focusAction)}"]`)?.focus({preventScroll:true});
  else if(keepScroll&&focusSquare)document.querySelector(`[data-square="${CSS.escape(focusSquare)}"]`)?.focus({preventScroll:true});
  const history=document.querySelector('#history-list');if(history)history.scrollTop=history.scrollHeight;
}
function route(){
  const [page='home',id='']=location.hash.replace(/^#\/?/,'').split('/');
  const known=['home','live','schedule','standings','team','submissions','benchmark','arena','replay','guide','login','register','recover','change-password','admin'];
  state.route=known.includes(page)?page:'home';state.detailId=id;state.menuOpen=false;state.group='all';
  state.replayPlaying=false;state.historyTab='moves';
  if(state.route==='arena'){const m=matchFor(state,id);state.liveStep=m?.turn||0;state.demoPaused=m?.status!=='live';}
  if(state.route==='replay'){state.replayStep=0;const m=matchFor(state,id);state.replayTotal=m?.status==='finished'?m.turn:42;}
  closeModal();render({keepScroll:false});window.scrollTo(0,0);
}
function modal(title,body){
  modalFocus=document.activeElement;
  document.querySelector('#modal-root').innerHTML=`<div class="modal-overlay" data-overlay="true"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header class="modal-head"><h2 id="modal-title">${title}</h2><button class="icon-btn" data-action="close-modal" aria-label="Đóng hộp thoại">${icon('x')}</button></header><div class="modal-body">${body}</div></section></div>`;
  document.body.classList.add('no-scroll');
  const first=document.querySelector('.modal input,.modal textarea,.modal select,.modal button');first?.focus();
}
function closeModal(){document.querySelector('#modal-root').innerHTML='';document.body.classList.remove('no-scroll');modalFocus?.focus?.({preventScroll:true});modalFocus=null;}
function download(name,text,type='text/plain;charset=utf-8'){
  const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),500);
}
function exportCSV(){
  const rows=[['Hạng','Đội','Bảng','Trận','Thắng','Hòa','Thua','Điểm'],...teams.map(t=>[t.rank,t.name,t.group,t.played,t.win,t.draw,t.loss,t.points])];
  download('uet-bot-arena-bang-diem-demo.csv','\uFEFF'+rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n'),'text/csv;charset=utf-8');toast('Đã tải bảng điểm demo (.csv).');
}
function drawModal(){
  if(!state.drawPairs.length)shufflePairs();
  modal('Bốc thăm thử · Vòng 7',`<p>Ghép cặp trong cùng bảng A/B để xem trước giao diện vận hành giải. Kết quả chỉ cập nhật lịch demo trên trình duyệt này.</p><div>${state.drawPairs.map((pair,i)=>`<div class="draw-pair"><a class="team-cell" href="#/team/${pair[0]}">${avatar(teamById(pair[0]))}${teamById(pair[0]).name}</a><span>vs</span><a class="team-cell" href="#/team/${pair[1]}">${teamById(pair[1]).name}</a></div>`).join('')}</div><div class="modal-actions">${btn('Bốc lại','redraw','secondary','shuffle')}${btn('Áp dụng lịch demo','confirm-draw','primary','check')}</div>`);
}
function shufflePairs(){
  state.drawPairs=[];
  for(const group of ['A','B']){
    const ids=teams.filter(t=>t.group===group).map(t=>t.id);
    for(let i=ids.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[ids[i],ids[j]]=[ids[j],ids[i]];}
    for(let i=0;i<ids.length;i+=2)state.drawPairs.push([ids[i],ids[i+1]]);
  }
}
function memberModal(){
  if(state.members.length>=4){toast('Đội đã đủ 4 người.');return;}
  modal('Mời thành viên',`<p>Thêm một thành viên vào đội demo. Thao tác này không gửi email hay lời mời thật.</p><form id="invite-form"><label class="field"><span>Họ và tên</span><input class="input" name="name" required minlength="2" maxlength="60" placeholder="Họ tên thành viên"></label><label class="field"><span>Email sinh viên</span><input class="input" name="email" type="email" required placeholder="thanhvien@vnu.edu.vn" maxlength="120"></label><div id="invite-error" class="error-inline" role="alert"></div><div class="modal-actions">${btn('Hủy','close-modal')}<button type="submit" class="btn primary">${icon('plus')}Thêm vào đội demo</button></div></form>`);
}
async function selectFile(file){
  if(!file)return;
  const form=document.querySelector('#submission-form');
  if(form){state.draftVersion=form.elements.version.value;state.draftNote=form.elements.note.value;}
  let error='';
  if(!file.name.toLowerCase().endsWith('.zip'))error='Hãy chọn file .zip chứa source bot.';
  else if(file.size>10*1024*1024)error='File vượt quá 10 MB. Hãy bỏ các file không cần thiết và thử lại.';
  else if(file.size<4)error='File ZIP trống hoặc không hợp lệ.';
  else{
    const bytes=new Uint8Array(await file.slice(0,4).arrayBuffer());
    if(bytes[0]!==0x50||bytes[1]!==0x4b||!((bytes[2]===3&&bytes[3]===4)||(bytes[2]===5&&bytes[3]===6)))error='File không có định dạng ZIP. Hãy nén source thành một file ZIP mới.';
  }
  state.uploadError=error;state.pendingFile=error?null:{name:file.name,size:file.size<1024*1024?Math.max(1,Math.round(file.size/1024))+' KB':(file.size/1024/1024).toFixed(1)+' MB'};
  render();
  if(!error)toast('Đã chọn file. Nội dung gói sẽ cần backend kiểm tra.');
}
function submissionDetail(id){
  const s=state.submissions.find(x=>x.id===id);if(!s)return;
  const consent=s.runtime==='python-codon'?(s.codonConsent?' · Đội đã xác nhận thử nghiệm':' · Chưa có xác nhận thử nghiệm'):'';
  const statusNote=s.status==='preview'
    ?`<div class="info-note">${icon('info')}Bản demo đã lưu tên file, runtime và ghi chú. Chưa upload source hoặc xác minh cấu trúc, thư viện, resource usage và protocol.</div>`
    :s.status==='rejected'
      ?`<div class="info-note" style="background:#fbf1eb;color:#a87963">${icon('info')}Dữ liệu mẫu: thiếu ${s.runtime==='cpp17'?'bot.cpp':'bot.py'} ở thư mục gốc. Hãy sửa entry point rồi nộp một phiên bản mới.</div>`
      :`<ul class="check-list"><li>${icon('check')}Dữ liệu mẫu: đúng cấu trúc gói.</li><li>${icon('check')}Dữ liệu mẫu: protocol trả action đúng định dạng.</li><li>${icon('check')}Dữ liệu mẫu: thư viện nằm trong danh sách cho phép.</li></ul>`;
  modal('Chi tiết phiên bản '+esc(s.version),`<div style="display:flex;gap:12px;align-items:center;margin-bottom:20px"><span class="bot-file-icon">${icon('file')}</span><div><strong>${esc(s.file)}</strong><div style="font-size:10px;color:#95a289">${esc(s.size)} · ${esc(s.id)}</div></div>${badge(s.status)}</div><p><strong>Runtime:</strong> ${submissionProfile(s).label}${consent}</p><p>${esc(s.note||'Không có ghi chú cho phiên bản này.')}</p>${statusNote}<div class="modal-actions">${btn('Đóng','close-modal','primary')}</div>`);
}
const actions={
  'runtime-default':()=>{state.draftRuntime=LANGUAGE_POLICY.defaultRuntime;state.draftCodonConsent=false;state.pendingFile=null;state.uploadError='';render();toast('Đã chọn C++17. Hãy chọn gói source C++ cho phiên bản mới.');},
  'benchmark-export':()=>exportBenchmark(download),
  'benchmark-open':el=>{state.benchmarkSubmission=el.dataset.id;location.hash='/benchmark';},
  'menu':()=>{state.menuOpen=!state.menuOpen;render();},
  'logout':()=>{state.user=null;try{const cache=JSON.parse(localStorage.getItem(STORAGE)||'{}');cache.user=null;localStorage.setItem(STORAGE,JSON.stringify(cache));}catch{}location.hash='/login';},
  'demo-login':()=>{state.user={name:'Minh Anh',initials:'MA'};persist();location.hash='/home';toast('Chào Minh Anh! Bạn đang dùng tài khoản demo.');},
  'auth-mode':el=>{state.authMode=el.dataset.value;render();},
  'password':el=>{const p=document.querySelector('#password');p.type=p.type==='password'?'text':'password';el.setAttribute('aria-label',p.type==='password'?'Hiện mật khẩu':'Ẩn mật khẩu');},
  'forgot':()=>modal('Khôi phục mật khẩu',`<p>Bản UI chưa kết nối dịch vụ xác thực nên không gửi email khôi phục. Bạn có thể quay lại và chọn “Trải nghiệm với tài khoản mẫu”.</p><div class="modal-actions">${btn('Đã hiểu','close-modal','primary')}</div>`),
  'close-modal':closeModal,
  'live-filter':el=>{state.liveFilter=el.dataset.value;render();},
  'schedule-filter':el=>{state.scheduleFilter=el.dataset.value;render();},
  'group-filter':el=>{state.group=el.dataset.value;render();},
  'sort':el=>{state.sort=el.dataset.value;render();},
  'refresh':()=>{if(['arena','replay'].includes(state.route))updateMatchView(state);else render();toast('Đã làm mới dữ liệu demo.');},
  'export-standings':exportCSV,
  'invite':memberModal,
  'remove-member':el=>{
    const member=state.members.find(m=>m.email===el.dataset.email);
    if(!member||member.role==='Đội trưởng')return;
    modal('Xóa thành viên khỏi đội demo',`<p>Xóa ${esc(member.name)} khỏi hồ sơ demo trên máy này. Thao tác không tác động tài khoản thật.</p><div class="modal-actions">${btn('Giữ thành viên','close-modal')}${btn('Xóa khỏi đội demo','confirm-remove','primary','',`data-email="${esc(member.email)}"`)}</div>`);
  },
  'confirm-remove':el=>{state.members=state.members.filter(m=>m.email!==el.dataset.email||m.role==='Đội trưởng');persist();closeModal();render();toast('Đã xóa thành viên khỏi đội demo.');},
  'edit-team':()=>modal('Chỉnh sửa thông tin đội',`<p>Thay đổi hồ sơ Byte Knights trong bản demo.</p><form id="team-form"><label class="field"><span>Tên đội</span><input name="name" class="input" value="${esc(state.teamName)}" required minlength="2" maxlength="40"></label><label class="field"><span>Thông điệp của đội</span><input name="motto" class="input" value="${esc(state.teamMotto)}" maxlength="120"></label><div class="modal-actions">${btn('Hủy','close-modal')}<button type="submit" class="btn primary">Lưu thay đổi</button></div></form>`),
  'clear-file':()=>{const f=document.querySelector('#submission-form');if(f){state.draftVersion=f.elements.version.value;state.draftNote=f.elements.note.value;}state.pendingFile=null;state.uploadError='';render();},
  'submission-detail':el=>submissionDetail(el.dataset.id),
  'draw':drawModal,
  'redraw':()=>{shufflePairs();drawModal();},
  'confirm-draw':()=>{state.drawConfirmed=true;state.scheduleFilter='upcoming';state.round='7';state.group='all';closeModal();render();toast('Đã áp dụng 4 cặp đấu cho vòng 7 trong lịch demo.');},
  'flip':()=>{state.flipped=!state.flipped;updateMatchView(state);},
  'demo-pause':()=>{state.demoPaused=!state.demoPaused;if(state.liveStep>=42)state.liveStep=0;updateMatchView(state);},
  'next-move':()=>{state.liveStep=Math.min(42,state.liveStep+1);if(state.liveStep===42)state.demoPaused=true;updateMatchView(state);},
  'history-tab':el=>{state.historyTab=el.dataset.value;updateMatchView(state);},
  'replay-start':()=>{state.replayStep=0;state.replayPlaying=false;updateMatchView(state);},
  'replay-prev':()=>{state.replayStep=Math.max(0,state.replayStep-1);state.replayPlaying=false;updateMatchView(state);},
  'replay-next':()=>{state.replayStep=Math.min(state.replayTotal,state.replayStep+1);state.replayPlaying=false;updateMatchView(state);},
  'replay-end':()=>{state.replayStep=state.replayTotal;state.replayPlaying=false;updateMatchView(state);},
  'replay-play':()=>{if(state.replayStep===state.replayTotal)state.replayStep=0;state.replayPlaying=!state.replayPlaying;updateMatchView(state);},
  'share':async()=>{try{await navigator.clipboard.writeText(location.href);toast('Đã sao chép liên kết phòng đấu.');}catch{modal('Chia sẻ phòng đấu',`<p>Sao chép liên kết bên dưới để chia sẻ màn hình này.</p><input class="input" aria-label="Liên kết phòng đấu" readonly value="${esc(location.href)}"><div class="modal-actions">${btn('Đóng','close-modal','primary')}</div>`);}},
  'support':()=>modal('Cần một chút trợ giúp?',`<p>Bắt đầu với bot mẫu và hướng dẫn trong mục Tài liệu. Để thử giao diện, bạn có thể lưu phiên bản, thêm thành viên và mở một trận đấu từ trang Trực tiếp.</p><div class="info-note">${icon('info')}Đây là bản demo frontend. Dữ liệu lưu cục bộ trên trình duyệt, chưa có tài khoản thật, upload source hay server thi đấu.</div><div class="modal-actions"><a class="btn primary" href="#/guide">Mở tài liệu ${icon('arrow')}</a></div>`),
  'notifications':()=>modal('Thông báo',`<div class="announcement" style="padding:0 0 18px"><span class="side-dot"></span><div><h3>Vòng 6 đang diễn ra</h3><p>Hai trận đang đấu và một trận technical pause.</p></div></div><div class="announcement" style="padding:18px 0"><span class="side-dot"></span><div><h3>Bot v1.3.0 đã chốt</h3><p>Byte Knights sử dụng phiên bản này cho vòng 6.</p></div></div><div class="announcement" style="padding:18px 0"><span class="side-dot blue"></span><div><h3>Lịch vòng 7 đã sẵn sàng</h3><p>Trận tiếp theo dự kiến bắt đầu lúc 15:00.</p></div></div><div class="modal-actions"><a href="#/schedule" class="btn primary">Xem lịch đấu ${icon('arrow')}</a></div>`)
};
Object.assign(actions,auth.actions);

document.addEventListener('click',event=>{
  const button=event.target.closest('[data-action]');
  if(button){if(button.closest('form')&&button.type!=='submit')event.preventDefault();actions[button.dataset.action]?.(button);}
  if(event.target.matches('[data-overlay]'))closeModal();
  const square=event.target.closest('[data-square]');if(square)toast(`Ô ${square.dataset.square} · Chế độ người xem, không điều khiển bot.`);
});
document.addEventListener('keydown',event=>{
  const dialog=document.querySelector('.modal');
  if(event.key==='Escape'){closeModal();if(state.menuOpen){state.menuOpen=false;render();}}
  if(dialog&&event.key==='Tab'){
    const items=[...dialog.querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(el=>!el.disabled);
    if(event.shiftKey&&document.activeElement===items[0]){event.preventDefault();items.at(-1)?.focus();}
    else if(!event.shiftKey&&document.activeElement===items.at(-1)){event.preventDefault();items[0]?.focus();}
  }
  if(['Enter',' '].includes(event.key)&&event.target.matches('[data-square]')){event.preventDefault();event.target.click();}
  if(['Enter',' '].includes(event.key)&&event.target.id==='dropzone'){event.preventDefault();document.getElementById('bot-file').click();}
});
document.addEventListener('change',event=>{
  if(event.target.id==='submission-runtime'){state.draftRuntime=event.target.value;state.draftCodonConsent=false;state.pendingFile=null;state.uploadError='';render();toast('Đã đổi runtime. Hãy chọn gói source phù hợp.');return;}
  if(event.target.id==='codon-consent')state.draftCodonConsent=event.target.checked;
  const benchmarkFields={'benchmark-runtime':'benchmarkRuntime','benchmark-algorithm':'benchmarkAlgorithm','benchmark-submission':'benchmarkSubmission'};
  if(benchmarkFields[event.target.id]){state[benchmarkFields[event.target.id]]=event.target.value;render();}
  if(event.target.id==='account-status'){state.accountStatus=event.target.value;render();}
  if(event.target.id==='bot-file')selectFile(event.target.files[0]);
  if(event.target.dataset.filter){state[event.target.dataset.filter]=event.target.value;render();}
});
document.addEventListener('input',event=>{
  if(event.target.id==='account-search'){state.accountSearch=event.target.value;render();}
  if(event.target.id==='teamSearch'){state.teamSearch=event.target.value;render();}
  if(event.target.id==='replay-range'){state.replayStep=Number(event.target.value);state.replayPlaying=false;updateMatchView(state);}
  if(event.target.closest('#submission-form')){
    if(event.target.name==='version')state.draftVersion=event.target.value;
    if(event.target.name==='note')state.draftNote=event.target.value;
  }
});
for(const name of ['dragover','dragleave','drop'])document.addEventListener(name,event=>{
  const zone=event.target.closest('#dropzone');if(!zone)return;
  event.preventDefault();zone.classList.toggle('dragging',name==='dragover');
  if(name==='drop')selectFile(event.dataTransfer.files[0]);
});
document.addEventListener('submit',event=>{
  if(event.target.matches('#issued-login-form,#registration-form,#first-password-form,#recovery-form,#issue-account-form,#reset-account-form,#reject-registration-form')){event.preventDefault();auth.submit(event.target);return;}
  if(!['invite-form','team-form','submission-form'].includes(event.target.id))return;
  event.preventDefault();const form=event.target,data=new FormData(form);
  if(form.id==='invite-form'){
    const name=String(data.get('name')).trim(),email=String(data.get('email')).trim().toLowerCase();
    if(state.members.some(m=>m.email.toLowerCase()===email)){document.querySelector('#invite-error').textContent='Email này đã có trong đội.';return;}
    if(state.members.length>=4){toast('Đội đã đủ 4 người.');return;}
    state.members.push({name,email,initials:name.split(' ').map(s=>s[0]).slice(-2).join('').toUpperCase(),role:'Thành viên',color:'#e7eddc'});
    persist();closeModal();render();toast('Đã thêm thành viên vào đội demo. Không gửi email.');
  }
  if(form.id==='team-form'){state.teamName=String(data.get('name')).trim();state.teamMotto=String(data.get('motto')).trim();persist();closeModal();render();toast('Đã lưu hồ sơ đội demo.');}
  if(form.id==='submission-form'){
    const runtime=String(data.get('language'));
    if(!availableRuntimes().includes(runtime)){state.uploadError='Runtime này không được mở cho lần nộp mới.';document.querySelector('#upload-error').textContent=state.uploadError;return;}
    if(runtime==='python-codon'&&!data.has('codonConsent')){state.uploadError='Đội cần xác nhận đồng ý thử Python + Codon trước khi lưu.';document.querySelector('#upload-error').textContent=state.uploadError;return;}
    if(!state.pendingFile){state.uploadError='Hãy chọn một file .zip trước khi lưu phiên bản.';document.querySelector('#upload-error').textContent=state.uploadError;return;}
    let version=String(data.get('version')).trim();if(!version.startsWith('v'))version='v'+version;
    if(state.submissions.some(s=>s.version===version)){state.uploadError='Phiên bản này đã tồn tại. Hãy dùng một tên phiên bản mới.';document.querySelector('#upload-error').textContent=state.uploadError;return;}
    const date=new Date().toLocaleString('vi-VN',{timeZone:'Asia/Bangkok',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
    const newId='SUB-'+String(Math.max(8,...state.submissions.map(s=>Number(s.id.split('-')[1])||0))+1).padStart(3,'0');
    state.submissions.unshift({id:newId,version,file:state.pendingFile.name,size:state.pendingFile.size,date,status:'preview',active:false,note:String(data.get('note')).trim(),runtime,runtimePolicy:LANGUAGE_POLICY.id,codonConsent:runtime==='python-codon'});
    state.pendingFile=null;state.draftVersion='';state.draftNote='';state.uploadError='';state.draftRuntime=LANGUAGE_POLICY.defaultRuntime;state.draftCodonConsent=false;persist();render();toast('Đã lưu phiên bản demo và runtime. Chưa upload hay kiểm tra source.');
  }
});
setInterval(()=>{
  if(document.hidden||document.querySelector('.modal'))return;
  if(state.route==='replay'&&state.replayPlaying){state.replayStep=Math.min(state.replayTotal,state.replayStep+1);if(state.replayStep===state.replayTotal)state.replayPlaying=false;updateMatchView(state);}
},850);
setInterval(()=>{
  if(document.hidden||document.querySelector('.modal')||state.route!=='arena'||state.demoPaused)return;
  state.liveStep=Math.min(42,state.liveStep+1);if(state.liveStep===42)state.demoPaused=true;updateMatchView(state);
},5000);
window.addEventListener('hashchange',route);
route();
