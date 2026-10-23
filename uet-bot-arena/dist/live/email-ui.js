import {esc,head,panel,table,note,authFrame,field,errors,submit,link,date} from './view.js';

export function emailStatusText(request) {
  if(!request)return '';
  if(request.verified_ms)return 'Email đã được xác minh.';
  if(request.status==='dropped')return 'Yêu cầu đã chờ quá 30 phút. Hãy bấm gửi mã mới.';
  if(request.status==='sent')return 'Mã đã được gửi. Hạn dùng tính từ lúc gửi thư.';
  return request.estimated_sent_ms?'Mã sẽ được gửi lúc khoảng '+date(request.estimated_sent_ms)+'.':'Yêu cầu đang chờ gửi.';
}

export function emailVerificationPage(s) {
  const r=s.emailRequest,verified=!!r?.verified_ms,waiting=(r?.resend_after_seconds||0)>0;
  const requestForm=`<form id="email-request-form" class="real-form">${field('Email trường','email','required maxlength="120" autocomplete="email"','email',s.emailAddress||'')}<button type="submit" class="btn primary" ${waiting?'disabled':''}>${r?'Gửi mã mới':'Gửi mã xác minh'}</button>${waiting?note('Có thể gửi lại sau '+r.resend_after_seconds+' giây.'):''}${errors()}</form>`;
  const codeForm=r&&r.status!=='dropped'&&!verified?`<form id="email-code-form" class="real-form">${field('Mã 6 số','email-code','required pattern="[0-9]{6}" maxlength="6" inputmode="numeric" autocomplete="one-time-code"')}${submit('Xác minh email')}${errors()}</form>`:'';
  return authFrame(`<h2>Xác minh email</h2><p>Nhận mã qua email trường để xác nhận địa chỉ của bạn.</p><div id="email-state" role="status">${r?note(emailStatusText(r)):''}</div>${verified?'':requestForm+codeForm}${note('Không thấy thư? Xem mục Spam. Mã có hiệu lực 10 phút kể từ lúc gửi.')}${link('login','Về đăng nhập','text-link')}`);
}

export function emailPage(s) {
  const m=s.mail||{},c=m.counts||{},providers=m.providers||[];
  return '<div id="email-admin">'+head('Gửi email','Hàng đợi, lượt gửi lại và hạn mức của từng dịch vụ.')+
    panel('Hàng đợi',table(['Đang chờ','Đã gửi','Chờ thử lại','Đã bỏ quá hạn','Có nhiều lượt gửi'],[`<tr>${[c.queued,c.sent,c.failed,c.dropped,m.retried].map(v=>`<td>${esc(v??0)}</td>`).join('')}</tr>`]))+
    providers.filter(p=>p.low_quota).map(p=>note('Dịch vụ '+p.name+' còn dưới 20% hạn mức.')).join('')+
    panel('Dịch vụ gửi thư',table(['Dịch vụ','Sẵn sàng','Đã gửi hôm nay','Lỗi hôm nay','Còn trong giờ','Còn trong ngày'],providers.map(p=>`<tr><th scope="row">${esc(p.name)}</th><td>${p.ready?'Đã cấu hình':'Thiếu cấu hình'}</td><td>${esc(p.sent)}</td><td>${esc(p.failed)}</td><td>${esc(p.hourly_remaining)} / ${esc(p.hourly_limit)}</td><td>${esc(p.daily_remaining)} / ${esc(p.daily_limit)}</td></tr>`))+note('Hạn mức tính cả lượt thử SMTP; mốc giờ và ngày theo UTC.'))+'</div>';
}
