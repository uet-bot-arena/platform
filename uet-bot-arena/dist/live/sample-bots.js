import {panel,table,button,esc,note,runtimeLabel} from './view.js';
export function sampleAdmin(s){
  return `<div id="sample-bot-catalog">${panel('Bot mẫu',`${note('Một bot dễ dùng chung với repo starter: ưu tiên vào căn cứ, ăn quân, tiến về căn cứ địch. Chỉ dùng để luyện tập, không tính Elo.')}${table(['Bot','Ngôn ngữ',''],(s.sampleBots||[]).map(b=>`<tr><td>${esc(b.name)}</td><td>${esc(runtimeLabel(b.runtime))}</td><td>${button(b.enabled?'Tắt':'Bật','sample-bot-toggle',`data-id="${b.id}" data-enabled="${!b.enabled}"`,'small')}</td></tr>`))}`)}</div>`;
}
