import {eloTeam} from './elo.js';
import {githubPanel} from './github-ui.js';
import {esc,head,panel,table,note,authFrame,field,password,errors,submit,link,button,select,date,empty} from './view.js';
import {emailStatusText} from './email-ui.js';

const form=(id,body,attrs='')=>`<form id="${id}" class="real-form" ${attrs}>${body}${errors()}</form>`;
const roles={student:'Sinh viên',staff:'Cán bộ',guest:'Khách',admin:'BTC'};
export function personalAuthPage(s){
  const reset=s.emailPurpose==='reset'||s.route==='recover',r=s.emailRequest,verified=!!r?.verification_token;
  if(s.passwordFinished)return authFrame(`<h2>${reset?'Đã đặt lại mật khẩu':'Đã tạo tài khoản'}</h2><p>Đăng nhập bằng email trường và mật khẩu vừa đặt.</p>${link('login','Đăng nhập','primary')}`);
  const title=verified?'Đặt mật khẩu':reset?'Quên mật khẩu':'Đăng ký tài khoản';
  const requestForm=form('email-request-form',field('Email trường','email','required maxlength="120" autocomplete="email"','email',s.emailAddress||'')+`<button class="btn primary" type="submit" ${r?.resend_after_seconds?'disabled':''}>${r?'Gửi mã mới':'Nhận mã'}</button>`+(r?.resend_after_seconds?note('Có thể gửi lại sau '+r.resend_after_seconds+' giây.'):''));
  const codeForm=r&&r.status!=='dropped'?form('email-code-form',field('Mã 6 số','email-code','required pattern="[0-9]{6}" maxlength="6" inputmode="numeric" autocomplete="one-time-code"')+submit('Xác minh email')):'';
  const finish=form('personal-password-form',(reset?'':field('Họ và tên','display-name','required minlength="2" maxlength="40" autocomplete="name"'))+password('Mật khẩu mới (8–128 ký tự)','new-password','new-password')+password('Nhập lại mật khẩu','confirm-password','new-password')+submit(reset?'Đặt lại mật khẩu':'Tạo tài khoản'));
  return authFrame(`<div id="people-auth"><h2>${title}</h2><p>${reset?'Nhận mã để đặt lại mật khẩu và đăng xuất mọi phiên cũ.':'Mỗi người một tài khoản. Xác minh email trường trước khi lập hoặc vào đội.'}</p><div id="email-state" role="status">${r?note(emailStatusText(r)):''}</div>${verified?finish:requestForm+codeForm}${note('Không thấy thư? Xem mục Spam. Mã hết hạn 10 phút sau khi gửi.')}${link('login','Về đăng nhập','text-link')}${reset?link('guest-recover','Khách: yêu cầu BTC cấp lại mật khẩu','text-link'):link('guest-register','Không có email trường? Gửi hồ sơ cho BTC','text-link')}</div>`);
}

export function accountTeamPage(s){
  const p=s.personal?.profile||s.user||{},t=s.personal?.team||null,policy=s.personal?.policy||{},captain=t?.captain_id===s.user?.id;
  const profile=form('personal-profile-form',field('Họ và tên','display-name','required maxlength="40"','text',p.display_name||p.username||'')+field('Lớp','class-name','maxlength="60"','text',p.class_name||'')+`<label class="field"><span>Ngôn ngữ bot</span>${select('language',[['cpp17','C++17'],['c11','C11'],['python3','Python']],p.language||'cpp17')}</label>`+field('Giới thiệu','introduction','maxlength="160"','text',p.introduction||'')+field('Liên hệ Facebook / Discord','contact','maxlength="120"','text',p.contact||'')+`<label class="check-field"><input name="looking" type="checkbox" ${p.looking?'checked':''}>Đang tìm đồng đội</label>`+submit('Lưu hồ sơ'));
  let team;
  if(!t)team=panel('Tạo hoặc vào đội',form('personal-team-create',field('Tên đội','team-name','required minlength="2" maxlength="40"')+(p.is_judge?'<label class="check-field"><input type="checkbox" name="guest">Tạo đội khách (không xếp hạng)</label>':'')+submit('Tạo đội'))+form('personal-team-join',field('Mã mời 8 ký tự','invite-code','required minlength="8" maxlength="8" autocomplete="off"','text',s.inviteCode||'')+submit('Vào đội')));
  else{
    const locked=!!t.locked_ms,rows=t.roster||[];
    const controls=captain&&!locked?form('personal-team-name',field('Tên đội','team-name','required maxlength="40"','text',t.name)+submit('Đổi tên'))+button('Đổi mã mời','team-rotate')+button(t.recruiting?'Tắt tuyển người':'Đang tuyển thêm người','team-recruiting')+note(t.invite_code?'Mã mời: '+t.invite_code+' · '+(t.invite_enabled?'Đang mở':'Đã tắt'):'Mã mời chưa được tạo.')+(t.invite_code?`<p class="invite-link"><a href="#/team?invite=${esc(t.invite_code)}">${esc(locationOrigin(s))}/#/team?invite=${esc(t.invite_code)}</a></p>`:''):'';
    const roster=table(['Thành viên','Vai trò',''],rows.map(m=>`<tr><td>${esc(m.display_name||'Thành viên')}</td><td>${m.captain?'Đội trưởng':'Thành viên'}</td><td>${captain&&!locked&&!m.captain?button('Chuyển quyền','team-captain',`data-user="${esc(m.id)}"`,'small')+button('Xóa','team-remove',`data-user="${esc(m.id)}"`,'small danger'):''}</td></tr>`));
    team=panel(t.name,(t.legacy_roster?note('Đội chuyển từ tài khoản cũ; roster '+t.members.length+' người do BTC duyệt vẫn giữ. Khi đổi thành viên, đội chuyển sang roster tài khoản cá nhân.'): '')+note(t.is_guest?'Đội khách · không vào bảng xếp hạng':(t.freshman?'Đội năm nhất · ':'')+rows.length+'/'+t.team_size+' thành viên'+(locked?' · Đã chốt':''))+roster+controls+(!locked?button('Rời đội','team-leave','','danger'):'')+note('Sau hạn lập đội, thay đổi thành viên cần BTC và được ghi nhật ký.')+link('submissions','Bot và phiên bản dự giải','small'));
  }
  return '<div id="people-page">'+head('Cá nhân & đội',policy.team_deadline_ms?'Hạn lập đội: '+date(policy.team_deadline_ms):'Tự lập đội bốn người bằng mã mời.',link('matching','Ghép đội'))+'<div class="real-two-col">'+panel('Hồ sơ cá nhân',note(roles[p.role]||p.role)+profile)+team+'</div>'+(t?githubPanel(s)+eloTeam(s):'')+'</div>';
}
function locationOrigin(s){return s.origin||globalThis.location?.origin||'';}

export function matchingPage(s){
  const m=s.matching||{},size=m.team_size||4;
  return '<div id="people-page">'+head('Ghép đội','Chỉ hiện người và đội đã bật tìm đồng đội; email được giữ riêng.',link('team','Cá nhân & đội'))+panel('Người đang tìm đội',m.people?.length?table(['Tên','Lớp','Ngôn ngữ','Giới thiệu','Liên hệ'],m.people.map(p=>`<tr><td>${esc(p.display_name)}</td><td>${esc(p.class_name)}</td><td>${esc(p.language)}</td><td>${esc(p.introduction)}</td><td>${esc(p.contact)}</td></tr>`)):empty('Chưa có người đang tìm đội'))+panel('Đội đang tuyển',m.teams?.length?table(['Đội','Cần thêm'],m.teams.map(t=>`<tr><td>${esc(t.name)}</td><td>${esc(size-t.count)} người</td></tr>`)):empty('Chưa có đội đang tuyển'))+'</div>';
}

export function peopleAdminPage(s){
  const users=s.people||[],teams=s.teamChoices||[],teamNames=new Map(teams.map(t=>[t.id,t.name])),policy=s.teamPolicy||{},merges=s.teamMerges||{};
  const deadline=form('team-deadline-form',field('Hạn lập đội (giờ máy của bạn)','team-deadline','', 'datetime-local',localDate(policy.team_deadline_ms))+submit('Lưu hạn lập đội')+note('Đội đủ người tự chốt khi đến hạn. Đề xuất ghép người lẻ cần BTC xác nhận trong 48 giờ.'));
  const rows=users.map(p=>`<tr><td>${esc(p.display_name||p.username)}</td><td>${esc(p.email||p.username)}</td><td>${esc(teamNames.get(p.team_id)||'Chưa có đội')}</td><td>${esc(roles[p.role]||p.role)}${p.is_judge?' · Giám khảo':''}</td><td>${p.role!=='admin'?button(p.locked?'Mở khóa':'Khóa','person-lock',`data-user="${esc(p.id)}" data-locked="${!p.locked}"`,'small'):''}${p.role==='staff'?button(p.is_judge?'Thu hồi Giám khảo':'Nâng Giám khảo','person-judge',`data-user="${esc(p.id)}" data-enabled="${!p.is_judge}"`,'small'):''}</td></tr>`);
  const proposals=(merges.items||[]).map(p=>form('team-merge-'+p.id,`<p>${p.team_id?'Bổ sung vào '+esc(p.team_name):'Lập đội mới'}: ${p.people.map(x=>esc(x.display_name)).join(', ')}</p>`+(p.team_id?'':field('Tên đội mới','team-name','required maxlength="40"'))+`<button class="btn primary" type="submit">Xác nhận ghép</button>`,`data-proposal="${esc(p.id)}"`)).join('');
  const manual=form('admin-membership-form',`<label class="field"><span>Đội</span>${select('team-id',teams.map(t=>[t.id,t.name]))}</label>`+`<label class="field"><span>Người dùng</span>${select('user-id',users.filter(u=>u.role!=='admin').map(u=>[u.id,u.display_name||u.username]))}</label><label class="field"><span>Thao tác</span>${select('member-action',[['add','Thêm vào đội'],['remove','Xóa khỏi đội'],['captain','Chuyển quyền đội trưởng']])}</label>`+submit('Áp dụng và ghi nhật ký'));
  return '<div id="people-admin">'+head('Người dùng & lập đội','Quản lý vai Giám khảo, hạn lập đội và thay đổi thành viên.')+panel('Người dùng',table(['Tên','Email / username','Đội','Vai trò','Thao tác'],rows))+panel('Hạn lập đội',deadline)+panel('Đề xuất ghép người lẻ',proposals||empty('Chưa có đề xuất đủ bốn người'))+(merges.remainder?.length?note('Còn '+merges.remainder.length+' người cần BTC xử lý tay.'):'')+panel('Chỉnh thành viên đội',teams.length?table(['Đội','Thành viên'],teams.map(t=>`<tr><td>${esc(t.name)}</td><td>${(t.roster||[]).map(p=>esc(p.display_name)+(p.captain?' (đội trưởng)':'')).join(', ')}</td></tr>`))+manual:empty('Chưa có đội'))+'</div>';
}
function localDate(value){if(!value)return '';const d=new Date(value);return new Date(value-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
