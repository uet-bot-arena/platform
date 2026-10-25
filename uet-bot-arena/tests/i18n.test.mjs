import test from 'node:test';
import assert from 'node:assert/strict';
import {tr,html,setLanguage,language,locale,languageButton} from '../dist/live/i18n.js';
import {esc,date} from '../dist/live/view.js';
import {APIError} from '../dist/live/client.js';

test('English translates static UI while preserving interpolated names and escaping',()=>{
 setLanguage('en');
 assert.equal(tr('Đăng nhập'),'Sign in');assert.equal(locale(),'en-GB');
 const name='Đăng nhập <script>alert(1)</script>';
 const output=html`<h2>Đăng nhập</h2><p>${esc(name)}</p>`;
 assert.equal(output,'<h2>Sign in</h2><p>Đăng nhập &lt;script&gt;alert(1)&lt;/script&gt;</p>');
 setLanguage('vi');assert.equal(tr('Đăng nhập'),'Đăng nhập');
});
test('language persists, invalid values fail and blocked storage remains usable',async()=>{
 const values=new Map([['uet-arena-language','en']]);
 globalThis.localStorage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};
 globalThis.document={documentElement:{}};
 const saved=await import('../dist/live/i18n.js?stored');
 assert.equal(saved.language(),'en');assert.equal(document.documentElement.lang,'en');
 saved.setLanguage('vi');assert.equal(values.get('uet-arena-language'),'vi');
 assert.throws(()=>saved.setLanguage('xx'),RangeError);
 globalThis.localStorage={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
 const blocked=await import('../dist/live/i18n.js?blocked');blocked.setLanguage('en');
 assert.equal(blocked.language(),'en');assert.equal(document.documentElement.lang,'en');
 delete globalThis.localStorage;delete globalThis.document;
});
test('English API failures explain authentication and overload without rewriting server/user details',()=>{
 setLanguage('en');
 assert.equal(new APIError(new Response('',{status:401}),{error:{code:'unauthorized'}}).message,'Session expired or incorrect account/password.');
 const failure=new APIError(new Response('',{status:400}),{error:{code:'invalid_request',detail:'Đội Đăng nhập'}});
 assert.equal(failure.message,'Invalid details. Check the fields you entered.');
 assert.match(new APIError(new Response('',{status:503}),'busy').message,/connections/);
 setLanguage('vi');assert.equal(language(),'vi');
});


test('ladder timestamps stay in Vietnam time in either language',()=>{
 for(const value of ['en','vi']){setLanguage(value);assert.match(date(Date.parse('2026-10-24T00:00:00Z')),/07:00/);}
});

test('language button shows the language in use and explains the switch',()=>{
 setLanguage('vi');let button=languageButton();
 assert.match(button,/>VI<\/button>$/);assert.match(button,/title="Chuyển sang tiếng Anh"/);assert.match(button,/lang="vi"/);
 setLanguage('en');button=languageButton();
 assert.match(button,/>EN<\/button>$/);assert.match(button,/title="Switch to Vietnamese"/);assert.match(button,/lang="en"/);
 setLanguage('vi');
});
