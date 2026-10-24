import {tr,html as l10n,language} from './i18n.js';
import {esc} from './view.js';
import {renderRules} from './rules.js';

export async function loadBotGuide(){
  const response=await fetch(language()==='en'?'/_ui/bot-guide.en.md':'/_ui/bot-guide.md',{cache:'no-store',signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error(tr('Chưa tải được hướng dẫn viết bot. Hãy thử mở lại trang tài liệu.'));
  return response.text();
}

export function renderBotGuide(source){
  if(!source)return tr('<p class="muted">Đang tải hướng dẫn…</p>');
  const parts=source.replace(/\r\n?/g,'\n').split(/^## /m),intro=parts.shift();
  return `<div class="rules-document bot-guide-intro">${renderRules(intro.replace(/^# .+\n/,''))}</div><div class="bot-guide-sections">${parts.map((part,i)=>{
    const newline=part.indexOf('\n'),title=part.slice(0,newline),body=part.slice(newline+1);
    return `<details class="bot-guide-section" data-key="bot-guide-${i}"${i===0?' open':''}><summary>${esc(title)}</summary><article class="rules-document" aria-label="${esc(title)}">${renderRules(body)}</article></details>`;
  }).join('')}</div>`;
}
