import {tr,html as l10n} from './i18n.js';
import {panel,table,button,esc,note,runtimeLabel} from './view.js';
export function sampleAdmin(s){
  return `<div id="sample-bot-catalog">${panel(tr('Bot mẫu'),`${note(tr('Một bot dễ dùng chung với repo starter: ưu tiên vào căn cứ, ăn quân, tiến về căn cứ địch. Chỉ dùng để luyện tập, không tính Elo.'))}${table(['Bot',tr('Ngôn ngữ'),''],(s.sampleBots||[]).map(b=>`<tr><td>${esc(b.name)}</td><td>${esc(runtimeLabel(b.runtime))}</td><td>${button(b.enabled?tr('Tắt'):tr('Bật'),'sample-bot-toggle',`data-id="${b.id}" data-enabled="${!b.enabled}"`,'small')}</td></tr>`))}`)}</div>`;
}
