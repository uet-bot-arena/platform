// Watch only fixture metadata. The board keeps its own move/clock SSE stream.
export class CompetitionFollow {
  constructor(api,{competitionId,fixtureId,onSwitch,onExpired,onError,onState}) {
    Object.assign(this,{api,competitionId,fixtureId,onSwitch,onExpired,onError,onState});
    this.enabled=true;this.disposed=false;
    this.checkbox=document.getElementById('follow-competition');
    this.message=document.getElementById('competition-follow-message');
    this.next=document.getElementById('competition-next');
    this.review=document.getElementById('competition-review');
    this.change=()=>{this.enabled=this.checkbox.checked;this.update();};
    this.checkbox.addEventListener('change',this.change);
  }
  start(){
    this.feed=this.api.stream('/competitions/'+encodeURIComponent(this.competitionId)+'/watch');
    this.feed.addEventListener('competition',e=>{
      if(this.disposed)return;
      try{this.state=JSON.parse(e.data);this.onState?.(this.state);this.update();if(this.state.status==='completed')this.feed.close();}
      catch(error){this.onError?.(error);}
    });
    this.feed.addEventListener('auth_expired',()=>{this.close();this.onExpired?.();});
    this.feed.addEventListener('unavailable',()=>{this.message.textContent='Giải này không còn được công khai.';this.close();});
    this.feed.onerror=()=>{if(!this.disposed)this.message.textContent='Đang nối lại tiến độ giải · Bàn hiện tại vẫn được giữ.';};
  }
  pause(){if(this.disposed)return;this.enabled=false;this.checkbox.checked=false;this.update();}
  update(){
    if(this.disposed||!this.state)return;
    const current=this.state.fixtures.find(f=>f.id===this.fixtureId);
    if(!current)return;
    const later=this.state.fixtures.filter(f=>f.ordinal>current.ordinal);
    const next=later.find(f=>f.status==='queued')||later.find(f=>f.status==='pending')||later.filter(f=>f.status==='completed').at(-1);
    this.review.href='#/replay/'+this.competitionId+'/'+current.id;
    this.next.hidden=!next;
    if(next){this.next.href='#/arena/'+this.competitionId+'/'+next.id;this.next.textContent='Trận tiếp theo · #'+next.ordinal;}
    if(['completed','attention'].includes(current.status)&&['queued','completed'].includes(next?.status)&&this.enabled){
      const previous=this.fixtureId;this.fixtureId=next.id;
      this.onSwitch(next,previous);this.update();return;
    }
    this.message.textContent=!this.enabled?'Đã tắt tự chuyển trận · Giữ bàn hiện tại.':
      this.state.status==='completed'?'Giải đã hoàn tất · Có thể xem lại từng trận.':
      this.state.status==='paused'?'Giải đang tạm dừng · Chờ BTC tiếp tục.':
      this.state.status==='locked'?'Đã có lịch · Chờ BTC bắt đầu giải.':
      !next&&this.state.fixtures.some(f=>f.status==='attention')?'Chưa có trận tiếp theo · Có trận cần BTC xử lý.':
      ['completed','attention'].includes(current.status)?'Trận đã kết thúc · Chờ trận kế tiếp bắt đầu.':
      'Theo dõi giải · Tự chuyển khi trận kế tiếp bắt đầu.';
  }
  close(){this.disposed=true;this.feed?.close();this.checkbox.removeEventListener('change',this.change);}
}
