import {tr} from './i18n.js';
import {esc,head,panel,link,button,field,select,note,errors,submit,badge,empty,date,backLink} from './view.js';
import {timeField,timeLabel} from './time-control.js';
import {rulesetField} from './experimental-ui.js';
import {importedBanner} from './imported-results.js';
import {resultLabel} from './result.js';

const stages=[['quarter','Tứ kết',['TK1','TK2','TK3','TK4']],['semi','Bán kết',['BK1','BK2']],['medals','Chung kết',['FINAL']]];
const states={draft:'Bản nháp',groups:'Vòng bảng',knockout:'Loại trực tiếp',completed:'Hoàn tất',stopped:'Đã dừng tự động'};
const names={A:'Bảng A',B:'Bảng B',C:'Bảng C',D:'Bảng D',TK1:'Tứ kết 1',TK2:'Tứ kết 2',TK3:'Tứ kết 3',TK4:'Tứ kết 4',BK1:'Bán kết 1',BK2:'Bán kết 2',FINAL:'Chung kết',BRONZE:'Tranh hạng ba'};
export function finalCard(f,event,admin=false){
 const champion=f.teams.find(t=>t.team_id===f.podium?.[0]);
 return `<article class="real-card" data-key="final-${esc(f.id)}"><div class="section-head">${badge(f.status,tr(states[f.status]||f.status))}<span class="muted">${esc(timeLabel(f.profile).replace(tr(' cho cả ván'),''))}</span></div><h3>${esc(f.name)}</h3><p>${tr('Chung kết · 4 bảng + loại trực tiếp · 16 đội')}</p>${champion?`<p><strong>${tr('Vô địch: ')}${esc(champion.name)}</strong></p>`:''}${event?`<p class="muted">${tr('Nạp từ hub LAN lúc ')}${date(event.imported_ms)}</p>`:''}<div class="table-actions">${link('finals/'+f.id,tr('Xem nhánh đấu'),'primary small')}${event&&admin?button(tr('Ẩn'),'hide-imported',`data-id="${esc(event.id)}"`,'small danger-button'):''}</div></article>`;
}
export function finalsCards(s){
 const items=s.finals||[];
 return items.length?`<div class="real-card-grid">${items.map(f=>finalCard(f,(s.importedEvents||[]).find(e=>(e.finals||[]).some(x=>x.id===f.id)),s.user?.role==='admin')).join('')}</div>`:'';
}
export function finalsForm(s){
 const teams=(s.finalTeams||[]).filter(t=>!t.is_guest);
 return panel(tr('Tạo chung kết 16 đội'),`<form id="finals-form" class="real-form">${field(tr('Tên chung kết'),'name','required minlength="3" maxlength="100"')}
 ${timeField()}<label class="field"><span>${tr('Nền tảng máy chấm')}</span>${select('grader_platform',[['windows','Windows'],['linux','Ubuntu / Linux']])}</label>${rulesetField(s)}
 ${note(tr('Nhập hạng online 1–16 không trùng. Hệ thống ghim bản Đạt mới nhất khi bắt đầu vòng bảng.'))}
 <div class="finals-entrants">${Array.from({length:16},(_,i)=>`<div class="form-grid"><label class="field"><span>${tr('Đội')} ${i+1}</span>${select('team-'+i,[['',tr('Chọn đội')],...teams.map(t=>[t.id,t.name])])}</label>${field(tr('Hạng online'),'rank-'+i,'required min="1" max="16"','number',String(i+1))}</div>`).join('')}</div>
 <label class="check-field"><input type="checkbox" name="pause_between_rounds">${tr('Chờ BTC bắt đầu giữa các vòng')}</label>${errors()}${submit(tr('Tạo bản nháp'))}</form>`);
}
export function waitingStage(f){
 for(const [stage,,keys] of [['quarter','', ['TK1','TK2','TK3','TK4']],['semi','',['BK1','BK2']],['medals','',['FINAL','BRONZE']]])
  if(!f.started_rounds.includes(stage)&&keys.every(k=>f.slots[k]?.status==='locked'))return stage;
 return null;
}
export function pairExplanation(result){
 if(!result)return '';
 const d=result.detail||{},signed=n=>Number(n)>0?'+'+n:String(n).replace('-','−');
 return esc(tr(result.reason))+(result.reason==='thắng nhanh hơn'?` (${esc(d.winner_plies)} / ${esc(d.loser_plies)} ${tr('nước')})`:result.reason==='hơn hiệu số quân'?` (${esc(signed(d.winner_diff))} / ${esc(signed(d.loser_diff))})`:'');
}
export function finalPairDetail(f,key){
 const slot=f.slots[key],name=id=>f.teams.find(t=>t.team_id===id)?.name||id;
 return `<div class="modal-head"><h2>${esc(tr(names[key]))} · ${slot.team_ids.map(t=>esc(name(t))).join(' – ')}</h2><button type="button" class="btn small" data-action="close-final-pair">${tr('Đóng')}</button></div><div class="modal-body"><div class="table-scroll"><table><thead><tr>${['Ván','Đỏ','Xanh','Kết quả','Số nước','Quân còn Đỏ–Xanh',''].map(t=>`<th>${esc(tr(t))}</th>`).join('')}</tr></thead><tbody>${(slot.games||[]).map(g=>`<tr><td>${esc(g.ordinal)}</td><td>${esc(name(g.red_team_id))}</td><td>${esc(name(g.blue_team_id))}</td><td>${esc(resultLabel(g,side=>name(g[side+'_team_id'])))}</td><td>${esc(g.plies)}</td><td>${esc(g.pieces.red)}–${esc(g.pieces.blue)}</td><td><a class="text-link" href="#/replay/${esc(slot.competition_id)}/${esc(g.fixture_id)}">${tr('Xem replay')}</a></td></tr>`).join('')}</tbody></table></div><p><strong>${esc(name(slot.result.winner))}</strong> ${tr('đi tiếp: ')}${pairExplanation(slot.result)}.</p><a class="text-link" href="#/competitions/${esc(slot.competition_id)}">${tr('Mở trang cặp đấu')}</a></div>`;
}
export function finalsPage(s){
 const f=s.final,projector=s.query?.get('chieu')==='1';
 if(!f)return head(tr('Chung kết'),'',s.user?.role==='admin'?button(tr('Tạo chung kết 16 đội'),'finals-create','','primary'):'')+finalsCards(s)+(s.finals?.length?'':empty(tr('Chưa có chung kết'),tr('16 đội · 4 bảng → loại trực tiếp · 2 ván đổi màu mỗi cặp')));
 const admin=s.user?.role==='admin'&&!s.importedFinal,byTeam=new Map(f.teams.map(t=>[t.team_id,t]));
 const actions=admin?[(f.status==='draft'?button(tr('Bắt đầu vòng bảng'),'finals-command','data-command="start-groups"','primary'):''),
  f.pause_between_rounds&&waitingStage(f)?button(tr('Bắt đầu vòng'),'finals-command','data-command="start-round"','primary'):'',
  ['draft','groups','knockout'].includes(f.status)?button(tr('Dừng tự động'),'finals-command','data-command="stop"','danger-button'):''].join(''):'';
 const group=key=>{const slot=f.slots[key];return `<article class="finals-group"><h3>${esc(tr(names[key]))}<small>${esc(slot?.counts?.completed||0)}/${esc(Object.values(slot?.counts||{}).reduce((a,b)=>a+b,0))} ${tr('ván')}</small></h3>${slot?`<table><thead><tr><th>#</th><th>${tr('Đội')}</th><th>${tr('T-H-B')}</th><th>${tr('Điểm')}</th></tr></thead><tbody>${slot.rows.map(r=>`<tr class="${r.rank<=2?'group-qualified':''}"><td>${esc(r.rank)}</td><td>${esc(r.name)}</td><td class="group-record">${esc(r.wins??0)}-${esc(r.draws??0)}-${esc(r.losses??0)}</td><td><strong>${esc(r.points)}</strong></td></tr>`).join('')}</tbody></table><p class="group-legend">${tr('▌ Vào tứ kết')}</p>${projector?'':`<a class="text-link" href="#/competitions/${esc(slot.competition_id)}">${tr('Lịch & replay ›')}</a>`}`:empty(tr('Chưa bắt đầu'))}</article>`;};
 const seeds={TK1:['1A','2B'],TK2:['1C','2D'],TK3:['1B','2A'],TK4:['1D','2C']};
 const sources={BK1:['TK1','TK2'],BK2:['TK3','TK4'],FINAL:['BK1','BK2'],BRONZE:['BK1','BK2']};
 const pair=key=>{
  const slot=f.slots[key],done=!!slot?.result,playing=['running','paused'].includes(slot?.status),clickable=!projector&&(done||playing),tag=clickable?(done?'button':'a'):'div';
  const source=slot?.source||sources[key]||[],waiting=source.length?tr(key==='BRONZE'?'Chờ đội thua ':'Chờ đội thắng ')+source.map(x=>tr(names[x])).join(tr(' và ')):tr('Chờ vòng trước');
  const score=slot?.team_ids.map(t=>slot.rows.find(r=>r.team_id===t)?.points??0).join('–');
  const foot=done?(slot.result.reason==='hơn điểm'?tr('2 ván'):esc(score)+' · '+pairExplanation(slot.result))+' · '+tr('Xem replay ›'):playing?tr('Đang đấu · Xem trực tiếp ›'):esc(waiting);
  return `<${tag} class="finals-pair ${playing?'is-running':''}" data-slot="${key}" ${clickable?(done?`type="button" data-action="final-pair" data-key="${key}"`:`href="#/competitions/${esc(slot.competition_id)}"`):''}><span class="pair-heading"><span>${esc(tr(names[key]))}</span><small>${done?tr('Xong'):playing?tr('Đang đấu'):tr('Chờ')}</small></span>${(slot?.team_ids||['','']).map((t,i)=>`<span class="pair-team ${slot?.result?(slot.result.winner===t?'pair-winner':'pair-loser'):''}"><span>${seeds[key]?`<small class="pair-seed">${seeds[key][i]}</small>`:''}${esc(byTeam.get(t)?.name||'—')}</span><strong>${esc(slot?.rows.find(r=>r.team_id===t)?.points??'—')}</strong></span>`).join('')}<span class="pair-foot">${foot}</span></${tag}>`;
 };
 const podium=f.podium.length?`<div class="finals-podium">${f.podium.map((tid,i)=>`<div class="podium-place podium-${i}"><span>${i===0?'🏆 ':''}${tr(['Vô địch','Á quân','Hạng ba'][i])}</span><strong>${esc(byTeam.get(tid)?.name||tid)}</strong></div>`).join('')}</div>`:'';
 const groups=`<section class="finals-group-section"><h2>${tr('Vòng bảng')}</h2><div class="finals-groups">${['A','B','C','D'].map(group).join('')}</div></section>`;
 const bracket=`<section class="finals-bracket-section"><h2>${tr('Nhánh đấu')}</h2><div class="finals-bracket">${stages.map(([,name,keys],i)=>`<section class="finals-round"><h3>${esc(tr(name))}</h3><div class="round-slots">${keys.map(pair).join('')}${i===2?pair('BRONZE'):''}${i<2?'<i class="bracket-join join-first" aria-hidden="true"></i>'+(i===0?'<i class="bracket-join join-second" aria-hidden="true"></i>':''):''}</div></section>`).join('')}</div></section>`;
 const rules=`<details class="finals-rule-details"><summary>${tr('Thể lệ chung kết')}</summary><p>${tr('Chia 4 bảng theo con rắn từ hạng online. Điểm → điểm đối đầu → số ván thắng → hạng online. Hai đội đầu mỗi bảng vào tứ kết.')}</p><p>${tr('Mỗi cặp 2 ván đổi màu. Hòa điểm: mỗi đội thắng 1 ván thì xét số nước ván thắng ít hơn → hiệu số quân cộng 2 ván → bốc thăm cố định. Chỉ dùng 2 ván của cặp, không dùng vòng bảng hay hạng online.')}</p><p>${tr('Một bản bot cả ngày, ghim lúc bắt đầu bảng. Dừng tự động giữ nguyên các giải con để BTC làm tiếp bằng tay.')}</p></details>`;
 const content=(projector?`<a class="projection-exit" href="#/finals/${esc(f.id)}">${tr('× Thoát chiếu')}</a>`:(s.importedFinal?importedBanner(s.importedFinal):'')+backLink('competitions',tr('Giải đấu')))+head(f.name,tr(states[f.status]||f.status)+' · '+tr('16 đội · 4 bảng → loại trực tiếp')+' · '+timeLabel(f.profile).replace(tr(' cho cả ván'),''),projector?'':actions+(admin?link('finals/'+f.id+'?chieu=1',tr('Màn hình chiếu')):''))+(f.error?note(f.error):'');
 return `<div class="finals-view">${content}<div class="finals-layout">${projector?groups+`<div class="finals-main">${podium}${bracket}</div>`:podium+(f.status==='groups'?groups+bracket:bracket+groups)}</div>${projector?'':rules+'<dialog id="final-pair-dialog" class="final-pair-dialog"></dialog>'}</div>`;
}
