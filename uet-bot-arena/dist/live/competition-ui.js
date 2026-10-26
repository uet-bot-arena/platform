import {tr} from './i18n.js';
import {esc,select,panel,table,link,note,date,badge,empty} from './view.js';

export const formats={elo:'Ladder Elo',double_round_robin:'Vòng tròn hai lượt',swiss:'Swiss'};
const phases={ladder:'Ladder',general:'Vòng chung',round_robin:'Vòng tròn'};
const reasons={team_size:'Chưa đủ 4 người',repository:'Chưa gắn repo',passed_bot:'Chưa có bản Đạt'};
export function participationLabel(item){
  if(!item)return '—';
  return item.state==='active'?tr('Đang đấu'):item.state==='excluded'?tr('Bị loại'):item.state==='eligible'?tr('Đủ điều kiện, chờ đợt sau'):tr('Chưa đủ: ')+(item.reasons||[]).map(r=>tr(reasons[r]||r)).join('; ');
}
export function competitionRoute(raw,value){
  const [path,query='']=raw.split('?'),q=new URLSearchParams(query);
  if(value)q.set('competition',value);else q.delete('competition');
  return path+(q.size?'?'+q.toString():'');
}
export function competitionFilter(s){
  return `<label class="field competition-filter"><span>${tr('Giải')}</span>${select('competition-scope',[['',tr('Tất cả')],...(s.competitions||[]).filter(c=>!c.archived_ms).map(c=>[c.id,c.title])],s.selectedCompetition||'')}</label>`;
}
export function competitionSummary(s){
  const c=s.competitionSummary;if(!c)return '';
  const n=c.counts,p=c.progress,rows=[['Đang đấu',n.active],['Đủ điều kiện, chờ đợt sau',n.eligible],['Chưa đủ điều kiện',n.ineligible],['Bị loại',n.excluded]];
  return panel(c.title,`<p>${tr(formats[c.format]||c.format)} · ${badge(c.status)} · ${tr('Hạn nộp')}: ${date(c.submission_deadline)}</p>`+
    `<div class="admin-kpis">${rows.map(([label,value])=>`<div class="stat"><div class="stat-label">${tr(label)}</div><div class="stat-value">${value}</div></div>`).join('')}</div>`+
    (Object.keys(c.reasons).length?note(Object.entries(c.reasons).map(([key,value])=>tr(reasons[key]||key)+': '+value).join(' · ')):'')+
    `<p>${tr('Ván xong / chờ / cần xử lý')}: ${c.fixtures.completed} / ${c.fixtures.waiting} / ${c.fixtures.attention}</p>`+
    (p?`<p>${tr('Đợt hiện tại')}: ${p.current?tr(phases[p.current.phase]||p.current.phase)+' '+p.current.number:'—'} · ${tr('Đợt kế tiếp')}: ${date(p.next_round_ms)}</p>`:'')+
    `<p>${tr('Việc trong hàng đợi của giải')}: ${Object.values(c.queue).reduce((a,b)=>a+b,0)}</p>`,link('competitions/'+c.id,tr('Mở giải'),'small'));
}
export function teamCompetitions(s){
  const hasTeam=!!(s.personal?.team||s.team||s.user?.team_id);
  const rows=s.teamCompetitions?.items||[];
  if(!hasTeam)return panel(tr('Giải của đội'),note(tr('Cần đội đủ 4 người, gắn repo GitHub và có bản Đạt để vào giải Elo'))+(s.competitions||[]).filter(c=>c.status==='open'&&!c.archived_ms).map(c=>link('competitions/'+c.id,c.title,'small')).join(''));
  return panel(tr('Giải của đội'),rows.length?table([tr('Giải'),tr('Trạng thái'),tr('Trong giải'),tr('Hạng'),tr('Elo / điểm'),tr('Ván'),tr('Đợt kế tiếp'),''],rows.map(c=>`<tr><td>${esc(c.title)}</td><td>${badge(c.status)}</td><td>${esc(participationLabel(c))}</td><td>${c.rank??'—'}</td><td>${c.points!=null?c.points+' '+tr('điểm')+(c.rating!=null?' · ':''):''}${c.rating!=null?'Elo '+c.rating.toFixed(2):c.points==null?'—':''}</td><td>${c.played}</td><td>${date(c.next_round_ms)}</td><td>${link('competitions/'+c.id,tr('Mở giải'),'small')}</td></tr>`)):empty(tr('Chưa có giải')));
}
