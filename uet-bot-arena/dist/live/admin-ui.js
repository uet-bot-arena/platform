import {esc,icon,button,link,head,empty,badge,date,short,metric,select,panel,note,table,statusLabel} from './view.js';
const kpis=items=>`<div class="admin-kpis">${items.map(([label,value,ico,foot=''])=>`<article class="stat"><p class="stat-label">${esc(label)}</p><div class="stat-value">${esc(value??'—')}</div><p class="stat-foot">${esc(foot)}</p></article>`).join('')}</div>`;
const tabs=(items,active,action)=>`<div class="tabs">${items.map(([value,label])=>button(label,action,`data-tab="${value}"`,active===value?'primary':'secondary')).join('')}</div>`;
const filtered=(items,query,values)=>items.filter(x=>values(x).toLocaleLowerCase('vi').includes((query||'').toLocaleLowerCase('vi')));
const paged=(items,n=1)=>{const pages=Math.max(1,Math.ceil(items.length/20)),page=Math.min(Math.max(n,1),pages);return{items:items.slice((page-1)*20,page*20),page,pages,total:items.length};};
const pagination=(p,scope)=>`<div class="admin-pagination"><span>${p.total} mục · Trang ${p.page}/${p.pages}</span><div>${button('Trước','admin-page',`data-scope="${scope}" data-page="${p.page-1}" ${p.page===1?'disabled':''}`,'small')}${button('Tiếp','admin-page',`data-scope="${scope}" data-page="${p.page+1}" ${p.page===p.pages?'disabled':''}`,'small')}</div></div>`;
export const teamLabel=(s,id)=>s.accounts?.find(a=>a.team_id===id)?.team.name||(id?.startsWith('competition:')?'Giải · '+short(id.slice(12)):short(id));
export function competitionTools(c){
  const attrs=`data-id="${c.id}" data-revision="${c.revision||1}"`;
  if(c.archived_ms)return button('Khôi phục','competition-restore',attrs,'small');
  return (['draft','open'].includes(c.status)?button('Sửa','competition-edit',attrs,'small'):'')+(['draft','open','completed'].includes(c.status)?button('Xóa','competition-archive',attrs,'small danger-button'):'');
}

function trend(days){
  const max=Math.max(1,...days.map(d=>d.jobs));
  return `<svg class="admin-trend" viewBox="0 0 700 210" role="img" aria-label="Số tác vụ tạo mỗi ngày trong 7 ngày gần nhất; cột xanh là tổng tác vụ, cột đỏ là tác vụ lỗi"><path class="trend-axis" d="M20 170H680"/>${days.map((d,i)=>{const x=35+i*95,h=130*d.jobs/max,bad=130*d.failed/max;return `<g><title>${esc(d.day)}: ${d.jobs} tác vụ, ${d.failed} lỗi</title><rect class="trend-total" x="${x}" y="${170-h}" width="30" height="${h}" rx="3"/><rect class="trend-failed" x="${x+34}" y="${170-bad}" width="16" height="${bad}" rx="2"/><text x="${x+25}" y="${Math.max(22,160-h)}" text-anchor="middle">${d.jobs}</text><text x="${x+25}" y="195" text-anchor="middle">${esc(d.day.slice(5))}</text></g>`;}).join('')}</svg><div class="trend-legend"><span>Tổng tác vụ</span><span>Tác vụ lỗi</span><small>Ngày theo Asia/Bangkok · Không phải số lượt bot</small></div>`;
}
export function adminDashboard(s){
  const m=s.metrics;if(!m)return head('Tổng quan ban tổ chức','Theo dõi đội thi, giải đấu và worker.')+empty('Đang tải số liệu…');
  const c=m.counts,j=m.jobs.by_status,w=m.worker,ready=w.ready&&!w.scheduler_error;
  const active=Object.entries(j).filter(([k])=>!['completed','failed','aborted'].includes(k)).reduce((n,[,v])=>n+v,0);
  return head('Tổng quan ban tổ chức','Đội thi, giải đấu và tình trạng vận hành.',link('admin','Quản lý đội','primary'))+
    `<div class="admin-status-strip">${badge(ready?'completed':'attention',ready?'Worker sẵn sàng':'Worker cần kiểm tra')}<span>Hàng đợi ${w.queue_depth}/${w.queue_capacity}</span><span>Ưu tiên giải · ${w.queue_by_scope?.official||0} chính thức / ${w.queue_by_scope?.practice||0} luyện tập chờ</span><span>Đã chạy ${Math.floor(w.uptime_seconds/60)} phút</span><span>Cập nhật ${date(m.generated_ms)}</span></div>`+
    kpis([['Đội đang quản lý',c.teams,'users',c.archived_teams+' đội đã lưu trữ'],['Hồ sơ chờ duyệt',c.pending_registrations,'file',c.pending_recoveries+' yêu cầu cấp lại mật khẩu'],['Giải đấu',c.competitions,'trophy',c.attention_fixtures+' trận cần xử lý'],['Tác vụ đang chạy',active,'clock',j.queued+' tác vụ đang chờ'],['Benchmark đạt · 24h',m.last_24h.benchmark_pass_rate===null?'—':m.last_24h.benchmark_pass_rate+'%','check','Tính cả benchmark lỗi / đã dừng'],['P95 cao nhất · 24h',metric(m.last_24h.benchmark_p95_max_ms),'code','Trong các benchmark hoàn tất']])+
    `<div class="admin-alerts">${c.pending_registrations?link('admin?tab=registrations',c.pending_registrations+' hồ sơ chờ duyệt','small'):''}${c.pending_recoveries?link('admin?tab=recoveries',c.pending_recoveries+' yêu cầu cấp lại','small'):''}${c.attention_fixtures?link('schedule',c.attention_fixtures+' trận cần kiểm tra','small'):''}${!ready?note(w.scheduler_error||w.detail||'Worker chưa sẵn sàng; kiểm tra Docker và image worker.') :''}</div>`+
    `<div class="real-two-col">${panel('Hoạt động 7 ngày',trend(m.daily))}${panel('Hàng đợi & worker',`<dl class="detail-list"><div><dt>Runtime</dt><dd>C++17 · 1 CPU / bot</dd></div><div><dt>Scheduler</dt><dd>${esc(w.scheduler_error||'Hoạt động')}</dd></div><div><dt>Tác vụ hiện tại</dt><dd class="mono">${esc(short(w.active_job_id)||'Đang rảnh')}</dd></div><div><dt>Worker image</dt><dd class="mono">${esc(w.image_id?short(w.image_id.slice(7)):'Chưa có')}</dd></div></dl>${Object.entries(j).map(([status,count])=>`<div class="admin-job-count"><span>${badge(status)}</span><meter min="0" max="${Math.max(1,m.jobs.total)}" value="${count}" aria-label="${esc(status)}: ${count}"></meter><strong>${count}</strong></div>`).join('')}`,link('operations','Mở vận hành bot','small'))}</div>`+
    panel('Lỗi gần đây',m.errors.length?table(['Mã lỗi','Số tác vụ'],m.errors.map(e=>`<tr data-key="error-${esc(e.code)}"><td class="mono">${esc(e.code)}</td><td>${e.count}</td></tr>`)):empty('Chưa có tác vụ lỗi'),link('operations?status=failed','Xem tác vụ lỗi','small'));
}

export function adminTeamsBody(s){
  let items=s.adminTab==='accounts'?s.accounts:s.adminTab==='recoveries'?s.recoveries:s.registrations;
  if(s.adminTab==='accounts')items=items.filter(a=>s.adminVisibility==='all'||(s.adminVisibility==='deleted'?!!a.team.archived_ms:!a.team.archived_ms));
  items=filtered(items,s.adminSearch,x=>s.adminTab==='accounts'?[x.username,x.team.name,x.team.email,...x.team.members.map(m=>m.student_id)].join(' '):[x.team,x.username,x.email].join(' '));
  const p=paged(items,s.adminPage);let body;
  if(s.adminTab==='accounts')body=table(['Đội / tài khoản','Liên hệ','Trạng thái','Thao tác'],p.items.map(a=>`<tr data-key="account-${a.id}"><td><strong>${esc(a.team.name)}</strong><div class="mono muted">${esc(a.username)}</div><small>${a.team.members.length} thành viên</small></td><td>${esc(a.team.email)}<div class="muted">${esc(a.team.phone||'—')}</div></td><td>${badge(a.team.archived_ms?'aborted':a.locked?'paused':a.must_change_password?'pending':'completed',a.team.archived_ms?'Đã lưu trữ':a.locked?'Tạm khóa':a.must_change_password?'Chờ đổi mật khẩu':'Hoạt động')}<div class="muted">${a.delivered?'Đã phát tài khoản':'Chưa phát tài khoản'}</div></td><td><div class="table-actions">${button('Xem','team-detail',`data-id="${a.id}"`,'small')}${a.team.archived_ms?button('Khôi phục','team-restore',`data-id="${a.id}"`,'small'):button('Sửa','team-edit',`data-id="${a.id}"`,'small')+button(a.locked?'Mở khóa':'Khóa','account-lock',`data-id="${a.id}" data-locked="${a.locked}"`,'small')+button('Cấp lại mật khẩu','reset-password',`data-id="${a.id}"`,'small')+(!a.delivered?button('Đã phát','delivered',`data-id="${a.id}"`,'small'):'')+button('Xóa','team-archive',`data-id="${a.id}"`,'small danger-button')}</div></td></tr>`));
  else if(s.adminTab==='recoveries')body=table(['Tài khoản','Đội / email','Gửi lúc','Thao tác'],p.items.map(r=>`<tr data-key="recovery-${r.id}"><td>${esc(r.username)}</td><td>${esc(r.team)}<div class="muted">${esc(r.email)}</div></td><td>${date(r.created_ms)}</td><td>${button('Cấp lại mật khẩu','reset-password',`data-id="${r.account_id}"`,'small')}${button('Đã xử lý','resolve-recovery',`data-id="${r.id}"`,'small')}</td></tr>`));
  else body=table(['Đội','Email','Thành viên','Gửi lúc','Thao tác'],p.items.map(r=>`<tr data-key="registration-${r.id}"><td><strong>${esc(r.team)}</strong>${badge(r.status)}</td><td>${esc(r.email)}</td><td>${r.members.length}</td><td>${date(r.created_ms)}</td><td>${button(r.status==='pending'?'Xem & duyệt':'Xem hồ sơ','review-registration',`data-id="${r.id}"`,'small primary')}</td></tr>`));
  return (items.length?body:empty('Không có kết quả','Thử đổi bộ lọc hoặc từ khóa.'))+pagination(p,'teams');
}
export function adminTeams(s){
  const active=s.adminTab||'accounts';
  return head('Đội & tài khoản.','Tạo đội, duyệt hồ sơ, sửa thông tin và quản lý truy cập.',button('Thêm đội','team-create','','primary')+button('Xuất CSV','export-teams')+button('Làm mới','refresh'))+
    tabs([['accounts','Đội & tài khoản'],['registrations','Hồ sơ đăng ký'],['recoveries','Cấp lại mật khẩu']],active,'admin-tab')+
    `<div class="admin-filters"><label class="field"><span>Tìm kiếm</span><input class="input" name="admin-search" value="${esc(s.adminSearch||'')}" placeholder="Tên đội, tài khoản, email…" type="search"></label>${active==='accounts'?`<label class="field"><span>Hiển thị</span>${select('admin-visibility',[['active','Đang quản lý'],['deleted','Đã lưu trữ'],['all','Tất cả']],s.adminVisibility||'active',false)}</label>`:active==='registrations'?`<label class="field"><span>Hồ sơ</span>${select('registration-status',[['pending','Chờ duyệt'],['approved','Đã duyệt'],['rejected','Đã từ chối']],s.registrationStatus||'pending',false)}</label>`:''}</div>`+
    panel('Danh sách',`<div id="admin-table">${adminTeamsBody(s)}</div>`)+note('Xóa đội có thể khôi phục. Lịch sử bot, tác vụ và kết quả vẫn được giữ. Cần xử lý bot dự giải của đội trước khi xóa nếu giải chưa hoàn tất.');
}

export function operationsBody(s){
  const submissions=s.opsTab==='submissions';
  let items=submissions?s.adminSubmissions:s.adminJobs;
  if(!submissions)items=items.filter(j=>(!s.opsStatus||j.status===s.opsStatus)&&(!s.opsKind||j.kind===s.opsKind));
  items=items.filter(j=>!s.opsTeam||j.team_id===s.opsTeam);
  items=filtered(items,s.opsSearch,j=>[j.id,j.version,teamLabel(s,j.team_id)].join(' '));
  const p=paged(items,s.opsPage);
  const body=submissions?table(['Phiên bản','Đội','Ngôn ngữ','Biên dịch','Nộp lúc',''],p.items.map(j=>`<tr data-key="submission-${j.id}"><td><strong>${esc(j.version)}</strong><div class="mono muted">${short(j.id)}</div></td><td>${esc(teamLabel(s,j.team_id))}</td><td>C++17</td><td>${badge(j.artifact?'completed':'pending',j.artifact?'Đã build':'Chưa build')}</td><td>${date(j.created_unix_ms)}</td><td>${button('Manifest','submission-manifest',`data-id="${j.id}"`,'small')}</td></tr>`)):
    table(['Tác vụ','Đội / giải','Loại','Trạng thái','Tạo lúc',''],p.items.map(j=>`<tr data-key="job-${j.id}"><td class="mono">${short(j.id)}</td><td>${esc(teamLabel(s,j.team_id))}</td><td>${j.kind==='benchmark'?'Benchmark':'Trận đấu'}${j.official?'<div class="muted">Chính thức</div>':''}</td><td>${badge(j.status)}${j.error?`<div class="muted mono">${esc(j.error.code)}</div>`:''}</td><td>${date(j.created_unix_ms)}</td><td>${button('Chi tiết','admin-job',`data-id="${j.id}"`,'small')}</td></tr>`));
  return (items.length?body:empty('Không có kết quả','Đổi bộ lọc để xem các mục khác.'))+pagination(p,'operations');
}
export function adminOperations(s){
  return head('Vận hành bot.','Theo dõi tác vụ, lỗi worker và các phiên bản bot đã nộp.',button('Làm mới','refresh'))+
    tabs([['jobs','Tác vụ & worker'],['submissions','Phiên bản bot']],s.opsTab||'jobs','ops-tab')+
    `<div class="admin-filters"><label class="field"><span>Tìm kiếm</span><input class="input" type="search" name="ops-search" value="${esc(s.opsSearch||'')}" placeholder="Mã tác vụ, phiên bản, tên đội…"></label><label class="field"><span>Đội</span>${select('ops-team',[['','Tất cả đội / giải'],...s.accounts.map(a=>[a.team_id,a.team.name])],s.opsTeam||'',false)}</label>${s.opsTab!=='submissions'?`<label class="field"><span>Trạng thái</span>${select('ops-status',[['','Tất cả'],...['queued','building','validating','benchmarking','playing','completed','failed','aborted'].map(v=>[v,statusLabel(v)])],s.opsStatus||'',false)}</label><label class="field"><span>Loại</span>${select('ops-kind',[['','Tất cả'],['benchmark','Benchmark'],['match','Trận đấu']],s.opsKind||'',false)}</label>`:''}</div>`+
    panel(s.opsTab==='submissions'?'Source đã nộp':'Danh sách tác vụ',`<div id="operations-table">${operationsBody(s)}</div>`)+note('Kết quả thi đấu và source đã nộp được giữ nguyên. Trận chính thức lỗi được chạy lại từ trang giải; không tự gán thắng/thua hoặc xóa báo cáo.');
}
