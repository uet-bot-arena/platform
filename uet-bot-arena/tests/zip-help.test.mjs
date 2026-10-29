import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {APIError} from '../dist/live/client.js';
import {setLanguage} from '../dist/live/i18n.js';
import {renderBotGuide} from '../dist/live/bot-guide.js';
test('archive hints retain error code and translate suggested entry file and language',()=>{
 const response={status:400,headers:new Headers()},messages=[
 ['Thiếu bot.py. Hệ thống chạy file tên bot.py; đổi tên main.py thành bot.py.','Missing bot.py. The entry file must be bot.py; rename main.py to bot.py.'],
 ['ZIP có bot.py: hãy chọn Ngôn ngữ Python 3.','ZIP contains bot.py: select Language Python 3.'],
 ['Hệ thống nhận Python 3, C11, C++17; chưa nhận Java/JavaScript.','Python 3, C11 and C++17 are supported; Java/JavaScript are not supported.'],
 ['File không được phép: app.exe. Chỉ giữ source cùng ngôn ngữ và README.md/LICENSE.MIT.','File not allowed: app.exe. Keep only source for the selected language and README.md/LICENSE.MIT.']];
 try{for(const [vi,en] of messages)for(const [lang,want] of [['vi',vi],['en',en]]){setLanguage(lang);const e=new APIError(response,{error:{code:'invalid_archive',detail:vi}});assert.equal(e.message,want);assert.equal(e.code,'invalid_archive');}}finally{setLanguage('vi');}
});
test('both bot guides explain per-sample budgets and foreign protocol in eight lines',()=>{
 for(const lang of ['vi','en']){
  const source=readFileSync(new URL('../dist/live/bot-guide'+(lang==='en'?'.en':'')+'.md',import.meta.url),'utf8'),section=source.slice(source.lastIndexOf('## ')).trimEnd();assert.equal(section.split('\n').filter(Boolean).length,8);
  for(const field of ['legalActions','legal_actions','payload.legal_moves','state_version','YOUR_TURN','ready','choose_move','bot.py'])assert.ok(section.includes(field));
  assert.match(renderBotGuide(source),/YOUR_TURN/);assert.match(source,/90/);assert.match(source,lang==='vi'?/0,5 giây/:/500 ms/);assert.doesNotMatch(source,/72 mẫu dùng chung|Bullet 60 giây/);
 }
});
