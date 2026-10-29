import {quickPractice} from './quick-practice.js';
import {tr,html as l10n} from './i18n.js';
import {esc,head,panel,note,table,field,errors,submit,link,date,short,etaRange} from './view.js';

const stateNames={detected:tr('Đang nhận bài'),checking:tr('Đang kiểm tra'),pass:tr('Đạt'),fail:tr('Lỗi'),replaced:tr('Đã thay bằng bản mới')};
export function githubProgress(commit){
  const checks=commit.checks||[],active=checks.find(c=>['waiting','checking'].includes(c.state));
  if(active){
    const eta=active.eta?' · '+etaRange(active.eta):'';
    if(active.phase==='queued'||!active.job_id){const p=active.queue_position;return tr('Đang chờ')+(p?tr(' · vị trí ')+p+'/'+Math.max(p,active.queue_total||p):'')+eta;}
    if(active.phase==='building')return tr('Đang biên dịch')+eta;
    return tr('Đang kiểm tra ')+(active.samples||0)+'/72'+eta;
  }
  return stateNames[commit.state]||commit.state;
}
export function githubPanel(s){
  const g=s.github;if(!g)return note(tr('Đang tải kết nối GitHub…'));
  const captain=s.personal?.team?.captain_id===s.user?.id;
  const name=g.repo_prefix+g.repo_code;
  const repo=g.repo?`<p><a href="https://github.com/${esc(g.repo.owner)}/${esc(g.repo.name)}" target="_blank" rel="noopener noreferrer">${esc(g.repo.owner+'/'+g.repo.name)}</a></p>${g.repo.error?`<div class="form-error visible" role="status">${esc(g.repo.error)}</div>`:note(tr('Đã kết nối · Hub tự nhận push nhánh main. Chỉ bot/ được chấm.'))}`:
    note(tr('Mã đội: ')+g.repo_code+tr(' · Tên repo: ')+name)+(captain?`<form id="github-attach-form" class="real-form">${field(tr('Link repo private'),'repository',tr('required maxlength="180" placeholder="github.com/chủ-repo/')+esc(name)+'"','url')}${errors()}${submit(tr('Gắn repo một lần'))}</form>`:note(tr('Đội trưởng tạo repo private đúng tên trên và gắn link tại đây.')));
  const banners=(g.deadlines||[]).map(d=>note(d.state==='locked'?tr('Đã chốt: ')+d.selected_sha.slice(0,7)+tr(' · push lúc ')+date(d.pushed_ms)+tr(' (giờ GitHub)'):d.error||tr('Đang kiểm tra bản tại hạn'))).join('');
  const previews=(g.previews||[]).map(p=>note(p.sha?tr('Nếu hết hạn lúc này, bản dự giải là ')+p.sha.slice(0,7)+tr(' (Đạt) · hạn ')+date(p.deadline):tr('Chưa có bản Đạt cho ')+p.title+tr(' · hạn ')+date(p.deadline))).join('');
  const commits=(g.commits||[]).length?table(['Commit','Push (GitHub)',tr('Tiến độ'),''],g.commits.map(c=>`<tr data-key="commit-${esc(c.sha)}"><td><code>${esc(c.sha.slice(0,7))}</code><br>${esc(c.message)}</td><td>${date(c.pushed_ms)}</td><td>${esc(githubProgress(c))}${c.error?`<p class="form-error visible">${esc(c.error)}</p>`:''}</td><td>${(c.checks||[]).filter(x=>x.job_id).map(x=>link('jobs/'+x.job_id,tr('Log kiểm tra'),'small')).join('')}</td></tr>`)):note(tr('Push mã nguồn lên main để bắt đầu kiểm tra.'));
  return `<div id="github-page">${panel(tr('Repo bài nộp'),note(tr('Mã đội: ')+g.repo_code)+repo+l10n`<p><a href="${esc(g.starter_url)}" target="_blank" rel="noopener noreferrer">Tạo repo từ mẫu</a> · <a href="${esc(g.install_url)}" target="_blank" rel="noopener noreferrer">Cài / chọn repo cho App</a> · ${link('guide',tr('Hướng dẫn nộp bài'))}</p>`)}${banners}${previews}${panel(tr('20 lần kiểm tra gần nhất'),commits+note(tr('Lịch sử mã nguồn nằm trong Git của đội. Push mới sau hạn không đổi bài đã chốt.')))}</div>`;
}
export function githubPage(s){if(!s.user?.team_id)return head(tr('Thử nhanh'),tr('Nộp chính thức qua GitHub và vào giải cần đội đủ 4 người'))+link('team',tr('Cá nhân & đội'))+quickPractice(s);return head(tr('Nộp bot qua GitHub'),tr('Repo private của đội · main · tự kiểm tra 72 mẫu.'))+githubPanel(s)+quickPractice(s);}
export function githubGuide(){return panel(tr('Nộp bài qua GitHub'),l10n`<ol><li>Tại trang Cá nhân &amp; đội, lấy mã đội và tên repo <code>ott2026-&lt;mã đội&gt;</code>.</li><li>Từ repo mẫu của BTC, chọn Use this template → Create a new repository; tài khoản đội trưởng sở hữu repo, đặt đúng tên và chọn Private.</li><li>Trong Settings → Collaborators, mời ba bạn cùng đội.</li><li>Cài GitHub App UET Bot Arena, chọn Only select repositories rồi chọn đúng repo private này.</li><li>Đội trưởng dán link repo tại trang đội. Kết nối chỉ gắn một lần.</li><li>Sửa mã trong <code>bot/</code>, commit và push lên <code>main</code>. Xem hàng chờ, tiến độ 72 mẫu và log tại trang Nộp bot.</li></ol><p>Chỉ một file chính: bot.cpp (C++17), bot.c (C11), hoặc bot.py (Python). File phụ cùng ngôn ngữ nằm phẳng trong bot/. Không nộp binary, symlink, submodule, LFS hoặc thư mục con. Có thể chạy <code>python run_local.py --check</code> trước khi push.</p><p>Hết hạn: hub lấy lần push cuối không muộn hơn hạn theo giờ GitHub. Nếu bản đó Lỗi, lùi tối đa năm lần push để tìm bản Đạt. Không có bản Đạt thì đội không có bài dự giải. Push sau hạn không đổi bài chốt; BTC khóa danh sách sau khi hub kiểm xong.</p><p>Lịch sử mã nguồn: <code>git log --oneline</code>, <code>git checkout &lt;sha&gt;</code>, chạy lại <code>python run_local.py</code>. Khi sửa tiếp, quay về <code>git switch main</code>.</p>${note(tr('Thiếu thư mời App: cài lại và chọn đúng repo. Repo Public: đổi sang Private. Sai tên: dùng đúng mã của đội. Không Đạt: mở log của commit và sửa rồi push bản mới.'))}`);}
