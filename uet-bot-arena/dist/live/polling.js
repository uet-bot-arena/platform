// One timer per reader; a hidden tab neither schedules nor starts a request.
export function visiblePoll(read,delay,{immediate=false,onError=()=>{},document:doc=globalThis.document}={}){
  let timer,closed=false,busy=false;
  const hidden=()=>Boolean(doc?.hidden);
  function schedule(){clearTimeout(timer);if(!closed&&!hidden())timer=setTimeout(tick,typeof delay==='function'?delay():delay);}
  async function tick(){
    if(closed||hidden()||busy)return;
    clearTimeout(timer);busy=true;
    try{await read();}catch(error){onError(error);}finally{busy=false;schedule();}
  }
  function visibility(){clearTimeout(timer);if(!hidden())tick();}
  doc?.addEventListener('visibilitychange',visibility);
  if(immediate)tick();else schedule();
  return {refresh:tick,close(){closed=true;clearTimeout(timer);doc?.removeEventListener('visibilitychange',visibility);}};
}

export function githubPollMs(github,now=Date.now()){
  return (github?.commits||[]).some(c=>['detected','checking'].includes(c.state)||(c.checks||[]).some(x=>['waiting','checking'].includes(x.state))||now-Math.max(c.pushed_ms||0,c.received_ms||0)<120000)?3000:30000;
}

export class VisibleSource {
  constructor(path,{document:doc=globalThis.document,Source=globalThis.EventSource}={}){
    this.path=path;this.doc=doc;this.Source=Source;this.listeners=new Map();
    this.visibility=()=>{this.source?.close();if(!doc?.hidden&&!this.closed)this.open();};
    doc?.addEventListener('visibilitychange',this.visibility);
    if(!doc?.hidden)this.open();
  }
  open(){
    const url=new URL(this.path,'http://localhost');
    if(this.cursor)url.searchParams.set('after',this.cursor);
    this.source=new this.Source(url.pathname+url.search);
    this.source.onopen=e=>this.onopen?.(e);this.source.onerror=e=>this.onerror?.(e);
    for(const [type,listeners] of this.listeners)for(const listener of listeners)this.bind(type,listener);
  }
  bind(type,listener){this.source.addEventListener(type,event=>{if(event.lastEventId)this.cursor=event.lastEventId.split(':').at(-1);listener(event);});}
  addEventListener(type,listener){if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(listener);if(this.source)this.bind(type,listener);}
  close(){this.closed=true;this.source?.close();this.doc?.removeEventListener('visibilitychange',this.visibility);}
}
