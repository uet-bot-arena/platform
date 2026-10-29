import {tr,html as l10n} from './i18n.js';
import {note,authFrame,field,errors,submit,link,date} from './view.js';

export function emailStatusText(request) {
  if(!request)return '';
  if(request.verified_ms)return tr('Email đã được xác minh.');
  if(request.status==='dropped')return tr('Yêu cầu đã chờ quá 30 phút. Hãy bấm gửi mã mới.');
  if(request.status==='sent')return tr('Mã đã được gửi. Hạn dùng tính từ lúc gửi thư.');
  return request.estimated_sent_ms?tr('Mã sẽ được gửi lúc khoảng ')+date(request.estimated_sent_ms)+'.':tr('Yêu cầu đang chờ gửi.');
}

export function emailVerificationPage(s) {
  const r=s.emailRequest,verified=!!r?.verified_ms,waiting=(r?.resend_after_seconds||0)>0;
  const requestForm=`<form id="email-request-form" class="real-form">${field(tr('Email trường'),'email','required maxlength="120" autocomplete="email"','email',s.emailAddress||'')}<button type="submit" class="btn primary" ${waiting?'disabled':''}>${r?tr('Gửi mã mới'):tr('Gửi mã xác minh')}</button>${waiting?note(tr('Có thể gửi lại sau ')+r.resend_after_seconds+tr(' giây.')):''}${errors()}</form>`;
  const codeForm=r&&r.status!=='dropped'&&!verified?`<form id="email-code-form" class="real-form">${field(tr('Mã 6 số'),'email-code','required pattern="[0-9]{6}" maxlength="6" inputmode="numeric" autocomplete="one-time-code"')}${submit(tr('Xác minh email'))}${errors()}</form>`:'';
  return authFrame(l10n`<h2>Xác minh email</h2><p>Nhận mã qua email trường để xác nhận địa chỉ của bạn.</p><div id="email-state" role="status">${r?note(emailStatusText(r)):''}</div>${verified?'':requestForm+codeForm}${note(tr('Không thấy thư? Xem mục Spam. Mã có hiệu lực 10 phút kể từ lúc gửi.'))}${link('login',tr('Về đăng nhập'),'text-link')}`);
}
