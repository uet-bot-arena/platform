import {esc} from './view.js';

// Render the Markdown constructs used by the supplied rulebook. Raw HTML is
// always escaped; source-file references stay text because they aren't web URLs.
function inline(text) {
  const tokens=/`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/g;
  let html='', previous=0;
  for(const match of text.matchAll(tokens)) {
    html+=esc(text.slice(previous,match.index));
    html+=match[1]!==undefined?`<code>${esc(match[1])}</code>`:
      match[2]!==undefined?inline(match[2]):`<strong>${inline(match[4])}</strong>`;
    previous=match.index+match[0].length;
  }
  return html+esc(text.slice(previous));
}

const listItem=line=>line.match(/^(\s*)([-+*]|\d+\.)\s+(.+)$/);
const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
const divider=line=>line?.includes('|')&&cells(line).every(x=>/^:?-{3,}:?$/.test(x));
const heading=line=>line.match(/^(#{1,3})\s+(.+)$/);
const rule=line=>/^\s*---+\s*$/.test(line);

export function renderRules(text) {
  const lines=String(text).replace(/\r\n?/g,'\n').split('\n');
  const blockStart=i=>heading(lines[i])||rule(lines[i])||listItem(lines[i])||
    /^\s*```/.test(lines[i])||divider(lines[i+1]);
  let html='', i=0;
  while(i<lines.length) {
    if(!lines[i].trim()) { i++; continue; }
    const title=heading(lines[i]);
    if(title) {
      const level=title[1].length+2;
      html+=`<h${level}>${inline(title[2])}</h${level}>`;
      i++; continue;
    }
    if(rule(lines[i])) { html+='<hr>'; i++; continue; }
    if(/^\s*```/.test(lines[i])) {
      const code=[];
      for(i++;i<lines.length&&!/^\s*```/.test(lines[i]);i++) code.push(lines[i]);
      html+=`<pre><code>${esc(code.join('\n'))}</code></pre>`;
      if(i<lines.length) i++;
      continue;
    }
    if(divider(lines[i+1])) {
      const headers=cells(lines[i]);
      i+=2;
      const rows=[];
      while(i<lines.length&&lines[i].trim().startsWith('|')) {
        rows.push(`<tr>${cells(lines[i++]).map(x=>`<td>${inline(x)}</td>`).join('')}</tr>`);
      }
      html+=`<div class="rules-table" role="region" aria-label="Bảng luật OTTv2" tabindex="0"><table><thead><tr>${headers.map(x=>`<th scope="col">${inline(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
      continue;
    }
    const first=listItem(lines[i]);
    if(first) {
      const indent=first[1].length, ordered=/\d/.test(first[2]);
      const tag=ordered?'ol':'ul';
      html+=`<${tag}${ordered?` start="${parseInt(first[2],10)}"`:''}>`;
      while(i<lines.length) {
        const item=listItem(lines[i]);
        if(!item||item[1].length!==indent||/\d/.test(item[2])!==ordered) break;
        const body=[item[3]];
        i++;
        while(i<lines.length) {
          if(!lines[i].trim()) { body.push(''); i++; continue; }
          const next=listItem(lines[i]);
          const depth=lines[i].match(/^\s*/)[0].length;
          if(depth<=indent||(next&&next[1].length<=indent)) break;
          body.push(lines[i].slice(indent+2));
          i++;
        }
        html+=`<li>${renderRules(body.join('\n'))}</li>`;
      }
      html+=`</${tag}>`;
      continue;
    }
    const paragraph=[lines[i++].trim()];
    while(i<lines.length&&lines[i].trim()&&!blockStart(i)) paragraph.push(lines[i++].trim());
    html+=`<p>${inline(paragraph.join(' '))}</p>`;
  }
  return html;
}
