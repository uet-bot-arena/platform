import {tr} from './i18n.js';
import {esc,head,panel,link,button,field,select,note,errors,submit,badge,empty} from './view.js';
import {timeField,timeLabel} from './time-control.js';
import {rulesetField} from './experimental-ui.js';
import {importedBanner} from './imported-results.js';

const stages=[['quarter','Tứ kết',['TK1','TK2','TK3','TK4']],['semi','Bán kết',['BK1','BK2']],['medals','Chung kết',['FINAL']]];
const states={draft:'Bản nháp',groups:'Vòng bảng',knockout:'Loại trực tiếp',completed:'Hoàn tất',stopped:'Đã dừng tự động'};
const names={A:'Bảng A',B:'Bảng B',C:'Bảng C',D:'Bảng D',TK1:'Tứ kết 1',TK2:'Tứ kết 2',TK3:'Tứ kết 3',TK4:'Tứ kết 4',BK1:'Bán kết 1',BK2:'Bán kết 2',FINAL:'Chung kết',BRONZE:'Tranh hạng ba'};
const groupRule='Điểm → điểm đối đầu → số ván thắng → hạng online';
const pairRule='Hòa cặp: vị trí bảng → điểm bảng → hạng online';
export function finalsCards(s){
 const items=s.finals||[];
 return items.length?panel(tr('Chung kết'),`<div class="real-card-grid">${items.map(f=>`<article class="real-card"><h3>${link('finals/'+f.id,f.name)}</h3>${badge(f.status,tr(states[f.status]||f.status))}</article>`).join('')}</div>`):'';
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
export function finalsPage(s){
 const f=s.final,projector=s.query?.get('chieu')==='1';
 if(!f)return head(tr('Chung kết'))+(s.user?.role==='admin'?finalsForm(s):'')+finalsCards(s)+(s.finals?.length?'':empty(tr('Chưa có chung kết')));
 const admin=s.user?.role==='admin'&&!s.importedFinal,byTeam=new Map(f.teams.map(t=>[t.team_id,t]));
 const actions=admin?[(f.status==='draft'?button(tr('Bắt đầu vòng bảng'),'finals-command','data-command="start-groups"','primary'):''),
  f.pause_between_rounds&&waitingStage(f)?button(tr('Bắt đầu vòng'),'finals-command','data-command="start-round"','primary'):'',
  ['draft','groups','knockout'].includes(f.status)?button(tr('Dừng tự động'),'finals-command','data-command="stop"','danger-button'):''].join(''):'';
 const group=key=>{const slot=f.slots[key];return `<article class="finals-group"><h2>${esc(tr(names[key]))}${slot?link('competitions/'+slot.competition_id,tr('Lịch / replay'),'small'):''}</h2>${slot?`<ol>${slot.rows.map(r=>`<li><span>${esc(r.rank)}. ${esc(r.name)}${slot.status==='completed'&&r.rank<=2?` <small>${tr('Vào tứ kết')}</small>`:''}</span><strong>${esc(r.points)}</strong></li>`).join('')}</ol>${badge(slot.status)}`:empty(tr('Chưa bắt đầu'))}</article>`;};
 const pair=key=>{const slot=f.slots[key];return `<article class="finals-pair ${slot?.status==='running'?'is-running':''}"><h3>${esc(tr(names[key]))}${slot?badge(slot.status):''}</h3>${slot?`<a href="#/competitions/${esc(slot.competition_id)}" aria-label="${esc(tr(names[key])+' · '+tr('Lịch / replay'))}">${slot.team_ids.map(t=>`<div class="pair-team ${slot.result?.winner===t?'pair-winner':''}"><span>${esc(byTeam.get(t)?.name||t)}</span><strong>${esc(slot.rows.find(r=>r.team_id===t)?.points??'—')}</strong></div>`).join('')}</a>${slot.result?`<p>${esc(tr(slot.result.reason))}</p>`:''}`:note(tr('Chờ vòng trước'))}</article>`;};
 const podium=f.podium.length?`<div class="finals-podium">${f.podium.map((tid,i)=>`<span><strong>${tr(['Vô địch','Á quân','Hạng ba'][i])}</strong> ${esc(byTeam.get(tid)?.name||tid)}</span>`).join('')}</div>`:'';
 const content=(s.importedFinal&&!projector?importedBanner(s.importedFinal):'')+head(f.name,tr(states[f.status]||f.status)+' · '+timeLabel(f.profile),projector?'':actions+link('finals/'+f.id+'?chieu=1',tr('Màn hình chiếu')))+podium+
  `<div class="finals-rules"><span>${esc(tr(groupRule))}</span><span>${esc(tr(pairRule))}</span></div>`+(f.error?note(f.error):'')+
  `<div class="finals-groups">${['A','B','C','D'].map(group).join('')}</div><div class="finals-bracket">${stages.map(([,name,keys],i)=>`<section class="finals-round"><h2>${esc(tr(name))}</h2><div>${keys.map(pair).join('')}${i<2?`<svg class="finals-connectors" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${i===0?'M0 12.5H50V25H100 M0 37.5H50V25 M0 62.5H50V75H100 M0 87.5H50V75':'M0 25H50V50H100 M0 75H50V50'}"/></svg>`:''}</div></section>`).join('')}</div><div class="finals-bronze">${pair('BRONZE')}</div>`+
  (!projector?note(tr('Một bản bot cả ngày, ghim lúc bắt đầu bảng. Dừng tự động giữ nguyên các giải con để BTC làm tiếp bằng tay.')):'');
 return `<div class="finals-view">${content}</div>`;
}
