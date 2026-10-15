import { boardAt, teamById } from './data.js';
const paths = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  radio:'<circle cx="12" cy="12" r="2"/><path d="M7 7a7 7 0 0 0 0 10m10-10a7 7 0 0 1 0 10M4 4a11 11 0 0 0 0 16M20 4a11 11 0 0 1 0 16"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18m-13 4h3m3 0h2m-8 3h3"/>',
  trophy:'<path d="M8 3h8v7a4 4 0 0 1-8 0V3Zm0 2H4v3a4 4 0 0 0 4 4m8-7h4v3a4 4 0 0 1-4 4m-4 2v5m-4 2h8m-7-2h6"/>',
  users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5v1"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  down:'<path d="m6 9 6 6 6-6"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-11 12a2 2 0 0 0 4 0"/>',
  book:'<path d="M12 6c-3-3-7-3-10-2v15c4-1 7-1 10 2 3-3 6-3 10-2V4c-3-1-7-1-10 2Zm0 0v15"/>',
  logout:'<path d="M9 4H4v16h5m5-12 4 4-4 4m-6-4h12"/>',
  code:'<path d="m7 6-6 6 6 6m10-12 6 6-6 6m-3-15-4 18"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  play:'<path d="m8 4 12 8-12 8V4Z"/>',
  pause:'<path d="M8 5v14M16 5v14"/>',
  rewind:'<path d="M5 5v14m14-14L7 12l12 7V5Z"/>',
  end:'<path d="M19 5v14M5 5l12 7-12 7V5Z"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  x:'<path d="m6 6 12 12M6 18 18 6"/>',
  file:'<path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8m-8 4h6"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  shield:'<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z"/><path d="m8 12 3 3 5-6"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  shuffle:'<path d="M3 5h3c4 0 8 14 12 14h3m-4-4 4 4-4 4M3 19h3c4 0 8-14 12-14h3m-4-4 4 4-4 4"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
  link:'<path d="m10 7 3-3a5 5 0 0 1 7 7l-3 3M7 10l-3 3a5 5 0 0 0 7 7l3-3m-6-2 8-8"/>',
  settings:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>'
};
export const icon = (name, cls='') => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name]||paths.grid}</svg>`;
export const escapeHTML = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const logo = () => `<span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>`;
export const avatar = (team, size='') => `<span class="team-avatar ${size}" style="--avatar:${team.color}">${escapeHTML(team.tag)}</span>`;
export const badge = (status, text='') => {
  const map={live:['live','Đang đấu'],paused:['paused','Tạm dừng'],scheduled:['neutral','Sắp diễn ra'],finished:['neutral','Đã kết thúc'],accepted:['success','Hợp lệ'],rejected:['danger','Cần sửa'],preview:['neutral','Chờ xác minh'],locked:['neutral','Đã chốt']};
  const [cls,label]=map[status]||['neutral',status];
  return `<span class="badge ${cls}">${status==='live'?'<span class="live-dot"></span>':''}${escapeHTML(text||label)}</span>`;
};
export const btn = (label, action='', kind='secondary', ico='', extra='') => `<button type="button" class="btn ${kind}" ${action?`data-action="${action}"`:''} ${extra}>${ico?icon(ico):''}${label}</button>`;
export const navLink = (route, label, kind='text-link', ico='arrow') => `<a class="${kind}" href="#/${route}">${label}${ico?icon(ico):''}</a>`;
export const sectionHead = (title, sub='', right='') => `<div class="section-head"><div><h2>${title}</h2>${sub?`<p>${sub}</p>`:''}</div>${right}</div>`;
export const pieceIcon = kind => `<svg viewBox="0 0 32 32" aria-hidden="true">${kind==='r'?'<path d="m9 7 11-1 6 8-3 10-15 1-4-10Z"/>':kind==='p'?'<path d="M8 5h12l5 5v17H8Z"/><path class="detail" d="M20 5v6h5M12 15h9m-9 4h9"/>':'<circle cx="9" cy="23" r="4"/><circle cx="23" cy="23" r="4"/><path class="scissor-detail" d="m11 20 12-15m-3 15L9 5m7 10v1"/>'}</svg>`;
export function board(step=24, mini=false, flipped=false) {
  const {pieces,moves}=boardAt(step);
  const last=moves.at(-1);
  let html='';
  for(let rr=0;rr<9;rr++)for(let cc=0;cc<9;cc++){
    const r=flipped?8-rr:rr,c=flipped?8-cc:cc;
    const square=`${'abcdefghi'[c]}${9-r}`;
    const p=pieces.find(x=>x[0]===r&&x[1]===c);
    const base=c===4&&(r===0||r===8);
    html+=`<div class="square ${(r+c)%2?'dark':''} ${last&&(last.to===square||last.from===square)?'last-move':''} ${base?'base':''}" ${mini?'':`data-square="${square}" role="button" tabindex="0" aria-label="Ô ${square}${p?`, ${p[3]==='green'?'phe xanh lá':'phe xanh dương'}, ${p[2]==='r'?'Búa':p[2]==='p'?'Bao':'Kéo'}`:''}"`}>${base?'<span class="base-symbol">⌂</span>':''}${p?`<span class="piece ${p[3]}">${pieceIcon(p[2])}</span>`:''}${!mini&&cc===0?`<span class="row-label">${9-r}</span>`:''}${!mini&&rr===8?`<span class="col-label">${'abcdefghi'[c]}</span>`:''}</div>`;
  }
  return `<div class="board ${mini?'mini-board':''}" aria-label="Bàn đấu OTTv2 minh họa 9 nhân 9">${html}</div>`;
}
export function matchCard(match, compact=false) {
  const a=teamById(match.a),b=teamById(match.b);
  return `<a class="match-card ${compact?'compact':''}" href="#/${match.status==='finished'?'replay':'arena'}/${match.id}"><div class="match-card-top">${badge(match.status)}<span>Bảng ${match.group} <span class="sep">/</span> ${match.id}</span></div><div class="match-versus"><div>${avatar(a)}<strong>${a.name}</strong></div><span class="versus">vs</span><div>${avatar(b)}<strong>${b.name}</strong></div></div>${compact?'':board(match.turn,true)}<div class="match-card-bottom"><span>${icon(match.status==='scheduled'?'clock':'code')}${match.status==='scheduled'?match.time:`Lượt ${match.turn}`}</span><span>${match.status==='scheduled'?'Vòng '+match.round:icon('eye')+' '+match.viewers}${icon('arrow')}</span></div></a>`;
}
export const emptyState = (title, text) => `<div class="empty-state">${icon('search')}<h3>${title}</h3><p>${text}</p></div>`;
