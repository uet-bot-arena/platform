import {ladderInterval} from './ladder-clock.js';
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
  return panel(c.title,`<p>${tr(formats[c.format]||c.format)} · ${competitionBadge(c)} · ${tr('Hạn nộp')}: ${date(c.submission_deadline)}</p>`+
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
  return panel(tr('Giải của đội'),rows.length?table([tr('Giải'),tr('Trạng thái'),tr('Trong giải'),tr('Hạng'),tr('Elo / điểm'),tr('Ván'),tr('Đợt kế tiếp'),''],rows.map(c=>`<tr><td>${esc(c.title)}</td><td>${competitionBadge(c)}</td><td>${esc(participationLabel(c))}</td><td>${c.rank??'—'}</td><td>${c.points!=null?c.points+' '+tr('điểm')+(c.rating!=null?' · ':''):''}${c.rating!=null?'Elo '+c.rating.toFixed(2):c.points==null?'—':''}</td><td>${c.played}</td><td>${date(c.next_round_ms)}</td><td>${link('competitions/'+c.id,tr('Mở giải'),'small')}</td></tr>`)):empty(tr('Chưa có giải')));
}

export const competitionBadge=c=>badge(c.status,(c.format||c.config?.format)==='elo'&&c.status==='open'?tr('Đang nhận bài'):undefined);
export function entryChecklist(s){
  const t=s.personal?.team||s.team,n=t?.member_count??t?.roster?.length??0;
  const open=(s.competitions||[]).filter(c=>!c.archived_ms&&c.status==='open'&&c.config.format==='elo');
  const own=s.teamCompetitions?.items||[],passed=(s.github?.commits||[]).some(c=>(c.checks||[]).some(x=>x.state==='pass'));
  const ready=open.some(c=>['active','eligible'].includes(own.find(x=>x.id===c.id)?.state));
  const steps=[[n===4,tr('Có đội')+' · '+n+'/4',link('team',tr('Cá nhân & đội'))],
    [!!s.github?.repo,tr('Đã gắn repo GitHub'),link('submissions',tr('Nộp bot'))],
    [passed,tr('Có bản Đạt 72 mẫu'),link('submissions',tr('Nộp bot'))],
    [ready,tr('Giải đang nhận bài')+': '+(open.length?open.map(c=>c.title+' · '+participationLabel(own.find(x=>x.id===c.id))).join('; '):tr('Chưa có giải')),link('competitions',tr('Giải đấu'))]];
  return panel(tr('Các bước vào giải'),steps.every(x=>x[0])?`<p>✓ ${tr('Đã đủ các bước vào giải')}</p>`:`<ol class="onboarding">${steps.map(([ok,text,action])=>`<li><span>${ok?'✓':'○'}</span><div>${esc(text)} · ${action}</div></li>`).join('')}</ol>`);
}
export function eloJoinPanel(s){
 const c=s.competition,own=s.teamCompetitions?.items?.find(x=>x.id===c.id),n=c.participation_counts||{};
 const reasons=own?.reasons||[],route=!s.user?.team_id||reasons.includes('team_size')?'team':'submissions';
 return panel(tr('Tham gia'),`<p>${tr('Hạn nộp (giờ Việt Nam)')}: ${date(c.submission_deadline)}</p><p>${tr('Ladder mỗi ')}${esc(ladderInterval(c.config.ladder))} · ${tr('Đợt kế tiếp')}: ${date(c.elo?.next_round_ms)}</p><p>${c.config.official.finalists} ${tr('đội đầu vào chung kết')}</p><p>${tr('Đang đấu')}: ${n.active??0} · ${tr('Đủ điều kiện')}: ${n.eligible??0} · ${tr('Chưa đủ điều kiện')}: ${n.ineligible??0}</p>`+(s.user?.role==='admin'?'':!s.user?link('login',tr('Đăng nhập để tham gia'),'primary'):`<p>${s.user.team_id?esc(participationLabel(own)):tr('Chưa có đội')}</p>${!own||own.state==='ineligible'?link(route,route==='team'?tr('Cá nhân & đội'):tr('Nộp bot'),'primary'):''}`));
}
