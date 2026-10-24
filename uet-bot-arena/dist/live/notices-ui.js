import {tr,html as l10n} from './i18n.js';
import {esc,head,panel,field,errors,submit,button,date,empty,note} from './view.js';

export function announcementsPage(s){
  const admin=s.user?.role==='admin',items=admin?s.adminAnnouncements||[]:s.announcements||[];
  const form=admin?panel(tr('Đăng thông báo'),l10n`<form id="announcement-form" class="real-form">${field(tr('Tiêu đề'),'title','required minlength="2" maxlength="120"')}<label class="field"><span>Nội dung</span><textarea name="body" required maxlength="3000" rows="5"></textarea></label>${note(tr('Nội dung hiện dưới dạng chữ. Banner mới nhất xuất hiện trên mọi trang trong vòng một phút.'))}${errors()}${submit(tr('Đăng thông báo'))}</form>`):'';
  return head(tr('Thông báo'),tr('Tin từ ban tổ chức.'))+form+(items.length?items.map(n=>panel(n.title,`<p class="muted">${date(n.created_unix_ms)}${n.archived_ms?tr(' · Đã ẩn'):''}</p><p class="notice-body">${esc(n.body)}</p>`,admin?button(n.archived_ms?tr('Hiện lại'):tr('Ẩn thông báo'),'notice-visibility',`data-id="${esc(n.id)}" data-revision="${n.revision}" data-archived="${!!n.archived_ms}"`,'small'):'')).join(''):empty(tr('Chưa có thông báo')));
}
