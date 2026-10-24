import {tr,html as l10n} from './i18n.js';
import {esc,head,panel,table,note,field,errors,submit,button,date,badge,empty} from './view.js';

const platformLabels={windows:'Windows',linux:'Linux'};
const backendLabels={windows:'AppContainer',bwrap:'bubblewrap'};

export function graderStatus(g){
  if(g.revoked_ms)return badge('failed',tr('Đã thu hồi'));
  if(!g.platform)return badge('pending',tr('Chưa kết nối'));
  return g.online?badge('running','Online'):badge('paused','Offline');
}

export function graderRows(items){
  return items.map(g=>`<tr data-grader="${esc(g.id)}"><td><strong>${esc(g.name)}</strong></td><td>${graderStatus(g)}</td><td>${g.platform?esc(platformLabels[g.platform]||g.platform)+' · '+esc(backendLabels[g.backend]||g.backend||''):'—'}</td><td>${g.slots??'—'}</td><td>${g.running??0}</td><td>${g.jobs_done??0}</td><td class="mono" title="${esc(g.runtime_id||'')}">${g.runtime_id?esc(g.runtime_id.slice(7,19)):'—'}</td><td>${esc(g.version||'—')}</td><td>${date(g.last_seen_ms)}</td><td>${g.revoked_ms?'':button(tr('Thu hồi'),'revoke-grader',`data-id="${esc(g.id)}" data-name="${esc(g.name)}"`,'small danger-button')}</td></tr>`);
}

export function gradersPage(s){
  const items=s.graders?.items||[],online=items.filter(g=>g.online),queue=s.graders?.queue||{};
  const slots=online.reduce((n,g)=>n+(g.slots||0),0);
  const stat=(label,value)=>`<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div></div>`;
  const summary=`<div class="admin-kpis">${stat(tr('Máy online'),online.length)}${stat(tr('Slot chấm'),slots)}${stat(tr('Trận chính thức chờ'),queue.official??0)}${stat(tr('Luyện tập, kiểm tra chờ'),queue.practice??0)}</div>`;
  const create=panel(tr('Thêm máy chấm'),`<form class="real-form" id="grader-form">${field(tr('Tên máy (ví dụ: PC phòng 301)'),'name','required maxlength="60"')}${note(tr('Mã máy chấm chỉ hiện một lần sau khi tạo. Dán mã vào cửa sổ cài máy chấm; hệ thống chỉ lưu bản băm.'))}${errors()}${submit(tr('Tạo mã máy chấm'))}</form>`);
  const list=panel(tr('Danh sách máy chấm'),items.length?table([tr('Tên'),tr('Trạng thái'),tr('Nền tảng'),'Slot',tr('Đang chấm'),tr('Đã chấm'),'runtime_id',tr('Phiên bản'),tr('Thấy lần cuối'),''],graderRows(items)):empty(tr('Chưa có máy chấm nào.'),tr('Tạo mã ở trên rồi chạy máy chấm trên máy Windows hoặc Linux.')),button(tr('Cập nhật'),'refresh','','small secondary'));
  const howto=panel(tr('Chạy máy chấm'),l10n`<p>Windows (không cần quyền admin, không cần WSL):</p><pre class="report-json">python -m arena.sandbox.toolchain install
python -m arena.grader --hub ${esc(s.origin||location.origin)} --slots 2 --backend windows</pre><p>Linux hoặc WSL2:</p><pre class="report-json">python3 -m arena.grader --hub ${esc(s.origin||location.origin)} --slots 2 --backend bwrap</pre><p>Lần chạy đầu, máy chấm hỏi mã bằng lời nhắc ẩn rồi lưu vào file chỉ tài khoản đó đọc được. Không dán mã vào dòng lệnh hay đường dẫn.</p>`);
  return head(tr('Máy chấm.'),tr('Máy chấm tự kéo việc về và chạy bot trong sandbox; hub không chạy bot. Trận tính điểm chỉ giao cho máy cùng nền tảng của giải.'))+summary+list+create+howto;
}

export function graderTokenModal(item){
  return l10n`<p>Đây là lần duy nhất mã của <strong>${esc(item.name)}</strong> được hiển thị. Sao chép rồi dán vào máy chấm khi được hỏi.</p><label class="field"><span>Mã máy chấm</span><div class="credential-value" id="grader-token">${esc(item.token)}</div></label><div class="modal-actions">${button(tr('Sao chép mã'),'copy-grader-token','','primary')}${button(tr('Đóng'),'close-modal')}</div>`;
}
