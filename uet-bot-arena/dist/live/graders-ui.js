import {tr,html as l10n} from './i18n.js';
import {esc,head,panel,table,note,field,errors,submit,button,date,badge,empty} from './view.js';

const platformLabels={windows:'Windows',linux:'Linux'};
const backendLabels={windows:'AppContainer',bwrap:'bubblewrap'};

export function graderStatus(g){
  if(g.revoked_ms)return badge('failed',tr('Đã thu hồi'));
  if(!g.platform)return badge('pending',tr('Chưa kết nối'));
  if(g.needs_update)return badge('attention',tr('Cần cập nhật gói'));
  return g.online?badge('running','Online'):badge('paused','Offline');
}

export function graderRows(items,canManage=true){
  return items.map(g=>`<tr data-grader="${esc(g.id)}"><td><strong>${esc(g.name)}</strong></td><td>${graderStatus(g)}</td><td>${g.platform?esc(platformLabels[g.platform]||g.platform)+' · '+esc(backendLabels[g.backend]||g.backend||''):'—'}</td><td>${g.slots??'—'}</td><td>${g.running??0}</td><td>${g.jobs_done??0}</td><td class="mono" title="${esc(g.runtime_id||'')}">${g.runtime_id?esc(g.runtime_id.slice(7,19)):'—'}</td><td>${esc(g.version||'—')}</td><td>${date(g.last_seen_ms)}</td><td>${button((canManage?'':'🔒 ')+(g.revoked_ms?tr('Xóa'):tr('Thu hồi')),g.revoked_ms?'delete-grader':'revoke-grader',`data-id="${esc(g.id)}" data-name="${esc(g.name)}" ${canManage?'':`disabled title="${esc(tr('Chỉ organizer'))}" aria-label="${esc(tr('Chỉ organizer'))}"`}`,'small danger-button')}</td></tr>`);
}

export function gradersPage(s){
  const canManage=s.canManageGraders!==false;
  const items=s.graders?.items||[],online=items.filter(g=>g.online&&!g.needs_update),queue=s.graders?.queue||{};
  const slots=online.reduce((n,g)=>n+(g.slots||0),0);
  const stat=(label,value)=>`<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div></div>`;
  const summary=`<div class="admin-kpis">${stat(tr('Máy online'),online.length)}${stat(tr('Slot chấm'),slots)}${stat(tr('Trận chính thức chờ'),queue.official??0)}${stat(tr('Luyện tập, kiểm tra chờ'),queue.practice??0)}</div>`;
  const create=panel(tr('Thêm máy chấm'),`<form class="real-form" id="grader-form">${field(tr('Tên máy (ví dụ: PC phòng 301)'),'name','required maxlength="60"')}${note(tr('Mã máy chấm chỉ hiện một lần sau khi tạo. Dán mã vào cửa sổ cài máy chấm; hệ thống chỉ lưu bản băm.'))}${errors()}${canManage?submit(tr('Tạo mã máy chấm')):'<button class="btn primary" disabled title="'+tr('Chỉ organizer')+'">🔒 '+tr('Tạo mã máy chấm')+'</button>'}</form>`);
  const list=panel(tr('Danh sách máy chấm'),items.length?table([tr('Tên'),tr('Trạng thái'),tr('Nền tảng'),'Slot',tr('Đang chấm'),tr('Đã chấm'),'runtime_id',tr('Phiên bản'),tr('Thấy lần cuối'),''],graderRows(items,canManage)):empty(tr('Chưa có máy chấm nào.'),tr('Tạo mã ở trên rồi chạy máy chấm trên máy Windows hoặc Linux.')),button(tr('Cập nhật'),'refresh','','small secondary'));
  return head(tr('Máy chấm.'),tr('Máy chấm tự kéo việc về và chạy bot trong sandbox; hub không chạy bot. Trận tính điểm chỉ giao cho máy cùng nền tảng của giải.'))+(canManage?'':panel(tr('Quản lý máy chấm'),note(tr('🔒 Cụm máy chấm do tài khoản organizer quản lý. Bạn xem được trạng thái từng máy; tạo mã, thu hồi, xóa máy chấm chỉ organizer làm được.'))+note(tr('Máy chấm tự nối ra hub qua HTTPS, nhận việc theo lease và gửi nhịp tim mỗi 5 giây. Máy tắt thì việc được giao lại cho máy khác.'))))+summary+list+create+(canManage?packagePanel(s.graderPackage):panel(tr('Cài máy chấm'),note(tr('Windows 10/11 hoặc Ubuntu/WSL2. Mỗi ván cùng lúc cần một luồng CPU và khoảng 1 GB RAM.'))));
}

// Install links come only from the organiser API and expire after seven days.
const PACKAGE=/^uet-arena-grader-[0-9a-f]{12}\.zip$/,ROOTFS=/^rootfs-([0-9a-f]{64})\.tar\.zst$/;
const signedURL=(entry)=>new RegExp('^/api/v1/grader/download/[0-9]{1,16}/[0-9a-f]{64}/'+entry.file.replaceAll('.','\\.')+'$').test(entry.url||'');
export const validPackage=pkg=>pkg&&PACKAGE.test(pkg.file||'')&&/^[0-9a-f]{64}$/.test(pkg.sha256||'')&&signedURL(pkg)?pkg:null;
// Linux graders also download the sandbox toolchain (rootfs); its file name carries its full SHA-256.
export const linuxRootfs=pkg=>{
  const rootfs=validPackage(pkg)?.linux_rootfs,match=ROOTFS.exec(rootfs?.file||'');
  return match&&rootfs.sha256===match[1]&&signedURL(rootfs)?rootfs:null;
};

// One PowerShell block: download, verify SHA-256, unpack, install. Plain text for chat apps.
export function installCommand(pkg,origin){
  const url=`${origin}${pkg.url}`,zip='$env:TEMP\\'+pkg.file;
  return ['& {','$ErrorActionPreference = "Stop"',`[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12`,
    `Invoke-WebRequest -UseBasicParsing "${url}" -OutFile "${zip}"`,
    `if ((Get-FileHash "${zip}" -Algorithm SHA256).Hash -ne "${pkg.sha256.toUpperCase()}") { throw "SHA-256 sai, dung lai" }`,
    `Expand-Archive "${zip}" -DestinationPath C:\\uet-grader -Force`,
    `cd C:\\uet-grader`,
    `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\install-grader.ps1 -HubUrl ${origin} -Slots 2`,'}'].join('\n');
}

// Linux/WSL2: one && chain, so a failed download or SHA-256 check stops everything after it.
export function linuxInstallCommand(pkg,origin){
  const rootfs=linuxRootfs(pkg);
  return [`cd ~ && curl -fLO ${origin}${pkg.url} \\`,
    `  && echo "${pkg.sha256}  ${pkg.file}" | sha256sum -c - \\`,
    `  && python3 -m zipfile -e ${pkg.file} ~/uet-grader \\`,
    `  && cd ~/uet-grader \\`,
    `  && bash scripts/install-grader.sh --hub ${origin} --slots 2 --rootfs ${origin}${rootfs.url} --sha256 ${rootfs.sha256}`].join('\n');
}

export const graderControls={
  windows:{
    stop:'cd C:\\uet-grader\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\install-grader.ps1 -Stop',
    start:'Start-Process "$([Environment]::GetFolderPath(\'Startup\'))\\UET Arena grader default.lnk"',
    uninstall:'cd C:\\uet-grader\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\install-grader.ps1 -Uninstall\n'+
      'cd C:\\\nRemove-Item -Recurse -Force C:\\uet-grader, "$env:LOCALAPPDATA\\uet-arena", "$env:TEMP\\uet-arena-grader-*.zip" -ErrorAction SilentlyContinue'},
  linux:{
    stop:'systemctl --user stop uet-arena-grader-default',
    start:'systemctl --user start uet-arena-grader-default',
    uninstall:'bash ~/uet-grader/scripts/install-grader.sh --uninstall && rm -rf ~/uet-grader ~/uet-arena-grader-*.zip ~/.local/share/uet-arena\n'+
      'if [ -f /etc/apparmor.d/uet-arena-bwrap ]; then sudo apparmor_parser -R /etc/apparmor.d/uet-arena-bwrap; sudo rm /etc/apparmor.d/uet-arena-bwrap; fi'}};

export function controlsMessage(platform){
  const c=graderControls[platform];
  return [tr('Nếu đã cài với profile riêng, thêm đúng -Profile hoặc --profile vào lệnh dừng/gỡ và thay default trong lệnh bật lại. Lệnh gỡ sạch bên dưới xóa dữ liệu máy chấm; không dùng nếu còn profile khác đang chạy.'),tr('Dừng máy chấm:'),c.stop,tr('Bật lại sau khi dừng:'),c.start,tr('Gỡ hẳn:'),c.uninstall].join('\n\n');
}

export function graderMessage(item,pkg,origin,platform='windows'){
  if(platform==='linux')return [tr('Cài máy chấm UET Bot Arena trên Linux, tên máy: ')+item.name,
    tr('1. Cần Ubuntu 24.04 (hoặc Linux có systemd, cgroup v2 và Python 3.11+); WSL2 phải bật systemd. Cắm sạc, tắt Sleep khi đang chấm.'),
    tr('2. Mở Terminal, dán cả khối lệnh dưới đây rồi Enter (máy 2 nhân trở xuống thì đổi --slots 2 thành --slots 1). Lệnh có thể hỏi mật khẩu sudo một lần để cài bubblewrap và cho phép sandbox:'),
    linuxInstallCommand(pkg,origin),
    tr('Link tải hết hạn sau 7 ngày, hết hạn thì BTC tạo lại tin nhắn'),
    tr('3. Khi được hỏi mã máy chấm, dán mã này rồi Enter (mã không hiện khi dán):'),
    item.token,
    tr('4. Thấy dòng "OK: hub nhận máy chấm ')+item.name+tr('" rồi "Grader installed" là xong; máy chấm chạy nền bằng systemd và tự bật khi đăng nhập.'),
    tr('Dừng máy chấm:'),
    graderControls.linux.stop,
    tr('Bật lại sau khi dừng:'),
    graderControls.linux.start,
    tr('Gỡ hẳn và xóa mọi file của máy chấm (khoảng 0,5 GB, gồm rootfs):'),
    graderControls.linux.uninstall].join('\n\n');
  return [tr('Cài máy chấm UET Bot Arena, tên máy: ')+item.name,
    tr('1. Cần Windows 10/11 và Python 3.11+ (python.org, tick "Add python.exe to PATH"). Cắm sạc, tắt Sleep khi đang chấm.'),
    tr('2. Mở PowerShell, dán cả khối lệnh dưới đây rồi Enter (máy 2 nhân trở xuống thì đổi -Slots 2 thành -Slots 1):'),
    installCommand(pkg,origin),
    tr('Link tải hết hạn sau 7 ngày, hết hạn thì BTC tạo lại tin nhắn'),
    tr('3. Khi được hỏi mã máy chấm, dán mã này rồi Enter (mã không hiện khi dán):'),
    item.token,
    tr('4. Thấy dòng "OK: hub nhận máy chấm ')+item.name+tr('" là xong; máy chấm chạy ẩn và tự bật khi đăng nhập Windows.'),
    tr('Dừng máy chấm:'),
    graderControls.windows.stop,
    tr('Bật lại sau khi dừng:'),
    graderControls.windows.start,
    tr('Gỡ hẳn và xóa mọi file của máy chấm (khoảng 1 GB, gồm bộ biên dịch):'),
    graderControls.windows.uninstall].join('\n\n');
}

export function packagePanel(pkg){
  return panel(tr('Cài máy chấm'),l10n`<div class="grader-package"><p>Bấm Tạo mã máy chấm để có tin nhắn cài, gửi riêng qua Zalo. Link tải trong tin nhắn có hạn 7 ngày; gói không tải công khai.</p><p><strong>Phiên bản máy chấm:</strong> ${esc(validPackage(pkg)?.grader_version||'—')}</p><h3>Windows 10/11</h3><p>Cần Python 3.11+ (python.org, tick "Add python.exe to PATH").</p><h3>Ubuntu/Linux hoặc WSL2</h3><p>Cần systemd, cgroup v2 và Python 3.11+; WSL2 phải bật systemd.</p><div class="table-actions">${button(tr('Sao chép lệnh dừng/gỡ (Windows)'),'copy-grader-controls','data-platform="windows"','secondary')}${button(tr('Sao chép lệnh dừng/gỡ (Linux)'),'copy-grader-controls','data-platform="linux"','secondary')}</div></div>`);
}

export function graderTokenModal(item,pkg=null,origin=''){
  const message=(platform,label)=>l10n`<label class="field"><span>${label}</span><pre class="report-json" id="grader-message-${platform}">${esc(graderMessage(item,pkg,origin,platform))}</pre></label>`;
  const windows=validPackage(pkg)?message('windows',tr('Tin nhắn gửi người cài Windows (gồm link, lệnh và mã)')):'';
  const linux=linuxRootfs(pkg)?message('linux',tr('Tin nhắn gửi người cài Linux')):'';
  const copy=(platform,label,kind)=>button(label,'copy-grader-message',`data-platform="${platform}"`,kind);
  return l10n`<p>Đây là lần duy nhất mã của <strong>${esc(item.name)}</strong> được hiển thị. Sao chép rồi dán vào máy chấm khi được hỏi.</p><label class="field"><span>Mã máy chấm</span><div class="credential-value" id="grader-token">${esc(item.token)}</div></label>${windows}${linux}<div class="modal-actions">${windows?copy('windows',tr('Sao chép tin nhắn Windows'),'primary'):''}${linux?copy('linux',tr('Sao chép tin nhắn Linux'),'secondary'):''}${button(tr('Sao chép mã'),'copy-grader-token','',windows?'secondary':'primary')}${button(tr('Đóng'),'close-modal')}</div>`;
}
