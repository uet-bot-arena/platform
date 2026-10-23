// The banner survives page/board rendering and never interprets notice HTML.
export function startAnnouncements(api){
  const target=document.getElementById('announcements-banner');
  let key='',busy=false;
  async function refresh(){
    if(busy)return;busy=true;
    try{
      const data=await api.snapshot('announcements.json'),notice=data.items[0];
      const next=notice?notice.id+':'+notice.revision:'';
      if(next===key)return;key=next;target.replaceChildren();target.hidden=!notice;
      if(!notice)return;
      const title=document.createElement('strong'),body=document.createElement('span'),link=document.createElement('a');
      title.textContent=notice.title;
      const text=Array.from(notice.body.replace(/\s+/g,' '));body.textContent=text.slice(0,180).join('')+(text.length>180?'…':'');
      link.href='#/announcements';link.textContent='Xem thông báo';
      target.append(title,body,link);
    }catch{/* Keep the last public notice while reconnecting. */}
    finally{busy=false;}
  }
  refresh();setInterval(refresh,30000);
  addEventListener('focus',refresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  return refresh;
}
