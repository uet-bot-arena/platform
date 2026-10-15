import {esc,table} from './view.js';

const outcomes=[['losses','Thua','loss'],['draws','Hòa','draw'],['wins','Thắng','win']];
const shortName=(name,length=26)=>{const chars=Array.from(name);return chars.length>length?chars.slice(0,length-1).join('')+'…':name;};
function scoreTable(items,ownTeam){
  return table(['Hạng','Đội','Phiên bản','Trận','Thua','Hòa','Thắng','Điểm'],items.map(team=>
    `<tr data-key="score-table-${esc(team.team_id)}"${team.team_id===ownTeam?' class="score-own"':''}><td>#${team.rank}</td><td><strong>${esc(team.name)}</strong>${team.team_id===ownTeam?'<small class="score-you">Đội bạn</small>':''}</td><td>${esc(team.version)}</td><td>${team.played}</td><td>${team.losses}</td><td>${team.draws||0}</td><td>${team.wins}</td><td><strong>${team.points}</strong></td></tr>`));
}
function pointAxis(maximum){
  const raw=Math.max(1,maximum)/4,power=10**Math.floor(Math.log10(raw));
  const step=Math.max(1,[1,2,5,10].find(value=>value*power>=raw)*power);
  const limit=Math.max(step,Math.ceil(maximum/step)*step);
  return {limit,ticks:Array.from({length:Math.round(limit/step)+1},(_,i)=>i*step)};
}
export function standingsChart(standings,ownTeam){
  const items=standings.items,scoring=standings.scoring;
  if(!scoring||items.some(team=>!Number.isFinite(team.points)))return '<p class="muted">Nhấn Cập nhật dữ liệu sau khi máy chủ cập nhật cách tính điểm.</p>';
  // API order and shared ranks are authoritative. The chart has exactly ten
  // slots, including when a shared rank crosses the tenth position.
  const leaders=items.slice(0,10),remaining=items.slice(10);
  if(!leaders.length)return '<p class="muted">Chưa có đội dự giải.</p>';
  const {limit,ticks}=pointAxis(Math.max(...leaders.map(team=>team.points)));
  const plotX=278,plotWidth=530,rowHeight=58,top=42,bottom=top+leaders.length*rowHeight,height=bottom+43;
  const legend=outcomes.map(([,label,kind])=>`<span><i class="score-dot score-${kind}"></i>${label} <strong>${scoring[kind]}đ</strong></span>`).join('');
  const grid=ticks.map(tick=>{const x=plotX+tick/limit*plotWidth;return `<g class="score-grid"><line x1="${x}" x2="${x}" y1="${top}" y2="${bottom}"/><text x="${x}" y="${bottom+25}" text-anchor="middle">${tick}</text></g>`;}).join('');
  const rows=leaders.map((team,index)=>{
    const id='score-'+team.team_id.replace(/[^A-Za-z0-9_-]/g,'-'),y=top+index*rowHeight;
    let offset=0;
    const segments=outcomes.map(([key,label,kind])=>{
      const points=team.point_breakdown?.[key]??(team[key]||0)*scoring[kind];
      const x=plotX+offset/limit*plotWidth;offset+=points;
      return points?`<rect class="score-${kind}" x="${x}" y="${y+15}" width="${points/limit*plotWidth}" height="22"><title>${esc(label)}: ${team[key]||0} trận · ${points} điểm</title></rect>`:'';
    }).join('');
    return `<g class="score-graph-row${team.team_id===ownTeam?' score-own':''}" data-key="score-${esc(team.team_id)}" aria-labelledby="${id}-title"><title id="${id}-title">Hạng ${team.rank} · ${esc(team.name)}: ${team.losses} thua, ${team.draws||0} hòa, ${team.wins} thắng; ${team.points} điểm.</title>${team.team_id===ownTeam?`<rect class="score-own-bg" x="0" y="${y}" width="900" height="${rowHeight}" rx="6"/>`:''}<line class="score-row-line" x1="0" x2="900" y1="${y+rowHeight}" y2="${y+rowHeight}"/><text class="score-rank" x="12" y="${y+29}">#${team.rank}</text><text class="score-name" x="60" y="${y+23}"><tspan class="score-name-wide">${esc(shortName(team.name))}</tspan><tspan class="score-name-compact">${esc(shortName(team.name,20))}</tspan></text><text class="score-record" x="60" y="${y+42}">${team.wins} thắng · ${team.draws||0} hòa · ${team.losses} thua${team.team_id===ownTeam?' · Đội bạn':''}</text>${segments}${!team.played?`<text class="score-unplayed" x="${plotX+10}" y="${y+31}">Chưa thi đấu</text>`:''}<text class="score-total" x="888" y="${y+32}" text-anchor="end">${team.points}</text></g>`;
  }).join('');
  return `<div class="score-rule"><div><span class="eyebrow">Cách tính điểm</span><p><strong>Điểm = ${scoring.win} × Thắng + ${scoring.draw} × Hòa</strong></p><small>Bằng tổng điểm thì đồng hạng. Thua ${scoring.loss} điểm.</small></div><div class="score-legend" aria-label="Chú giải kết quả">${legend}</div></div>
    <figure class="score-overview" aria-labelledby="score-overview-title"><figcaption class="score-caption"><div><h3 id="score-overview-title">Top 10 đội dẫn đầu</h3></div><span>${leaders.length} / ${items.length} đội</span></figcaption><p class="score-chart-note">Độ dài thanh thể hiện tổng điểm. Màu sắc chia phần điểm từ từng loại kết quả.</p><div class="score-graph-scroll" tabindex="0" role="region" aria-label="Biểu đồ tổng điểm top 10, có thể cuộn ngang trên màn hình nhỏ"><svg class="score-graph" viewBox="0 0 900 ${height}" role="img" aria-labelledby="score-graph-title score-graph-description"><title id="score-graph-title">Tổng điểm ${leaders.length} đội dẫn đầu</title><desc id="score-graph-description">Các đội theo thứ tự từ điểm cao đến thấp. Cùng trục điểm từ 0 đến ${limit}. Bằng điểm đồng hạng; tối đa 10 đội trên biểu đồ.</desc><text class="score-column" x="12" y="22">Hạng / đội</text><text class="score-column" x="${plotX}" y="22">Tổng điểm</text><text class="score-column" x="888" y="22" text-anchor="end">Điểm</text>${grid}${rows}</svg></div><p class="score-mobile-hint">Vuốt ngang để xem toàn bộ biểu đồ.</p></figure>
    <details class="score-data"><summary>Xem số liệu top ${leaders.length}</summary>${scoreTable(leaders,ownTeam)}</details>
    ${remaining.length?`<section class="score-rest" aria-labelledby="score-rest-title"><div class="score-rest-heading"><h3 id="score-rest-title">Các đội còn lại</h3><span>Từ vị trí 11 · Điểm giảm dần</span></div>${scoreTable(remaining,ownTeam)}</section>`:''}`;
}
