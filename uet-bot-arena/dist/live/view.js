import {tr,html as l10n,locale,languageButton} from './i18n.js';
export const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths={grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  radio:'M8 8a6 6 0 0 0 0 8m8-8a6 6 0 0 1 0 8M4 4a12 12 0 0 0 0 16M20 4a12 12 0 0 1 0 16M12 11v2',
  calendar:'M3 5h18v16H3zM7 2v6M17 2v6M3 11h18', trophy:'M7 3h10v7a5 5 0 0 1-10 0zM7 5H3v4l5 4M17 5h4v4l-5 4M12 15v6M8 21h8',
  users:'M7 8a3 3 0 1 0 6 0a3 3 0 1 0-6 0M3 21v-3a7 7 0 0 1 14 0v3M17 5a3 3 0 0 1 0 6M19 15l2 6',
  upload:'M12 16V3M7 8l5-5 5 5M4 15v6h16v-6', arrow:'M4 12h16M14 6l6 6-6 6', book:'M12 6C9 3 5 3 2 4v16c4-1 7-1 10 2c3-3 6-3 10-2V4c-4-1-7-1-10 2zM12 6v16',
  shield:'M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6zM8 12l3 3 5-6', code:'M7 6l-6 6 6 6M17 6l6 6-6 6M14 3l-4 18',
  sun:'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon:'M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11z',
  menu:'M4 6h16M4 12h16M4 18h16', x:'M6 6l12 12M6 18L18 6', logout:'M9 4H4v16h5M14 8l4 4-4 4M8 12h12',
  play:'M8 4l12 8-12 8z', clock:'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7v5l3 2', check:'M5 12l4 4L19 6', download:'M12 3v13M7 11l5 5 5-5M4 16v5h16v-5', file:'M14 2H5v20h14V7zM14 2v6h5M8 12h8M8 16h6',home:'M3 11l9-8 9 8M5 9v12h14V9M9 21v-7h6v7',bolt:'M13 2 4 14h7l-1 8 10-13h-8z',target:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',info:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 11v6M12 7v1'};
export const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[name]||paths.grid}"/></svg>`;
export const logo=()=>'<span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>';
export const button=(label,action,extra='',kind='secondary')=>`<button type="button" class="btn ${kind}" data-action="${action}" ${extra}>${esc(label)}</button>`;
export const link=(route,label,kind='secondary',ico='')=>`<a href="#/${route}" class="btn ${kind}">${ico?icon(ico):''}${esc(label)}</a>`;
export const field=(label,name,extra='',type='text',value='')=>`<label class="field"><span>${esc(label)}</span><input class="input" type="${type}" name="${name}" value="${esc(value)}" ${extra}></label>`;
export const password=(label,name,autocomplete='current-password')=>l10n`<div class="field"><label for="${name}">${esc(label)}</label><div class="password-wrap"><input class="input" id="${name}" type="password" name="${name}" required maxlength="128" autocomplete="${autocomplete}"><button type="button" data-action="password" data-target="${name}" aria-label="Hiện mật khẩu">Hiện</button></div></div>`;
export const head=(title,sub='',actions='')=>`<div class="page-heading"><div><h1>${esc(title.replace(/[.]$/,''))}</h1>${sub?`<p>${esc(sub)}</p>`:''}</div>${actions?`<div class="page-actions">${actions}</div>`:''}</div>`;
export const empty=(title,text='')=>`<div class="empty-state"><h3>${esc(title)}</h3>${text?`<p>${esc(text)}</p>`:''}</div>`;
const statusLabels={draft:tr('Bản nháp'),open:tr('Mở đăng ký'),locked:tr('Đã chốt'),running:tr('Đang chạy'),paused:tr('Tạm dừng'),completed:tr('Hoàn tất'),pending:tr('Chờ'),queued:tr('Đã vào hàng đợi'),starting:tr('Đang chuẩn bị'),building:tr('Đang biên dịch'),validating:tr('Đang khởi động'),benchmarking:tr('Đang kiểm tra'),playing:tr('Đang đấu'),failed:tr('Lỗi'),aborted:tr('Đã dừng'),attention:tr('Cần xử lý'),approved:tr('Đã duyệt'),rejected:tr('Từ chối')};
export const statusLabel=status=>statusLabels[status]||status;
export const runtimeLabels={cpp17:'C++17',c11:'C11',python3:'Python 3.11 (PyPy)'};
export const runtimeLabel=runtime=>runtimeLabels[runtime]||runtime||'C++17';
export const badge=(status,label='')=>`<span class="badge ${['running','playing','benchmarking'].includes(status)?'live':['completed','approved'].includes(status)?'success':['failed','rejected'].includes(status)?'danger':['attention','paused'].includes(status)?'warning':'neutral'}">${esc(label||statusLabel(status))}</span>`;
export const date=value=>value?new Intl.DateTimeFormat(locale(),{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Ho_Chi_Minh'}).format(new Date(value)):'—';
export const short=id=>String(id||'').slice(0,8);
export const metric=value=>typeof value==='number'?new Intl.NumberFormat(locale(),{maximumFractionDigits:1}).format(value)+' ms':'—';
export const select=(name,options,value='',required=true)=>`<select class="select" name="${name}" ${required?'required':''}>${options.map(([v,l])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
export const panel=(title,body,actions='')=>`<section class="panel"><div class="section-head"><h2>${esc(title)}</h2>${actions}</div>${body}</section>`;
export const note=text=>`<div class="info-note"><span>${esc(text)}</span></div>`;
export const table=(headers,rows)=>`<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
export const errors=()=>'<div class="form-error" role="alert"></div>';
export const submit=label=>`<button type="submit" class="btn primary">${esc(label)}</button>`;

const nav=[['announcements',tr('Thông báo'),'book'],['home',tr('Tổng quan'),'grid'],['queue',tr('Tình trạng chấm'),'clock'],['live',tr('Trực tiếp'),'radio'],['schedule',tr('Lịch thi đấu'),'calendar'],['standings',tr('Bảng xếp hạng'),'trophy'],['competitions',tr('Giải đấu'),'shield']];
const teamNav=[['team',tr('Cá nhân & đội'),'users'],['submissions',tr('Nộp bot'),'upload'],['benchmark',tr('Kiểm tra bot'),'code'],['practice',tr('Luyện tập'),'play']];
export function shell(s,content) {
  const user=s.user, admin=user?.role==='admin';
  const menu=[...nav,['finals',tr('Chung kết'),'trophy'],...(user&&!admin?[teamNav[0],['matching',tr('Ghép đội'),'users'],...(user.team_id?teamNav.slice(1):[])]:[]),...(user?[['rulesets','Ruleset add-on','code']]:[]),['guide',tr('Tài liệu & thể lệ'),'book'],['limits',tr('Giới hạn & báo lỗi'),'shield'],...(admin?[['admin',tr('Đội & tài khoản'),'users'],['users',tr('Người dùng & lập đội'),'users'],['graders',tr('Máy chấm'),'bolt'],['email',tr('Gửi email'),'book'],['operations',tr('Vận hành bot'),'code'],['audit',tr('Nhật ký quản trị'),'shield']]:[])];
  return l10n`<div class="app-shell ${s.menuOpen?'menu-open':''}"><button class="menu-backdrop" data-action="menu" aria-label="Đóng menu"></button><aside class="sidebar"><a class="brand" href="#/home">${logo()}<span><strong>UET Bot Arena</strong><small>Thi đấu bot OTTv2</small></span></a><div class="season">Python <span>·</span> C <span>·</span> C++</div><nav aria-label="Điều hướng chính">${menu.map(([route,label,ico])=>`<a href="#/${route}" class="nav-item ${s.route===route||route==='live'&&['arena','replay'].includes(s.route)?'active':''}" ${s.route===route?'aria-current="page"':''}>${icon(ico)}${esc(label)}</a>`).join('')}</nav><div class="sidebar-bottom"><div class="profile ${user?'':'guest-profile'}"><span class="person-avatar">${esc((s.team?.name||user?.username||'UET').slice(0,2).toUpperCase())}</span><div><strong>${esc(user?.username||tr('Khách'))}</strong><small>${admin?tr('Ban tổ chức'):esc(s.team?.name||(user?tr('Tài khoản đội'):tr('Đăng nhập để quản lý đội')))}</small></div>${user?l10n`<button class="icon-btn" data-action="logout" aria-label="Đăng xuất">${icon('logout')}</button>`:link('login',tr('Đăng nhập'),'small')}</div></div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb"><button class="icon-btn mobile-menu" data-action="menu" aria-label="Mở menu" aria-expanded="${!!s.menuOpen}">${icon('menu')}</button></div><div class="top-actions">${languageButton()}${themeButton()}<span class="connection-label ${s.offline?'offline':''}"><i></i>${s.offline?tr('Mất kết nối'):tr('Kết nối máy chủ')}</span>${['home','standings','competitions','submissions','audit','jobs','benchmark','practice','graders','email','team','matching','users'].includes(s.route)?button(tr('Làm mới'),'refresh','','small secondary'):''}${user?link('change-password',tr('Đổi mật khẩu'),'small'):link('login',tr('Đăng nhập'),'small')}</div></header><main class="page ${['arena','replay'].includes(s.route)?'arena-page':''}" id="main" tabindex="-1">${content}<footer class="footer"><span>UET Bot Arena · OTTv2 · Python / C / C++</span><a href="#/guide">Tài liệu & thể lệ</a></footer></main></div></div>`;
}

export function authFrame(content) {
  return l10n`<main class="real-auth" id="main" tabindex="-1"><section class="auth-brand-panel"><a class="brand" href="#/home">${logo()}<span><strong>UET Bot Arena</strong><small>Đại học Công nghệ</small></span></a><div class="auth-story"><h1>Thi đấu bot OTTv2</h1><p>Đăng ký đội, nộp bot Python, C hoặc C++ và theo dõi các trận đấu Oẳn Tù Tì trên bàn cờ.</p><dl class="auth-facts"><div><dt>Ngôn ngữ</dt><dd>Python 3.11 / C11 / C++17</dd></div><div><dt>Thể thức</dt><dd>Ladder Elo / Vòng tròn hai lượt</dd></div><div><dt>Tài khoản</dt><dd>Email trường · Mỗi người một tài khoản</dd></div></dl><a class="auth-public-link" href="#/live">Xem trận không cần đăng nhập ${icon('arrow')}</a></div><small>UET Bot Arena · OTTv2</small></section><section class="real-auth-content"><div class="auth-theme">${languageButton()}${themeButton()}</div>${content}</section></main>`;
}

export const etaRange=eta=>!eta?'':eta.upper_seconds<120?l10n`còn khoảng ${Math.floor(eta.lower_seconds)}–${Math.ceil(eta.upper_seconds)} giây`:l10n`còn khoảng ${Math.ceil(eta.lower_seconds/60)}–${Math.ceil(eta.upper_seconds/60)} phút`;

// Moon shows in light mode (switch to dark), sun in dark mode; CSS picks one so no re-render is needed.
export function themeButton(){const label=tr('aria-label="Chuyển giữa giao diện sáng và tối"');return `<button type="button" class="icon-btn theme-toggle" data-action="theme" ${label} ${label.replace('aria-label','title')}><span class="theme-moon">${icon('moon')}</span><span class="theme-sun">${icon('sun')}</span></button>`;}
