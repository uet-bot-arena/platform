import {tr,html as l10n} from './i18n.js';
import {esc,head,panel,table,note,authFrame,field,errors,submit,link,date} from './view.js';

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

export function emailPage(s) {
  const m=s.mail||{},c=m.counts||{},providers=m.providers||[];
  return '<div id="email-admin">'+head(tr('Gửi email'),tr('Hàng đợi, lượt gửi lại và hạn mức của từng dịch vụ.'))+
    panel(tr('Hàng đợi'),table([tr('Đang chờ'),tr('Đã gửi'),tr('Chờ thử lại'),tr('Đã bỏ quá hạn'),tr('Có nhiều lượt gửi')],[`<tr>${[c.queued,c.sent,c.failed,c.dropped,m.retried].map(v=>`<td>${esc(v??0)}</td>`).join('')}</tr>`]))+
    providers.filter(p=>p.low_quota).map(p=>note(tr('Dịch vụ ')+p.name+tr(' còn dưới 20% hạn mức.'))).join('')+
    panel(tr('Dịch vụ gửi thư'),table([tr('Dịch vụ'),tr('Sẵn sàng'),tr('Đã gửi hôm nay'),tr('Lỗi hôm nay'),tr('Còn trong giờ'),tr('Còn trong ngày')],providers.map(p=>`<tr><th scope="row">${esc(p.name)}</th><td>${p.ready?tr('Đã cấu hình'):tr('Thiếu cấu hình')}</td><td>${esc(p.sent)}</td><td>${esc(p.failed)}</td><td>${esc(p.hourly_remaining)} / ${esc(p.hourly_limit)}</td><td>${esc(p.daily_remaining)} / ${esc(p.daily_limit)}</td></tr>`))+note(tr('Hạn mức tính cả lượt thử SMTP; mốc giờ và ngày theo UTC.')))+'</div>';
}
