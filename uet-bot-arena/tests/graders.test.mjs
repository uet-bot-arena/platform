import assert from 'node:assert/strict';
import {setLanguage,tr} from '../dist/live/i18n.js';
import {test} from 'node:test';
import {gradersPage,graderRows,graderTokenModal,graderMessage,installCommand,linuxInstallCommand,linuxRootfs,validPackage,controlsMessage} from '../dist/live/graders-ui.js';
const signed=file=>`/api/v1/grader/download/1794000000000/${'a'.repeat(64)}/${file}`;
const pkg={file:'uet-arena-grader-0123456789ab.zip',sha256:'0123456789ab'+'c'.repeat(52),bytes:254540,grader_version:'0.8.0-m1',url:signed('uet-arena-grader-0123456789ab.zip')};
const linuxPkg={...pkg,linux_rootfs:{file:'rootfs-'+ 'd'.repeat(64)+'.tar.zst',sha256:'d'.repeat(64),bytes:1000000,url:signed('rootfs-'+ 'd'.repeat(64)+'.tar.zst')}};
const g=extra=>({id:'g1',name:'PC 301',platform:'windows',backend:'windows',slots:2,running:1,jobs_done:5,runtime_id:'sha256:'+'a'.repeat(64),version:'0.8.0-m1',last_seen_ms:1,online:true,revoked_ms:null,...extra});
test('incompatible graders need an update in both languages and add no ready slots',()=>{
 try{for(const [language,label] of [['vi','Cần cập nhật gói'],['en','Package update required']]){
  setLanguage(language);
  const html=gradersPage({graders:{items:[g({needs_update:true}),g({id:'current',slots:3,needs_update:false})],queue:{}}});
  assert.ok(html.includes(label));
  assert.ok(html.includes(`<div class="stat-label">${tr('Slot chấm')}</div><div class="stat-value">3</div>`));
 }}finally{setLanguage('vi');}
});
test('grader table keeps status, slots and revoke/delete without tokens',()=>{
 const html=gradersPage({graders:{items:[g(),g({id:'g2',platform:'linux',backend:'bwrap',online:false}),g({id:'g3',revoked_ms:5,online:false}),g({id:'g4',platform:null,online:false})],queue:{official:2,practice:7}}});
 for(const word of ['Online','Offline','Đã thu hồi','Chưa kết nối','Windows · AppContainer','Linux · bubblewrap'])assert.ok(html.includes(word));
 assert.equal((html.match(/data-action="revoke-grader"/g)||[]).length,3);assert.match(html,/data-action="delete-grader"/);assert.doesNotMatch(html,/uag_|token_hash|undefined|NaN/);
});
test('grader names and modal tokens are escaped',()=>{
 const evil='<img src=x onerror=alert(1)>';assert.doesNotMatch(graderRows([g({name:evil})]).join(''),/<img/);assert.doesNotMatch(graderTokenModal({name:evil,token:'uag_x'}),/<img/);
});
test('install panel exposes neither downloads, hashes nor commands',()=>{
 for(const graderPackage of [null,pkg,linuxPkg]){
  const html=gradersPage({graders:{items:[],queue:{}},graderPackage});
  assert.doesNotMatch(html,/install-grader\.ps1 -HubUrl|SHA-256:|\/downloads\/|<pre|api\/v1\/grader\/download/);
  assert.match(html,/copy-grader-controls/);assert.match(html,/Windows/);assert.match(html,/WSL2/);assert.match(html,/7 ngày/);
 }
});
test('only allowlisted file names and signed relative URLs can enter shell commands',()=>{
 assert.equal(validPackage(pkg),pkg);
 for(const bad of [null,{},{...pkg,file:'../passwd'},{...pkg,url:'/downloads/'+pkg.file},{...pkg,url:pkg.url+';whoami'},{...pkg,url:'https://evil.test'+pkg.url},{...pkg,sha256:'zz'}])assert.equal(validPackage(bad),null);
 for(const linux_rootfs of [{...linuxPkg.linux_rootfs,sha256:'c'.repeat(64)},{...linuxPkg.linux_rootfs,url:'oops'}])assert.equal(linuxRootfs({...pkg,linux_rootfs}),null);
});
test('one-paste Windows message uses signed URL, verifies SHA and explains expiry',()=>{
 const text=graderMessage({name:'laptop',token:'uag_test'},pkg,'https://hub.example');
 assert.ok(text.includes('https://hub.example'+pkg.url));assert.match(text,/Get-FileHash/);assert.ok(text.includes(pkg.sha256.toUpperCase()));assert.match(text,/7 ngày/);assert.match(text,/uag_test/);assert.doesNotMatch(text,/SHA-256:|\/downloads\//);
 const lines=installCommand(pkg,'https://hub.example').split('\n');
 assert.equal(lines[0],'& {');assert.equal(lines.at(-1),'}');
 assert.equal(lines[1],'$ErrorActionPreference = "Stop"');assert.equal(lines.length,9);
});
test('Linux message verifies both files and stops on errors with &&',()=>{
 const text=graderMessage({name:'Linux',token:'uag_test'},linuxPkg,'https://hub.example','linux');
 for(const entry of [linuxPkg,linuxPkg.linux_rootfs])assert.ok(text.includes('https://hub.example'+entry.url));
 const command=linuxInstallCommand(linuxPkg,'https://hub.example');assert.match(command,/sha256sum -c -/);assert.match(command,/--sha256 d{64}/);assert.equal(command.split('\n').filter(x=>x.trim().startsWith('&&')).length,4);assert.match(text,/7 ngày/);
});
test('one-time modal offers Windows and Linux messages only for available packages',()=>{
 assert.doesNotMatch(graderTokenModal({name:'PC',token:'uag_x'}),/copy-grader-message/);
 const html=graderTokenModal({name:'PC',token:'uag_x'},linuxPkg,'https://hub.example');
 for(const marker of ['copy-grader-token','copy-grader-message','data-platform="windows"','data-platform="linux"','lần duy nhất'])assert.ok(html.includes(marker));
 assert.doesNotMatch(html,/SHA-256:|\/downloads\//);
});
test('clipboard controls retain stop/start/uninstall and profile warning',()=>{
 assert.match(controlsMessage('windows'),/install-grader\.ps1 -Stop/);assert.match(controlsMessage('windows'),/install-grader\.ps1 -Uninstall/);assert.match(controlsMessage('windows'),/default\.lnk/);
 assert.match(controlsMessage('linux'),/systemctl --user start/);assert.match(controlsMessage('linux'),/--uninstall/);assert.match(controlsMessage('linux'),/profile/);
});
