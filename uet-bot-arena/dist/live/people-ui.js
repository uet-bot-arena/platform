import {matchingPeople,availableTeams,teamSearchText} from './team-search.js';
import {vnDeadlineField} from './vn-deadline.js';
import {competitionFilter,participationLabel,teamCompetitions} from './competition-ui.js';
import {tr,html as l10n} from './i18n.js';
import {eloTeam} from './elo.js';
import {githubPanel} from './github-ui.js';
import {esc,head,panel,table,note,authFrame,field,password,errors,submit,link,button,select,date,empty} from './view.js';
import {emailStatusText} from './email-ui.js';

const form=(id,body,attrs='')=>`<form id="${id}" class="real-form" ${attrs}>${body}${errors()}</form>`;
const roles={student:tr('Sinh viên'),staff:tr('Cán bộ'),guest:tr('Khách'),admin:tr('BTC')};
export function personalAuthPage(s){
  const reset=s.emailPurpose==='reset'||s.route==='recover',r=s.emailRequest,verified=!!r?.verification_token;
  if(s.passwordFinished)return authFrame(l10n`<h2>${reset?tr('Đã đặt lại mật khẩu'):tr('Đã tạo tài khoản')}</h2><p>Đăng nhập bằng email trường và mật khẩu vừa đặt.</p>${link('login',tr('Đăng nhập'),'primary')}`);
  const title=verified?tr('Đặt mật khẩu'):reset?tr('Quên mật khẩu'):tr('Đăng ký tài khoản');
  // Once a code is on its way, hide the email box: show where it went, the code box first, then resend / change email.
  const sent=!!r&&r.status!=='dropped';
  const requestForm=sent
    ?form('email-request-form',`<input type="hidden" name="email" value="${esc(s.emailAddress||'')}"><p class="email-sent-to">${tr('Mã gửi tới ')}<strong>${esc(s.emailAddress||'')}</strong></p><div class="email-sent-actions"><button class="btn small secondary" type="submit" ${r.resend_after_seconds?'disabled':''}>${r.resend_after_seconds?tr('Gửi lại mã sau ')+r.resend_after_seconds+tr(' giây'):tr('Gửi lại mã')}</button>${button(tr('Đổi email'),'email-change','','small secondary')}</div>`)
    :form('email-request-form',field(tr('Email trường'),'email','required maxlength="120" autocomplete="email"','email',s.emailAddress||'')+`<button class="btn primary" type="submit" ${r?.resend_after_seconds?'disabled':''}>${r?tr('Gửi mã mới'):tr('Nhận mã')}</button>`+(r?.resend_after_seconds?note(tr('Có thể gửi lại sau ')+r.resend_after_seconds+tr(' giây.')):''));
  const codeForm=sent?form('email-code-form',field(tr('Mã 6 số'),'email-code','required pattern="[0-9]{6}" maxlength="6" inputmode="numeric" autocomplete="one-time-code"')+submit(tr('Xác minh email'))):'';
  const finish=form('personal-password-form',(reset?'':field(tr('Họ và tên'),'display-name','required minlength="2" maxlength="40" autocomplete="name"'))+password(tr('Mật khẩu mới (8–128 ký tự)'),'new-password','new-password')+password(tr('Nhập lại mật khẩu'),'confirm-password','new-password')+submit(reset?tr('Đặt lại mật khẩu'):tr('Tạo tài khoản')));
  return authFrame(`<div id="people-auth"><h2>${title}</h2><p>${reset?tr('Nhận mã để đặt lại mật khẩu và đăng xuất mọi phiên cũ.'):tr('Mỗi người một tài khoản. Xác minh email trường trước khi lập hoặc vào đội.')}</p><div id="email-state" role="status">${r?note(emailStatusText(r)):''}</div>${verified?finish:sent?codeForm+requestForm:requestForm}${note(tr('Không thấy thư? Xem mục Spam. Mã hết hạn 10 phút sau khi gửi.'))}<div class="table-actions people-auth-links">${link('login',tr('Về đăng nhập'),'text-link')}${reset?link('guest-recover',tr('Khách: yêu cầu BTC cấp lại mật khẩu'),'text-link'):link('guest-register',tr('Không có email trường? Gửi hồ sơ cho BTC'),'text-link')}</div></div>`);
}

export function accountTeamPage(s){
  const p=s.personal?.profile||s.user||{},t=s.personal?.team||null,policy=s.personal?.policy||{},captain=t?.captain_id===s.user?.id;
  const profile=form('personal-profile-form',field(tr('Họ và tên'),'display-name','required maxlength="40"','text',p.display_name||p.username||'')+field(tr('Lớp'),'class-name','maxlength="60"','text',p.class_name||'')+l10n`<label class="field"><span>Ngôn ngữ bot</span>${select('language',[['cpp17','C++17'],['c11','C11'],['python3','Python']],p.language||'cpp17')}</label>`+field(tr('Giới thiệu'),'introduction','maxlength="160"','text',p.introduction||'')+field(tr('Liên hệ Facebook / Discord'),'contact','maxlength="120"','text',p.contact||'')+l10n`<label class="check-field"><input name="looking" type="checkbox" ${p.looking?'checked':''}>Đang tìm đồng đội</label>`+note(tr('Hiện ở trang Ghép đội: tên, lớp, ngôn ngữ, giới thiệu, liên hệ; email giữ riêng.'))+submit(tr('Lưu hồ sơ')));
  let team;
  if(!t)team=panel(tr('Tạo hoặc vào đội'),form('personal-team-create',field(tr('Tên đội'),'team-name','required minlength="2" maxlength="40"')+(p.is_judge?tr('<label class="check-field"><input type="checkbox" name="guest">Tạo đội khách (không xếp hạng)</label>'):'')+submit(tr('Tạo đội')))+form('personal-team-join',field(tr('Mã mời 8 ký tự'),'invite-code','required minlength="8" maxlength="8" autocomplete="off"','text',s.inviteCode||'')+submit(tr('Vào đội'))));
  else{
    const locked=!!t.locked_ms,rows=t.roster||[];
    const controls=captain&&!locked?form('personal-team-name',field(tr('Tên đội'),'team-name','required maxlength="40"','text',t.name)+submit(tr('Đổi tên')))+button(tr('Đổi mã mời'),'team-rotate')+button(t.recruiting?tr('Tắt tuyển người'):tr('Đang tuyển thêm người'),'team-recruiting')+note(tr('Hiện ở trang Ghép đội: tên, lớp, ngôn ngữ, giới thiệu, liên hệ; email giữ riêng.'))+(t.recruiting&&!p.contact?note(tr('Hãy điền liên hệ Facebook / Discord trong hồ sơ để người tìm đội liên hệ đội trưởng.')):'')+note(t.invite_code?tr('Mã mời: ')+t.invite_code+' · '+(t.invite_enabled?tr('Đang mở'):tr('Đã tắt')):tr('Mã mời chưa được tạo.'))+(t.invite_code?`<p class="invite-link"><a href="#/team?invite=${esc(t.invite_code)}">${esc(locationOrigin(s))}/#/team?invite=${esc(t.invite_code)}</a></p>`:''):'';
    const roster=table([tr('Thành viên'),tr('Vai trò'),''],rows.map(m=>`<tr><td>${esc(m.display_name||tr('Thành viên'))}</td><td>${m.captain?tr('Đội trưởng'):tr('Thành viên')}</td><td>${captain&&!locked&&!m.captain?button(tr('Chuyển quyền'),'team-captain',`data-user="${esc(m.id)}"`,'small')+button(tr('Xóa'),'team-remove',`data-user="${esc(m.id)}"`,'small danger'):''}</td></tr>`));
    team=panel(t.name,((t.member_count??rows.length)>rows.length?note(tr('Số thành viên theo hồ sơ BTC đã duyệt.')):'')+note(t.is_guest?tr('Đội khách · không vào bảng xếp hạng'):(t.freshman?tr('Đội năm nhất · '):'')+(t.member_count??rows.length)+'/'+t.team_size+tr(' thành viên')+(locked?tr(' · Đã chốt'):''))+roster+controls+(!locked?button(tr('Rời đội'),'team-leave','','danger'):'')+note(tr('Sau hạn lập đội, thay đổi thành viên cần BTC và được ghi nhật ký.'))+link('submissions',tr('Bot và phiên bản dự giải'),'small'));
  }
  return '<div id="people-page">'+head(tr('Cá nhân & đội'),policy.team_deadline_ms?tr('Hạn lập đội: ')+date(policy.team_deadline_ms):tr('Tự lập đội bốn người bằng mã mời.'),link('matching',tr('Ghép đội')))+'<div class="real-two-col">'+panel(tr('Hồ sơ cá nhân'),note(roles[p.role]||p.role)+profile)+team+'</div>'+teamCompetitions(s)+(t?githubPanel(s)+eloTeam(s):'')+'</div>';
}
function locationOrigin(s){return s.origin||globalThis.location?.origin||'';}

export function matchingPage(s){
  const m=s.matching||{},size=m.team_size||4;
  return '<div id="people-page">'+head(tr('Ghép đội'),tr('Chỉ hiện người và đội đã bật tìm đồng đội; email được giữ riêng.'),link('team',tr('Cá nhân & đội')))+panel(tr('Người đang tìm đội'),m.people?.length?table([tr('Tên'),tr('Lớp'),tr('Ngôn ngữ'),tr('Giới thiệu'),tr('Liên hệ')],m.people.map(p=>`<tr><td>${esc(p.display_name)}</td><td>${esc(p.class_name)}</td><td>${esc(p.language)}</td><td>${esc(p.introduction)}</td><td>${esc(p.contact)}</td></tr>`)):empty(tr('Chưa có người đang tìm đội')))+panel(tr('Đội đang tuyển'),m.teams?.length?table([tr('Đội'),tr('Đội trưởng'),tr('Ngôn ngữ'),tr('Liên hệ'),tr('Cần thêm')],m.teams.map(t=>l10n`<tr><td>${esc(t.name)}</td><td>${esc(t.captain_name||'—')}</td><td>${esc(t.language||'—')}</td><td>${esc(t.contact||tr('Chưa để liên hệ'))}</td><td>${esc(size-t.count)} người</td></tr>`)):empty(tr('Chưa có đội đang tuyển')))+'</div>';
}

export function peopleAdminPage(s){
  const query=(s.peopleSearch||'').toLocaleLowerCase('vi'),teams=(s.teamChoices||[]).filter(t=>teamSearchText(t,s.people).includes(query)),users=matchingPeople(s),teamNames=new Map((s.teamChoices||[]).map(t=>[t.id,t.name])),policy=s.teamPolicy||{},merges=s.teamMerges||{};
  const deadline=form('team-deadline-form',vnDeadlineField('team-deadline',policy.team_deadline_ms)+submit(tr('Lưu hạn lập đội'))+note(tr('Đội đủ người tự chốt khi đến hạn. Đề xuất ghép người lẻ cần BTC xác nhận trong 48 giờ.')));
  const rows=users.slice(0,50).map(p=>`<tr><td>${esc(p.display_name||p.username)}</td><td>${esc(p.email||p.username)}</td><td>${esc(teamNames.get(p.team_id)||tr('Chưa có đội'))}</td><td>${esc(roles[p.role]||p.role)}${p.is_judge?tr(' · Giám khảo'):''}</td>${s.selectedCompetition?`<td>${esc(participationLabel(p.participation))}</td>`:''}<td>${p.role!=='admin'?personActions(s,p):''}${p.role!=='admin'?button(p.locked?tr('Mở khóa'):tr('Khóa'),'person-lock',`data-user="${esc(p.id)}" data-locked="${!p.locked}"`,'small'):''}${p.role==='staff'?button(p.is_judge?tr('Thu hồi Giám khảo'):tr('Nâng Giám khảo'),'person-judge',`data-user="${esc(p.id)}" data-enabled="${!p.is_judge}"`,'small'):''}</td></tr>`);
  const proposals=(merges.items||[]).map(p=>form('team-merge-'+p.id,`<p>${p.team_id?tr('Bổ sung vào ')+esc(p.team_name):tr('Lập đội mới')}: ${p.people.map(x=>esc(x.display_name)).join(', ')}</p>`+(p.team_id?'':field(tr('Tên đội mới'),'team-name','required maxlength="40"'))+l10n`<button class="btn primary" type="submit">Xác nhận ghép</button>`,`data-proposal="${esc(p.id)}"`)).join('');
  return '<div id="people-admin">'+head(tr('Người dùng & lập đội'),tr('Quản lý vai Giám khảo, hạn lập đội và thay đổi thành viên.'))+`<div class="admin-filters people-filters">${competitionFilter(s)}${field(tr('Tìm theo tên, email, MSSV hoặc đội'),'people-search','','search',s.peopleSearch||'')+`<label class="field"><span>${tr('Đội')}</span>${select('people-team-filter',[['all',tr('Tất cả')],['without',tr('Chưa có đội')],['with',tr('Có đội')]],s.peopleTeamFilter||'all')}</label>`}</div>`+panel(tr('Người dùng'),(!query?note(l10n`Gõ tên, email hoặc MSSV để tìm trong ${(s.people||[]).length} người`):'')+(users.length>50?note(l10n`Còn ${users.length-50} người, gõ thêm để thu hẹp`):'')+table([tr('Tên'),'Email / username',tr('Đội'),tr('Vai trò'),...(s.selectedCompetition?[tr('Trong giải')]:[]),tr('Thao tác')],rows))+panel(tr('Hạn lập đội'),deadline)+panel(tr('Đề xuất ghép người lẻ'),proposals||empty(tr('Chưa có đề xuất đủ bốn người')))+(merges.remainder?.length?note(tr('Còn ')+merges.remainder.length+tr(' người cần BTC xử lý tay.')):'')+panel(tr('Chỉnh thành viên đội'),teams.length?table([tr('Đội'),tr('Thành viên'),...(s.selectedCompetition?[tr('Trong giải')]:[])],teams.map(t=>`<tr><td>${esc(t.name)}</td><td>${(t.roster||[]).map(p=>esc(p.display_name)+(p.captain?tr(' (đội trưởng)'):'')).join(', ')}</td>${s.selectedCompetition?`<td>${esc(participationLabel(t.participation))}</td>`:''}</tr>`)):empty(tr('Chưa có đội')))+'</div>';
}

function personActions(s,p){
 const attrs=`data-user="${esc(p.id)}"`;
 if(!p.team_id)return button(tr('Thêm vào đội'),'person-add',attrs,'small')+button(tr('Lập đội mới'),'person-create-team',attrs,'small');
 const team=(s.teamChoices||[]).find(t=>t.id===p.team_id);if(!team||team.archived_ms)return '';const captain=team.roster?.some(m=>m.id===p.id&&m.captain);
 return (!captain?button(tr('Làm đội trưởng'),'person-captain',attrs,'small'):'')+button(tr('Xóa khỏi đội'),'person-remove',attrs,'small danger');
}
export function teamChoiceButtons(s,query='',selected=''){
 const teams=availableTeams(s,query);
 return teams.length?teams.map(t=>`<button type="button" class="btn secondary" data-action="person-team-choice" data-team="${esc(t.id)}" aria-pressed="${t.id===selected}">${esc(t.name)} · ${t.member_count??t.roster?.length??0}/${t.team_size??4} ${tr('người')} · ${tr('đội trưởng')} ${esc(t.roster?.find(p=>p.captain)?.display_name||'—')}</button>`).join(''):note(tr('Không có đội còn chỗ khớp tìm kiếm.'));
}
