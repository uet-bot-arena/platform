// Reconcile the overview's server-rendered content without remounting the page.
// Stable keys keep job/competition links attached when list ordering changes.
const key=node=>node.nodeType===1?(node.getAttribute('data-key')||node.id):null;
const compatible=(a,b)=>a&&a.nodeType===b.nodeType&&a.nodeName===b.nodeName;
function patchNode(current,next){
  if(current.isEqualNode(next))return;
  if(current.nodeType!==1){current.nodeValue=next.nodeValue;return;}
  for(const attr of [...current.attributes])if(!next.hasAttribute(attr.name))current.removeAttribute(attr.name);
  for(const attr of next.attributes)if(current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
  patchChildren(current,next);
}
export function patchChildren(current,next){
  const keyed=new Map([...current.childNodes].filter(key).map(node=>[key(node),node]));
  let cursor=current.firstChild;
  for(const child of next.childNodes){
    const id=key(child);
    let existing=id?keyed.get(id):cursor&&!key(cursor)?cursor:null;
    if(!compatible(existing,child))existing=null;
    if(!existing){existing=child.cloneNode(true);current.insertBefore(existing,cursor);}
    else{
      if(existing!==cursor)current.insertBefore(existing,cursor);
      patchNode(existing,child);
    }
    cursor=existing.nextSibling;
  }
  while(cursor){const stale=cursor;cursor=cursor.nextSibling;stale.remove();}
}
