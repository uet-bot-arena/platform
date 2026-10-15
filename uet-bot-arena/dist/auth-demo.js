import {DEMO_USERNAME, DEMO_PASSWORD, emptyRegistration, passwordField} from './auth-ui.js';
import {icon, btn, badge, escapeHTML as esc} from './ui.js';

// UI simulation only. Secrets live in this module's memory and are never persisted.
export function createAuthDemo({state,render,persist,modal,closeModal,toast,download}) {
  const secrets=new Map([['ACC-001',DEMO_PASSWORD]]);
  let issued=null;
  const date=()=>new Date().toLocaleString('vi-VN',{timeZone:'Asia/Bangkok',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  const nextId=(prefix,items)=>prefix+'-'+String(Math.max(0,...items.map(x=>Number(x.id.split('-')[1])||0))+1).padStart(3,'0');
  const admin=()=>state.user?.role==='admin';
  const error=(id,message)=>{const el=document.getElementById(id);if(el)el.textContent=message;};
  const randomPassword=()=>{const alphabet='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';return 'Uet-'+[...crypto.getRandomValues(new Uint8Array(12))].map(n=>alphabet[n%alphabet.length]).join('');};
  function draftFromForm(form) {
    const data=new FormData(form),count=state.registrationDraft.members.length;
    return {team:String(data.get('team')||'').trim(),email:String(data.get('email')||'').trim(),phone:String(data.get('phone')||'').trim(),confirmed:data.has('confirmed'),members:Array.from({length:count},(_,i)=>({name:String(data.get('memberName'+i)||'').trim(),studentId:String(data.get('studentId'+i)||'').trim()}))};
  }
  function finishLogin(account,remember) {
    const request=state.registrations.find(r=>r.id===account.requestId);
    state.user={name:account.captain,initials:account.captain.split(' ').map(s=>s[0]).slice(-2).join('').toUpperCase(),role:'team',accountId:account.id};
    state.teamName=account.team;state.persistUser=remember;state.pendingLogin=null;
    if(request)state.members=request.members.map((m,i)=>({...m,email:i===0?account.email:m.studentId+'@vnu.edu.vn',initials:m.name.split(' ').map(s=>s[0]).slice(-2).join('').toUpperCase(),role:i===0?'Đội trưởng':'Thành viên',color:'#e7eddc'}));
    persist();location.hash='/team';toast('Đã vào hồ sơ đội bằng phiên đăng nhập demo.');
  }
  function showCredential(account,password) {
    issued={id:account.id,username:account.username,password,team:account.team,email:account.email};
    modal('Thông tin cấp tài khoản',`<div class="credential-heading"><span class="auth-success-mark">${icon('check')}</span><div><h3>${esc(account.team)}</h3><p>Mật khẩu tạm đã được tạo trong phiên demo này.</p></div></div><div class="credential-slip"><label>Mã tài khoản<input class="input" readonly value="${esc(account.username)}" aria-label="Mã tài khoản vừa cấp"></label><label for="issued-password">Mật khẩu tạm</label><div class="password-input"><input class="input" id="issued-password" type="password" readonly value="${esc(password)}" autocomplete="off"><button type="button" class="icon-btn" data-action="toggle-secret" data-target="issued-password" aria-label="Hiện mật khẩu tạm">${icon('eye')}</button></div><p>Phát riêng cho ${esc(account.captain)} · ${esc(account.email)}</p></div><div class="info-note">${icon('shield')}Đội phải đổi mật khẩu ở lần đăng nhập đầu. Bản demo không gửi email hoặc lưu mật khẩu vào localStorage.</div><div class="credential-tools">${btn('Sao chép thông tin','copy-credential','secondary small','link')}${btn('Tải phiếu cấp tài khoản','download-credential','secondary small','download')}</div><div class="modal-actions">${btn('Đóng','close-modal')}${btn('Đánh dấu đã phát','issued-delivered','primary','check')}</div>`);
  }
  function issueModal(requestId='') {
    if(!admin())return;
    const request=state.registrations.find(r=>r.id===requestId);
    if(request&&request.status!=='pending'){toast('Đăng ký này đã được xử lý.');return;}
    const username='UET-TEAM-'+String(Math.max(0,...state.accounts.map(a=>Number(a.id.split('-')[1])||0))+1).padStart(3,'0');
    modal(request?'Duyệt & cấp tài khoản đội':'Tạo tài khoản đội thi',`<p>${request?'Thông tin hồ sơ đã điền sẵn. Kiểm tra trước khi cấp tài khoản cho đội trưởng.':'Cấp tài khoản trực tiếp cho đội đã được ban tổ chức xác nhận.'}</p><form id="issue-account-form"><input type="hidden" name="requestId" value="${esc(requestId)}"><label class="field"><span>Tên đội</span><input class="input" name="team" value="${esc(request?.team||'')}" required minlength="2" maxlength="40"></label><label class="field"><span>Đội trưởng</span><input class="input" name="captain" value="${esc(request?.members[0]?.name||'')}" required minlength="2" maxlength="60"></label><label class="field"><span>Email đội trưởng</span><input class="input" name="email" type="email" value="${esc(request?.email||'')}" required maxlength="120"></label><label class="field"><span>Mã tài khoản</span><input class="input" name="username" value="${username}" required pattern="[A-Za-z0-9_-]{4,30}" maxlength="30" autocomplete="off"><small>4–30 ký tự, gồm chữ, số, dấu gạch ngang hoặc gạch dưới.</small></label>${passwordField('temporary-password','Mật khẩu tạm','required minlength="8" maxlength="128" autocomplete="off"')}<div class="password-generate">${btn('Tạo mật khẩu khác','generate-password','ghost small','shuffle')}<span>Đội sẽ đổi ở lần đăng nhập đầu.</span></div><div id="issue-error" class="auth-error" role="alert"></div><div class="modal-actions">${btn('Hủy','close-modal')}<button type="submit" class="btn primary">${icon('plus')}${request?'Duyệt & cấp tài khoản':'Tạo tài khoản demo'}</button></div></form>`);
    document.getElementById('temporary-password').value=randomPassword();
  }
  function resetModal(accountId,resetId='') {
    if(!admin())return;
    const account=state.accounts.find(a=>a.id===accountId);if(!account)return;
    modal('Cấp lại mật khẩu tạm',`<p>Cấp mật khẩu mới cho <strong>${esc(account.team)}</strong> (${esc(account.username)}). Mật khẩu cũ trong phiên demo sẽ bị thay thế; trạng thái tạm khóa vẫn được giữ.</p><form id="reset-account-form"><input type="hidden" name="accountId" value="${esc(accountId)}"><input type="hidden" name="resetId" value="${esc(resetId)}">${passwordField('temporary-password','Mật khẩu tạm mới','required minlength="8" maxlength="128" autocomplete="off"')}<div class="password-generate">${btn('Tạo mật khẩu khác','generate-password','ghost small','shuffle')}</div><div class="modal-actions">${btn('Hủy','close-modal')}<button class="btn primary" type="submit">Cấp lại mật khẩu</button></div></form>`);
    document.getElementById('temporary-password').value=randomPassword();
  }
  const actions={
    'demo-login':()=>{location.hash='/login';},
    'fill-demo-credentials':()=>{document.querySelector('#login-username').value=DEMO_USERNAME;document.querySelector('#login-password').value=secrets.get('ACC-001')||DEMO_PASSWORD;error('auth-error','');toast('Đã điền tài khoản thử. Bấm Đăng nhập để tiếp tục.');},
    'demo-admin':()=>{state.user={name:'Ban tổ chức',initials:'BTC',role:'admin'};state.persistUser=false;persist();location.hash='/admin/accounts';if(state.route==='admin')render();},
    'logout':()=>{state.user=null;state.persistUser=false;state.pendingLogin=null;persist();location.hash='/login';},
    'forgot':()=>{state.resetReceipt=null;location.hash='/recover';},
    'auth-mode':el=>{location.hash=el.dataset.value==='signup'?'/register':'/login';},
    'toggle-secret':el=>{const field=document.getElementById(el.dataset.target);if(!field)return;field.type=field.type==='password'?'text':'password';el.setAttribute('aria-label',field.type==='password'?'Hiện mật khẩu':'Ẩn mật khẩu');},
    'add-registration-member':()=>{const form=document.getElementById('registration-form');if(!form||state.registrationDraft.members.length>=4)return;state.registrationDraft=draftFromForm(form);state.registrationDraft.members.push({name:'',studentId:''});render();},
    'remove-registration-member':()=>{const form=document.getElementById('registration-form');if(!form||state.registrationDraft.members.length!==4)return;state.registrationDraft=draftFromForm(form);state.registrationDraft.members.pop();render();},
    'new-registration':()=>{state.registrationReceipt=null;state.registrationDraft=emptyRegistration();persist();render();},
    'account-tab':el=>{state.accountTab=el.dataset.value;render();},
    'issue-account':el=>issueModal(el.dataset.request||''),
    'generate-password':()=>{const field=document.getElementById('temporary-password');if(field)field.value=randomPassword();},
    'copy-credential':async()=>{if(!issued||!admin())return;try{await navigator.clipboard.writeText(`UET Bot Arena · THÔNG TIN DEMO\nĐội: ${issued.team}\nTài khoản: ${issued.username}\nMật khẩu tạm: ${issued.password}\nĐăng nhập: ${location.origin}${location.pathname}#/login\nĐổi mật khẩu ở lần đăng nhập đầu.`);toast('Đã sao chép thông tin demo. Chưa gửi cho đội.');}catch{toast('Không thể sao chép tự động. Bạn có thể xem và sao chép từ phiếu.');}},
    'download-credential':()=>{if(!issued||!admin())return;download(issued.username+'-demo.txt',`UET BOT ARENA · PHIẾU CẤP TÀI KHOẢN DEMO\n\nĐội: ${issued.team}\nMã tài khoản: ${issued.username}\nMật khẩu tạm: ${issued.password}\n\nĐổi mật khẩu ở lần đăng nhập đầu.\nThông tin thử, chỉ hoạt động trong phiên demo đang mở.`);},
    'issued-delivered':()=>{if(!issued||!admin())return;const a=state.accounts.find(a=>a.id===issued.id);if(a)a.delivered=true;issued=null;persist();closeModal();render();toast('Đã đánh dấu phát tài khoản trong bản demo.');},
    'mark-delivered':el=>{if(!admin())return;const a=state.accounts.find(a=>a.id===el.dataset.id);if(a){a.delivered=true;persist();render();}},
    'reset-account':el=>resetModal(el.dataset.id),
    'toggle-account':el=>{if(!admin())return;const a=state.accounts.find(a=>a.id===el.dataset.id);if(!a)return;modal(a.status==='locked'?'Mở khóa tài khoản':'Tạm khóa tài khoản',`<p>${a.status==='locked'?'Cho phép':'Tạm ngừng'} đăng nhập demo của đội <strong>${esc(a.team)}</strong>. ${a.status==='locked'?'':'Tài khoản có thể được mở lại sau.'}</p><div class="modal-actions">${btn('Hủy','close-modal')}${btn(a.status==='locked'?'Mở khóa':'Tạm khóa','confirm-account-lock','primary','',`data-id="${esc(a.id)}"`)}</div>`);},
    'confirm-account-lock':el=>{if(!admin())return;const a=state.accounts.find(a=>a.id===el.dataset.id);if(a){if(a.status==='locked')a.status=a.previousStatus||'temporary';else{a.previousStatus=a.status;a.status='locked';}persist();closeModal();render();}},
    'registration-detail':el=>{if(!admin())return;const r=state.registrations.find(r=>r.id===el.dataset.id);if(!r)return;modal('Hồ sơ đăng ký '+esc(r.id),`<div class="registration-detail"><h3>${esc(r.team)}</h3><p>${esc(r.email)}${r.phone?' · '+esc(r.phone):''}</p>${r.members.map((m,i)=>`<div><span>${String(i+1).padStart(2,'0')}</span><strong>${esc(m.name)}<small>${esc(m.studentId)}${i===0?' · Đội trưởng':''}</small></strong></div>`).join('')}</div><div class="modal-actions">${btn('Yêu cầu bổ sung','reject-registration','secondary','',`data-id="${esc(r.id)}"`)}${btn('Duyệt & cấp tài khoản','issue-account','primary','',`data-request="${esc(r.id)}"`)}</div>`);},
    'reject-registration':el=>{if(!admin())return;modal('Yêu cầu bổ sung thông tin',`<form id="reject-registration-form"><input type="hidden" name="requestId" value="${esc(el.dataset.id)}"><label class="field"><span>Nội dung cần bổ sung</span><textarea class="input" name="reason" required minlength="5" maxlength="300" rows="3" placeholder="Ví dụ: kiểm tra lại mã sinh viên của thành viên thứ ba."></textarea></label><div class="modal-actions">${btn('Hủy','close-modal')}<button class="btn primary" type="submit">Lưu yêu cầu demo</button></div></form>`);},
    'review-reset':el=>{if(!admin())return;const r=state.resets.find(r=>r.id===el.dataset.id);if(!r)return;const a=state.accounts.find(a=>a.username.toLowerCase()===r.username.toLowerCase()&&a.email.toLowerCase()===r.email.toLowerCase());if(a)resetModal(a.id,r.id);else modal('Thông tin chưa khớp',`<p>Mã tài khoản và email trong yêu cầu chưa khớp hồ sơ đội. Ban tổ chức cần kiểm tra lại với đội trưởng trước khi cấp mật khẩu.</p><div class="modal-actions">${btn('Đóng','close-modal','primary')}</div>`);}
  };
  function submit(form) {
    const ids=['issued-login-form','registration-form','first-password-form','recovery-form','issue-account-form','reset-account-form','reject-registration-form'];
    if(!ids.includes(form.id))return false;
    const data=new FormData(form);
    if(form.id==='issued-login-form'){
      const username=String(data.get('username')).trim().toUpperCase(),password=String(data.get('login-password'));
      const account=state.accounts.find(a=>a.username.toUpperCase()===username);
      if(!account||secrets.get(account.id)!==password){error('auth-error','Mã tài khoản hoặc mật khẩu chưa đúng. Nếu dùng bản demo, hãy điền tài khoản mẫu hoặc cấp lại mật khẩu từ demo quản trị.');return true;}
      if(account.status==='locked'){error('auth-error','Tài khoản đội đang tạm khóa. Hãy liên hệ ban tổ chức.');return true;}
      form.elements['login-password'].value='';
      if(account.status==='temporary'){state.user=null;state.persistUser=false;state.pendingLogin={accountId:account.id,remember:data.has('remember')};persist();location.hash='/change-password';}
      else finishLogin(account,data.has('remember'));
    }
    if(form.id==='first-password-form'){
      const account=state.accounts.find(a=>a.id===state.pendingLogin?.accountId);if(!account){location.hash='/login';return true;}
      const password=String(data.get('new-password')),confirm=String(data.get('confirm-password'));
      if(password!==confirm){error('password-error','Hai mật khẩu chưa trùng khớp. Hãy nhập lại.');return true;}
      if(password.trim().length<8||password===secrets.get(account.id)){error('password-error','Mật khẩu mới cần ít nhất 8 ký tự và khác mật khẩu tạm.');return true;}
      secrets.set(account.id,password);account.status='active';account.delivered=true;form.reset();finishLogin(account,state.pendingLogin.remember);
    }
    if(form.id==='registration-form'){
      const draft=draftFromForm(form);state.registrationDraft=draft;
      const ids=draft.members.map(m=>m.studentId.toLowerCase());
      if(draft.team.length<2||draft.members.some(m=>m.name.length<2)){error('registration-error','Hãy nhập đầy đủ tên đội và họ tên các thành viên.');return true;}
      if(new Set(ids).size!==ids.length){error('registration-error','Mã sinh viên của các thành viên không được trùng nhau.');return true;}
      if(state.registrations.some(r=>r.status!=='rejected'&&(r.team.toLowerCase()===draft.team.toLowerCase()||r.email.toLowerCase()===draft.email.toLowerCase()))||state.accounts.some(a=>a.team.toLowerCase()===draft.team.toLowerCase())){error('registration-error','Tên đội hoặc email đội trưởng đã có hồ sơ. Hãy kiểm tra lại hoặc liên hệ ban tổ chức.');return true;}
      if(state.registrations.some(r=>r.status!=='rejected'&&r.members.some(m=>ids.includes(m.studentId.toLowerCase())))){error('registration-error','Có mã sinh viên đã đăng ký trong một đội khác. Hãy kiểm tra lại.');return true;}
      const request={id:nextId('REG',state.registrations),...draft,status:'pending',date:date()};delete request.confirmed;
      state.registrations.unshift(request);state.registrationReceipt=request.id;persist();render({keepScroll:false});window.scrollTo(0,0);
    }
    if(form.id==='recovery-form'){
      const username=String(data.get('username')).trim().toUpperCase(),email=String(data.get('email')).trim().toLowerCase();
      let request=state.resets.find(r=>r.status==='pending'&&r.username===username&&r.email===email);
      if(!request){request={id:nextId('HELP',state.resets),username,email,date:date(),status:'pending'};state.resets.unshift(request);}
      state.resetReceipt=request.id;persist();render();
    }
    if(form.id==='issue-account-form'){
      if(!admin())return true;
      const username=String(data.get('username')).trim().toUpperCase(),team=String(data.get('team')).trim();
      const request=state.registrations.find(r=>r.id===data.get('requestId'));
      if(team.length<2||String(data.get('captain')).trim().length<2||String(data.get('temporary-password')).trim().length<8){error('issue-error','Hãy nhập tên đội, đội trưởng và mật khẩu tạm có ít nhất 8 ký tự.');return true;}
      if(state.accounts.some(a=>a.username.toUpperCase()===username||a.team.toLowerCase()===team.toLowerCase())){error('issue-error','Mã tài khoản hoặc tên đội đã được cấp. Hãy dùng giá trị khác.');return true;}
      if(request&&request.status!=='pending'){error('issue-error','Hồ sơ này đã được xử lý.');return true;}
      const account={id:nextId('ACC',state.accounts),username,team,captain:String(data.get('captain')).trim(),email:String(data.get('email')).trim().toLowerCase(),status:'temporary',delivered:false,...(request?{requestId:request.id}:{})};
      const password=String(data.get('temporary-password'));secrets.set(account.id,password);state.accounts.unshift(account);if(request)request.status='approved';state.accountTab='accounts';state.accountSearch='';state.accountStatus='all';persist();closeModal();render();showCredential(account,password);
    }
    if(form.id==='reset-account-form'){
      if(!admin())return true;
      const account=state.accounts.find(a=>a.id===data.get('accountId'));if(!account)return true;
      const password=String(data.get('temporary-password'));secrets.set(account.id,password);if(account.status==='locked')account.previousStatus='temporary';else account.status='temporary';account.delivered=false;
      const reset=state.resets.find(r=>r.id===data.get('resetId'));if(reset)reset.status='resolved';persist();closeModal();render();showCredential(account,password);
    }
    if(form.id==='reject-registration-form'){
      if(!admin())return true;const request=state.registrations.find(r=>r.id===data.get('requestId'));if(request){request.status='rejected';request.reason=String(data.get('reason')).trim();persist();closeModal();render();toast('Đã lưu yêu cầu bổ sung trong bản demo.');}
    }
    return true;
  }
  return {actions,submit};
}
