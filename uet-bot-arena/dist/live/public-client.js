// Guest views share cacheable files. Authenticated private streams keep SSE.
export function snapshotPath(path){
  const u=new URL(path,'file:///'),p=u.pathname;
  let m;
  if(p==='/competitions')return 'competitions.json';
  if(['/queue','/announcements'].includes(p))return p.slice(1)+'.json';
  if((m=p.match(/^\/competitions\/([\w-]+)$/)))return `competitions/${m[1]}.json`;
  if((m=p.match(/^\/competitions\/([\w-]+)\/standings$/)))return `standings/${m[1]}.json`;
  if((m=p.match(/^\/competitions\/([\w-]+)\/fixtures$/)))return `fixtures/${m[1]}.json`;
  if((m=p.match(/^\/competitions\/([\w-]+)\/fixtures\/([\w-]+)\/events$/)))return `fixtures/${m[1]}/${m[2]}.json`;
  return null;
}

export async function publicRequest(api,path){
  const u=new URL(path,'file:///'),mapped=snapshotPath(path);
  let value;
  if(u.pathname.endsWith('/watch')){
    const base=u.pathname.slice(0,-6);
    const [competition,fixtures]=await Promise.all([publicRequest(api,base),publicRequest(api,base+'/fixtures')]);
    return {competition_id:competition.id,status:competition.status,fixtures:fixtures.items};
  }
  if(!mapped)return null;
  value=await api.snapshot(mapped);
  if(u.pathname.endsWith('/events'))return {...value,items:value.items.filter(e=>e.seq>Number(u.searchParams.get('after')||0))};
  if(u.searchParams.has('limit')&&Array.isArray(value.items)){
    const start=Number(u.searchParams.get('offset')||0),limit=Number(u.searchParams.get('limit'));
    return {...value,items:value.items.slice(start,start+limit)};
  }
  return value;
}

export class PublicFeed {
  constructor(api,path){
    this.api=api;this.path=path;this.listeners=new Map();this.closed=false;this.seq=Number(new URL(path,'file:///').searchParams.get('after')||0);
    queueMicrotask(()=>this.tick());
  }
  addEventListener(type,listener){if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(listener);}
  emit(type,data){if(!this.closed)for(const listener of this.listeners.get(type)||[])listener({data:JSON.stringify(data)});}
  close(){this.closed=true;clearTimeout(this.timer);}
  async tick(){
    try{
      const featured=await this.api.featured();
      if(this.closed)return;
      if(!this.opened){this.opened=true;this.onopen?.();}
      if(new URL(this.path,'file:///').pathname.endsWith('/watch'))this.emit('competition',await publicRequest(this.api,this.path));
      else{
        const m=this.path.match(/^\/competitions\/([\w-]+)\/fixtures\/([\w-]+)\/events/);
        let h=featured.boards.find(x=>x.competition_id===m[1]&&x.fixture_id===m[2])||await publicRequest(this.api,this.path);
        if(h.summary_only&&(h.attempt_id!==this.attempt||h.next_after>this.seq))h=await publicRequest(this.api,this.path);
        if(this.closed)return;
        if(h.fixture&&h.fixture.status!=='completed'&&(!this.interestAt||Date.now()-this.interestAt>=5000)){
          await this.api.request(`/competitions/${m[1]}/fixtures/${m[2]}/live-interest`);this.interestAt=Date.now();
        }
        if(this.attempt!==undefined&&this.attempt!==h.attempt_id){this.emit('attempt_changed',{});return;}
        this.attempt=h.attempt_id;this.emit('attempt',{attempt_id:h.attempt_id});
        for(const event of h.items)if(event.seq>this.seq)this.emit('arena',event);
        this.seq=Math.max(this.seq,h.next_after||0);
      }
    }catch(error){
      if(error.status===404){this.emit('unavailable',{});this.close();return;}
      if(!this.closed)this.onerror?.(error);
    }
    if(!this.closed)this.timer=setTimeout(()=>this.tick(),2000);
  }
}
