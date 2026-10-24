import {visiblePoll} from './polling.js';
import {tr,html as l10n} from './i18n.js';
import {esc,button,head,panel,note,badge,link,icon} from './view.js';
import {resultLabel,sideName} from './result.js';
import {remainingClocks,clockText,timeLabel} from './time-control.js';
const labels={rock:tr('Búa'),paper:tr('Lá'),scissors:tr('Kéo')};
const piecePaths={
  rock:'<g transform="rotate(-35 12 12)"><rect x="4" y="3.5" width="16" height="7" rx="1.5"/><path d="M10 10.5V20a2 2 0 0 0 4 0v-9.5"/></g>',
  paper:'<path d="M20 3C12 3 4 6 4 13a7 7 0 0 0 7 7c7 0 9-9 9-17Z"/><path d="m4 21 12-12"/>',
  scissors:'<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8.2 8.2 12 12m-12-4.4 12-12"/>'
};
export const pieceIcon=kind=>`<svg class="piece-symbol" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${piecePaths[kind]||''}</svg>`;

const samePiece=(a,b)=>a?.side===b?.side&&a?.kind===b?.kind;
const square=(r,c,rows=9)=>String.fromCharCode(97+c)+(rows-r);
export function describeTransition(previous,state) {
  if(!previous)return {type:state.result?'end':state.ply===0?'start':'snapshot',ply:state.ply};
  const changed=[];
  for(let r=0;r<state.board.length;r++)for(let c=0;c<state.board[r].length;c++){
    if(!samePiece(previous.board[r][c],state.board[r][c]))changed.push({r,c,before:previous.board[r][c],after:state.board[r][c]});
  }
  // A result-only snapshot (timeout, resignation, startup failure, wall limit)
  // has no move. Never invent an action or name the next side as the mover.
  if(state.ply===previous.ply+1&&changed.length===2){
    const from=changed.find(x=>x.before?.side===previous.to_move&&!x.after);
    const to=changed.find(x=>x.after?.side===previous.to_move);
    if(from&&to&&samePiece(from.before,to.after)&&(!to.before||to.before.side!==previous.to_move)){
      return {type:'move',ply:state.ply,side:from.before.side,kind:from.before.kind,
        from:square(from.r,from.c,state.board.length),to:square(to.r,to.c,state.board.length),captured:to.before?{...to.before}:null};
    }
  }
  return {type:state.result?'end':'snapshot',ply:state.ply};
}
const teamLabel=(side,names)=>`${names[side]||sideName(side)} (${sideName(side)})`;
export function moveDescription(move,state,names={}) {
  if(move.type==='ability')return l10n`Nước ${move.ply} · ${teamLabel(move.side,names)} · Khiên cho ${labels[move.kind]} tại ${move.square}`+(state.result?' · '+resultLabel(state.result,side=>teamLabel(side,names)):'');
  if(move.type==='move')return l10n`Nước ${move.ply} · ${teamLabel(move.side,names)} · ${labels[move.kind]} ${move.from} → ${move.to}`+
    (move.ability?' · '+move.ability:'')+(move.blocked?tr(' · Khiên chặn; quân tấn công ở nguyên ô'):'')+
    (move.captured?l10n` · ăn ${labels[move.captured.kind]} của ${teamLabel(move.captured.side,names)}`:'')+
    (state.result?' · '+resultLabel(state.result,side=>teamLabel(side,names)):'');
  if(move.type==='start')return l10n`Bắt đầu · ${teamLabel(state.to_move,names)} đi trước`;
  if(move.type==='end')return tr('Kết thúc · ')+resultLabel(state.result,side=>teamLabel(side,names));
  return tr('Trạng thái tại nước ')+move.ply;
}
export function moveMarkup(move,state,names={}) {
  if(!['move','ability'].includes(move.type))return `<span class="move-step">${move.type==='start'?'○':move.type==='end'?'■':'#'+esc(move.ply)}</span><span class="move-content"><strong class="move-event-title">${move.type==='start'?tr('Thế cờ ban đầu'):move.type==='end'?tr('Kết thúc trận'):tr('Trạng thái bàn cờ')}</strong><span class="move-event-detail">${esc(moveDescription(move,state,names))}</span></span>`;
  const capture=move.captured;
  return `<span class="move-step">#${esc(move.ply)}</span><span class="move-content"><span class="move-team"><i class="side-dot ${esc(move.side)}" aria-hidden="true"></i><strong title="${esc(names[move.side]||sideName(move.side))}">${esc(names[move.side]||sideName(move.side))}</strong><small>${sideName(move.side)}</small></span><span class="move-action"><span class="log-piece ${esc(move.side)}">${pieceIcon(move.kind)}</span><strong>${labels[move.kind]}</strong><span class="move-squares">${move.type==='ability'?tr('Khiên · ')+esc(move.square):esc(move.from)+' <span aria-hidden="true">→</span> '+esc(move.to)}</span>${move.ability?`<span class="ability-tag">${esc(move.ability)}</span>`:''}${move.blocked?tr('<span class="ability-tag">Khiên chặn</span>'):''}</span>${capture?l10n`<span class="move-capture" title="${esc(labels[move.kind]+tr(' ăn ')+labels[capture.kind]+tr(' tại ')+move.to)}"><span class="capture-symbol" aria-hidden="true">×</span><span class="log-piece ${esc(capture.side)}">${pieceIcon(capture.kind)}</span><span>Ăn ${labels[capture.kind]} <small>· ${sideName(capture.side)}</small></span></span>`:''}${move.effects?.length?`<span class="move-effects">${move.effects.map(e=>esc((e.side==='red'?tr('Đỏ'):tr('Xanh'))+' '+(e.type==='energy_gained'?tr('Năng lượng'):tr('Kiểm soát'))+' +'+e.amount+tr(' tại ')+e.square)).join(' · ')}</span>`:''}${state.result?`<span class="move-outcome">${esc(resultLabel(state.result,side=>names[side]||sideName(side)))}</span>`:''}</span>`;
}
export function inventoryMarkup(side) {
  return Object.entries(labels).map(([kind,label])=>l10n`<span class="inventory-item" data-kind="${kind}" role="img" aria-label="${label}: chờ trạng thái" title="${label}: chờ trạng thái"><span class="inventory-piece ${side}">${pieceIcon(kind)}<strong class="inventory-count" data-count="${kind}">—</strong></span><span class="inventory-label" aria-hidden="true">${label}</span></span>`).join('');
}
export function countPieces(state) {
  const counts={red:{rock:0,paper:0,scissors:0},blue:{rock:0,paper:0,scissors:0}};
  for(const row of state?.board||[])for(const piece of row)if(piece&&counts[piece.side]?.[piece.kind]!==undefined)counts[piece.side][piece.kind]++;
  return counts;
}
export function boardPage({title,red,blue,back,admin=false,officialLive=false,profile,basicRules}) {
  const rows=basicRules?.rows||9,cols=basicRules?.cols||9,bases=basicRules?.bases||{red:"a1",blue:"i9"};
  const follow=officialLive?l10n`<section class="panel competition-follow"><label class="check-field"><input type="checkbox" id="follow-competition" checked> Tự chuyển trận</label><span id="competition-follow-message" role="status">Đang kết nối tiến độ giải…</span><div class="match-controls"><a id="competition-next" class="btn secondary" hidden>Trận tiếp theo</a><a id="competition-review" class="btn secondary">Xem lại trận này</a></div></section>`:'';
  return head(title,profile?timeLabel(profile):tr('Theo dõi bàn cờ và xem lại từng nước đi.'),link(back,tr('Quay lại')))+l10n`<div id="rule-banner" class="rule-banner" hidden></div><div class="match-layout"><section class="panel"><div class="match-players">${[['red',red],['blue',blue]].map(([side,name])=>l10n`<div class="match-player ${side}" id="player-${side}"><small>${sideName(side)}${side==='red'?tr(' · Đi trước'):''}</small><strong>${esc(name)}</strong><span class="clock" id="clock-${side}">—</span><div id="counts-${side}" class="piece-inventory" role="group" aria-label="Quân còn lại của ${sideName(side)}">${inventoryMarkup(side)}</div><div class="rule-resources" id="resources-${side}" hidden><span data-resource="energy"></span><span data-resource="control"></span></div></div>`).join('')}</div><div id="match-result" class="match-result" role="status">Đang chờ máy chạy bot…</div><div class="board-wrap" data-rows="${rows}" data-cols="${cols}"><div id="live-board" data-view-id="${crypto.randomUUID()}" class="live-board" role="img" aria-label="Bàn cờ OTTv2 ${rows} nhân ${cols}">${Array.from({length:rows*cols},(_,i)=>{const r=Math.floor(i/cols),c=i%cols,sq=square(r,c,rows);return `<div class="board-square ${(r+c)%2?'dark':''} ${sq===bases.red?'base-red':sq===bases.blue?'base-blue':''}" data-square="${sq}"><span class="board-piece"></span>${sq===bases.red||sq===bases.blue?`<span class="base-marker" aria-hidden="true">${icon('home')}</span>`:''}<span class="zone-marker" aria-hidden="true"></span></div>`;}).join('')}</div><div class="board-files">${Array.from({length:cols},(_,i)=>String.fromCharCode(97+i)).map(v=>`<span>${v}</span>`).join('')}</div><div class="board-ranks">${Array.from({length:rows},(_,i)=>rows-i).map(v=>`<span>${v}</span>`).join('')}</div></div><div class="board-legend">${Object.entries(labels).map(([kind,label])=>`<span>${pieceIcon(kind)}${label}</span>`).join('')}<span>${icon('home')}Căn cứ</span></div><div class="match-caption"><strong id="move-caption">Chưa có nước đi</strong><label><input type="checkbox" id="follow-live" checked> Theo dõi mới nhất</label></div><input type="range" class="replay-slider" id="replay-slider" min="0" max="0" value="0" aria-label="Chọn nước đi replay" disabled><div class="match-controls">${button(tr('Đầu trận'),'board-first')}${button(tr('Trước'),'board-prev')}${button(tr('Phát replay'),'board-play')}${button(tr('Sau'),'board-next')}${button(tr('Mới nhất'),'board-last')}${button(tr('Xoay bàn'),'board-flip')}</div><span id="stream-message" class="stream-message" role="status">Đang tải diễn biến…</span><p id="clock-note" class="match-meta">Đang đồng bộ đồng hồ…</p></section><aside class="match-side">${follow}${panel(tr('Diễn biến'),`<div id="move-list" class="move-list"></div>`)}${panel(tr('Thông tin trận'),`<div id="match-status">${badge('queued')}</div>${note(tr('Hạn chót mỗi nước do đồng hồ của máy chạy bot quyết định. Bot hết quỹ giờ thì sau 3 giây server tuyên bố thua; nước gửi muộn không được nhận.'))}${admin?button(tr('Lịch sử & lỗi worker'),'diagnostic'):''}`)}</aside></div>`;
}

// Event snapshots are immutable. The board square elements remain mounted for the
// lifetime of the view; only each square's piece/label/highlight is patched.
export class MatchBoard {
  constructor(api,{path,official=false,terminal=false,initialHistory=null,initialClock=null,onExpired,onError,onReview}) {
    Object.assign(this,{api,path,official,terminal,initialHistory,onExpired,onError,onReview});
    this.states=[];this.index=-1;this.seq=0;this.attempt=undefined;this.follow=true;this.disposed=false;
    this.cells=[...document.querySelectorAll('#live-board .board-square')];
    this.$=id=>document.getElementById(id);
    this.listeners=new AbortController();
    this.$('replay-slider').addEventListener('input',e=>this.seek(Number(e.target.value)),{signal:this.listeners.signal});
    this.$('follow-live').addEventListener('change',e=>{this.follow=e.target.checked;if(this.follow)this.show(this.states.length-1);else this.onReview?.();},{signal:this.listeners.signal});
    this.tick=setInterval(()=>this.clock(),100);
    if(initialClock)this.clockMap=initialClock;else if(!terminal)this.syncClock();
    this.resync=visiblePoll(()=>this.terminal?Promise.resolve():this.syncClock(),30000);
  }
  async start(){try{await this.history();if(!this.disposed&&!this.terminal)this.connect();}catch(e){if(!this.disposed){this.message(e.message);this.onError?.(e);}}}
  message(text){if(!this.disposed)this.$('stream-message').textContent=text;}
  async syncClock(){
    let best;
    try{for(let i=0;i<1;i++){const a=performance.now(),t=await this.api.request('/time'),b=performance.now(),rtt=b-a;if(!best||rtt<best.rtt)best={rtt,anchor:(a+b)/2,unix:(t.server_received_unix_ms+t.server_sent_unix_ms)/2,clockId:t.clock_id};}
      if(this.disposed)return;this.clockMap=best;this.clock();
    }catch{if(!this.disposed)this.$('clock-note').textContent=tr('Chưa đồng bộ đồng hồ hiển thị; worker vẫn quyết định thời hạn mỗi lượt.');}
  }
  reset(attempt){
    this.stopPlay();this.attempt=attempt;this.seq=0;this.states=[];this.index=-1;this.turn=null;this.pendingClocks=null;this.result=null;this.terminal=false;this.follow=true;
    this.$('move-list').replaceChildren();this.paint(null);
    Object.assign(this.$('replay-slider'),{disabled:true,max:'0',value:'0'});
    this.$('follow-live').checked=true;this.$('move-caption').textContent=tr('Chưa có nước đi');
    this.$('match-status').innerHTML=badge('queued');this.$('match-result').textContent=tr('Đang chờ máy chạy bot…');
  }
  async history(){
    // The official cursor also advances over private events filtered by API.
    for(let pages=0;pages<20;pages++){
      const requested=this.seq,preloaded=this.initialHistory,h=preloaded||await this.api.request(this.path+'?after='+requested);this.initialHistory=null;if(this.disposed)return;
      if(this.official&&h.attempt_id!==this.attempt){this.reset(h.attempt_id);if(requested>0)continue;}
      if(h.replay_pruned){this.terminal=true;this.$('match-status').innerHTML=badge('completed');this.$('match-result').textContent=tr('Kết quả vẫn được giữ trong lịch đấu.');this.message(tr('Replay đã quá hạn lưu; hub giữ các ván ladder và luyện tập gần nhất của mỗi đội.'));return;}
      const before=this.seq;
      for(const event of h.items)this.consume(event);
      this.seq=Math.max(this.seq,h.next_after||0);
      if(preloaded)return;
      if(this.seq===before||(!this.official&&h.items.length<1000))break;
    }
    this.message(this.terminal?tr('Đã tải diễn biến. Bạn có thể xem lại từng nước.'):tr('Đang theo dõi nước đi mới.'));
  }
  connect(){
    if(this.disposed||this.terminal)return;
    this.feed?.close();this.feed=this.api.stream(this.path+'?after='+this.seq);
    this.feed.addEventListener('attempt',e=>{if(this.disposed)return;const {attempt_id}=JSON.parse(e.data);if(attempt_id!==this.attempt)this.reset(attempt_id);});
    this.feed.addEventListener('attempt_changed',()=>{if(this.disposed)return;this.feed.close();this.reset(undefined);this.history().then(()=>this.connect()).catch(e=>{if(!this.disposed)this.onError?.(e);});});
    this.feed.addEventListener('arena',e=>{if(!this.disposed)this.consume(JSON.parse(e.data));});
    this.feed.addEventListener('auth_expired',()=>{this.close();this.onExpired?.();});
    this.feed.addEventListener('unavailable',()=>{this.message(tr('Trận này không còn được công khai.'));this.close();});
    this.feed.onopen=()=>this.message(tr('Đang nhận sự kiện trực tiếp từ server.'));
    this.feed.onerror=()=>{if(!this.disposed&&!this.terminal)this.message(tr('Đang nối lại luồng sự kiện… Replay hiện tại vẫn được giữ.'));};
  }
  markPlaying(){
    // Public feeds hide worker phase events; the first board/turn event means the match is on.
    const label=this.$('match-result');
    if(!this.result&&!this.terminal){
      if(label?.textContent===tr('Đang chờ máy chạy bot…'))label.textContent=tr('Đang đấu');
      this.$('match-status').innerHTML=badge('playing');
    }
  }
  consume(event){
    if(this.disposed||event.seq<=this.seq)return;this.seq=event.seq;
    const d=event.data||{};
    if(event.type==='state'){
      this.turn=null;this.pendingClocks=null;const index=this.states.length,names=this.teamNames(),trace=d.events?.find(e=>e.type==='move'||e.type==='ability_used');
      const move=trace?{...trace,type:trace.type==='move'?'move':'ability',ply:d.state.ply,effects:d.events.filter(e=>['energy_gained','control_gained'].includes(e.type))}:describeTransition(this.states.at(-1)?.state,d.state);
      this.states.push({state:d.state,counts:d.counts||countPieces(d.state),move,ruleset:d.ruleset,clocks:d.clocks});
      const b=document.createElement('button');b.type='button';b.className='move-entry '+(move.side||'neutral');b.dataset.action='board-seek';b.dataset.index=String(index);
      b.innerHTML=moveMarkup(move,d.state,names);b.setAttribute('aria-label',moveDescription(move,d.state,names));this.$('move-list').append(b);
      this.$('replay-slider').max=String(index);this.$('replay-slider').disabled=false;
      if(this.follow||this.index<0)this.show(index);
      if(d.state.result)this.result=d.state.result;
      else this.markPlaying();
    }else if(event.type==='turn_started'){this.turn=d;this.$('match-status').innerHTML=badge('playing');this.markPlaying();this.clock();}
    else if(event.type==='timeout_pending'){this.turn=null;this.pendingClocks=d.clocks;this.$('match-result').textContent=l10n`${sideName(d.side)} đã hết giờ · Chờ server tuyên bố sau ${d.grace_ms/1000}s`;this.clock();}
    else if(event.type==='phase')this.$('match-status').innerHTML=badge(d.phase);
    else if(['completed','failed','aborted'].includes(event.type)){
      this.terminal=true;this.turn=null;this.feed?.close();this.result=d.result||this.result;
      this.$('match-status').innerHTML=badge(event.type);
      this.$('match-result').textContent=this.result?resultLabel(this.result):event.type==='aborted'?tr('Trận đã dừng · Không tính điểm'):event.type==='failed'?tr('Tác vụ lỗi · Không tính điểm'):tr('Trận hoàn tất');
      this.message(tr('Đã nhận kết quả từ server · Có thể xem replay.'));this.clock();
    }
  }
  teamNames(){return Object.fromEntries(['red','blue'].map(side=>[side,this.$('player-'+side).querySelector(':scope > strong').textContent]));}
  paint(snapshot){
    const state=snapshot?.state,previous=this.states[this.index-1]?.state,pack=snapshot?.ruleset,zones=pack?.definition.zones||[];
    const banner=this.$('rule-banner');if(banner){banner.hidden=!pack;banner.textContent=pack?'Experimental · '+pack.definition.name+' · v'+pack.version+' · '+pack.hash.slice(0,12):'';}

    this.cells.forEach((cell,i)=>{const r=state?state.board.length-Number(cell.dataset.square.slice(1)):0,c=cell.dataset.square.charCodeAt(0)-97,p=state?.board[r]?.[c],span=cell.firstElementChild,was=previous?.board[r][c];
      const shielded=p?.shield_until>(state?.turns?.[p?.side]??0);
      const pieceClass='board-piece'+(p?' '+p.side:'')+(shielded?' shielded':'');if(span.className!==pieceClass)span.className=pieceClass;
      const kind=p?.kind||'';if((span.dataset.kind||'')!==kind){span.innerHTML=p?pieceIcon(kind):'';span.dataset.kind=kind;}
      const zone=zones.find(z=>z.square===cell.dataset.square);cell.classList.toggle('zone-energy',zone?.kind==='energy');cell.classList.toggle('zone-control',zone?.kind==='control');
      const marker=cell.querySelector('.zone-marker'),zoneKind=zone?.kind||'';if(marker.dataset.kind!==zoneKind){marker.innerHTML=zoneKind?icon(zoneKind==='energy'?'bolt':'target'):'';marker.dataset.kind=zoneKind;}
      cell.classList.toggle('changed',!!state&&!!previous&&JSON.stringify(p)!==JSON.stringify(was));
      cell.setAttribute('aria-label',cell.dataset.square+(p?' · '+sideName(p.side)+' · '+labels[p.kind]:tr(' · Trống'))+(zone?tr(' · Vùng ')+(zone.kind==='energy'?tr('năng lượng'):tr('kiểm soát')):'')+(shielded?tr(' · Có Khiên'):''));cell.title=cell.getAttribute('aria-label')+(p?.cooldowns?' · '+Object.entries(p.cooldowns).map(([id,until])=>id+tr(': chờ ')+Math.max(0,until-state.turns[p.side])+tr(' lượt')).join(', '):'');
    });
    for(const side of ['red','blue']){
      const cnt=snapshot?.counts?.[side],group=this.$('counts-'+side),resources=this.$('resources-'+side);
      if(resources){resources.hidden=!pack;resources.querySelector('[data-resource=energy]').innerHTML=pack?icon('bolt')+tr('<span>Năng lượng <strong>')+esc(state.energy[side])+'/'+esc(pack.definition.energy.cap)+'</strong></span>':'';resources.querySelector('[data-resource=control]').innerHTML=pack?icon('target')+tr('<span>Kiểm soát <strong>')+esc(state.control[side])+(pack.definition.victory.control_target?'/'+esc(pack.definition.victory.control_target):'')+'</strong></span>':'';}

      for(const [kind,label] of Object.entries(labels)){
        const item=group.querySelector('[data-kind="'+kind+'"]'),number=item.querySelector('[data-count]'),value=cnt?.[kind];
        const text=Number.isInteger(value)&&value>=0?String(value):'—',description=label+': '+(text==='—'?tr('chờ trạng thái'):text+tr(' quân'));
        if(number.textContent!==text)number.textContent=text;
        item.classList.toggle('depleted',value===0);item.setAttribute('aria-label',description);item.title=description;
      }
    }
  }
  show(index){
    if(index<0||index>=this.states.length)return;this.index=index;this.paint(this.states[index]);this.$('replay-slider').value=String(index);
    this.$('move-caption').textContent=l10n`Nước ${this.states[index].state.ply} / ${this.states.at(-1).state.ply}`;
    this.$('move-list').querySelectorAll('button').forEach((b,i)=>{b.classList.toggle('current',i===index);b.setAttribute('aria-current',i===index?'step':'false');});
    this.$('follow-live').checked=this.follow;this.clock();
  }
  seek(index){this.onReview?.();this.follow=false;this.stopPlay();this.show(Math.max(0,Math.min(this.states.length-1,index)));}
  action(action,index){
    if(action==='seek')this.seek(Number(index));
    if(action==='first')this.seek(0);if(action==='prev')this.seek(this.index-1);if(action==='next')this.seek(this.index+1);
    if(action==='last'){this.stopPlay();this.follow=true;this.show(this.states.length-1);}
    if(action==='play'){this.onReview?.();if(this.play){this.stopPlay();return;}this.follow=false;if(this.index>=this.states.length-1)this.show(0);this.play=setInterval(()=>{if(this.index>=this.states.length-1)this.stopPlay();else this.show(this.index+1);},650);this.$('follow-live').checked=false;document.querySelector('[data-action="board-play"]').textContent=tr('Tạm dừng');}
    if(action==='flip'){const flipped=this.$('live-board').classList.toggle('flipped');this.$('live-board').setAttribute('aria-label',tr('Bàn cờ OTTv2 ')+(this.states[this.index]?.state.board.length||9)+tr(' nhân ')+(this.states[this.index]?.state.board[0]?.length||9)+(flipped?tr(' · Đã xoay'):''));document.querySelectorAll('.board-files span').forEach((e,i)=>e.textContent=String.fromCharCode(97+(flipped?document.querySelectorAll('.board-files span').length-1-i:i)));document.querySelectorAll('.board-ranks span').forEach((e,i)=>e.textContent=flipped?i+1:document.querySelectorAll('.board-ranks span').length-i);}
  }
  stopPlay(){clearInterval(this.play);this.play=null;const b=document.querySelector('[data-action="board-play"]');if(b)b.textContent=tr('Phát replay');}
  clock(){
    if(this.disposed)return;const current=this.states[this.index]?.state,live=this.follow&&!this.terminal;
    const now=this.clockMap?this.clockMap.unix+performance.now()-this.clockMap.anchor:NaN;
    const bank=remainingClocks(live&&(this.turn?.clocks||this.pendingClocks)||this.states[this.index]?.clocks,this.turn,now,live);
    for(const side of ['red','blue']){const active=live&&this.turn?.side===side,clock=this.$('clock-'+side);this.$('player-'+side).classList.toggle('active',current?.to_move===side&&!current.result);
      if(bank){clock.textContent=clockText(bank[side]);clock.setAttribute('aria-label',tr('Tổng thời gian còn lại: ')+clockText(bank[side]));}
      else if(active&&this.clockMap){const remaining=Math.max(0,this.turn.estimated_started_unix_ms+this.turn.budget_ms-now);clock.textContent=Number.isFinite(remaining)?(remaining/1000).toFixed(1)+'s':'—';}
      else clock.textContent=current?.result||this.terminal?tr('Kết thúc'):this.follow?'—':'Replay';
    }
    if(this.clockMap)this.$('clock-note').textContent=bank?tr('Thời gian còn lại cho cả trận · Chỉ trừ khi đến lượt · Không cộng giây.'):tr('Đồng hồ mỗi lượt của trận theo cấu hình cũ.');
  }
  close(){this.disposed=true;this.listeners.abort();this.feed?.close();clearInterval(this.tick);this.resync.close();this.stopPlay();}
}
