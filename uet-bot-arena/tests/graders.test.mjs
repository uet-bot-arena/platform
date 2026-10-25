import assert from 'node:assert/strict';
import {test} from 'node:test';
import {gradersPage,graderRows,graderTokenModal,graderMessage,installCommand,linuxInstallCommand,linuxRootfs,validPackage} from '../dist/live/graders-ui.js';
import {readTournamentOptions} from '../dist/live/tournament-options.js';

const g=(extra)=>({id:'g1',name:'PC 301',platform:'windows',backend:'windows',slots:2,running:1,jobs_done:5,
  runtime_id:'sha256:'+'a'.repeat(64),version:'0.7.0-m1',last_seen_ms:1,online:true,revoked_ms:null,...extra});

test('grader list shows status, platform, slots and never a token',()=>{
  const html=gradersPage({graders:{items:[g(),g({id:'g2',name:'WSL',platform:'linux',backend:'bwrap',online:false}),
    g({id:'g3',name:'Cũ',revoked_ms:5,online:false}),g({id:'g4',name:'Mới',platform:null,online:false})],queue:{official:2,practice:7}},origin:'http://127.0.0.1:4181'});
  assert.match(html,/Online/);assert.match(html,/Offline/);assert.match(html,/Đã thu hồi/);assert.match(html,/Chưa kết nối/);
  assert.match(html,/Windows · AppContainer/);assert.match(html,/Linux · bubblewrap/);
  assert.match(html,/<div class="stat-value">1<\/div>/,'one machine online');
  assert.match(html,/<div class="stat-value">2<\/div>/,'two slots online');
  assert.match(html,/--hub http:\/\/127\.0\.0\.1:4181 --slots 2 --backend windows/);
  assert.doesNotMatch(html,/uag_|token_hash|undefined|NaN/);
  assert.equal((html.match(/data-action="revoke-grader"/g)||[]).length,3,'no revoke button for an already revoked grader');
});

test('grader names are escaped everywhere',()=>{
  const evil='<img src=x onerror=alert(1)>';
  const html=graderRows([g({name:evil})]).join('');
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
  assert.doesNotMatch(graderTokenModal({name:evil,token:'uag_x'}),/<img/);
});

test('token modal shows the one-time token and a copy action',()=>{
  const html=graderTokenModal({name:'PC 301',token:'uag_abc'});
  assert.match(html,/uag_abc/);assert.match(html,/copy-grader-token/);assert.match(html,/lần duy nhất/);
});

const pkg={file:'uet-arena-grader-0123456789ab.zip',sha256:'0123456789ab'+'c'.repeat(52),bytes:254540,grader_version:'0.8.0-m1'};

test('package panel links the hub ZIP with its SHA-256 and a one-paste install block',()=>{
  const html=gradersPage({graders:{items:[],queue:{}},graderPackage:pkg,origin:'https://hub.example'});
  assert.match(html,/href="https:\/\/hub\.example\/downloads\/uet-arena-grader-0123456789ab\.zip" download/);
  assert.match(html,new RegExp(pkg.sha256));assert.match(html,/0\.8\.0-m1/);
  assert.match(html,/Get-FileHash/);assert.match(html,new RegExp(pkg.sha256.toUpperCase()));
  assert.match(html,/install-grader\.ps1 -HubUrl https:\/\/hub\.example -Slots 2/);
  assert.doesNotMatch(gradersPage({graders:{items:[],queue:{}},origin:'https://hub.example'}),/Gói cài máy chấm/,'no manifest, no panel');
});

test('only well-formed package manifests are used',()=>{
  assert.equal(validPackage(pkg),pkg);
  for(const bad of [null,{},{...pkg,file:'../etc/passwd'},{...pkg,file:'uet-arena-grader-0123456789ab.zip"onerror=x'},{...pkg,sha256:'zz'}])
    assert.equal(validPackage(bad),null);
});

test('install message carries link, checked SHA, command and token for one machine',()=>{
  const text=graderMessage({name:'laptop-2',token:'uag_abc'},pkg,'https://hub.example');
  assert.match(text,/laptop-2/);assert.match(text,/uag_abc/);
  assert.match(text,/Invoke-WebRequest -UseBasicParsing "https:\/\/hub\.example\/downloads\/uet-arena-grader-0123456789ab\.zip"/);
  assert.match(text,/-ne "0123456789AB/);assert.match(text,/install-grader\.ps1 -Stop/);
  assert.equal(installCommand(pkg,'https://hub.example').split('\n').length,6);
});

test('token modal adds the copyable install message only when a package exists',()=>{
  const html=graderTokenModal({name:'PC <b>',token:'uag_abc'},pkg,'https://hub.example');
  assert.match(html,/copy-grader-message/);assert.match(html,/id="grader-message-windows"/);assert.match(html,/uag_abc/);
  assert.doesNotMatch(html,/data-platform="linux"/,'no rootfs, no Linux message');
  assert.doesNotMatch(html,/<b>/);
  assert.doesNotMatch(graderTokenModal({name:'PC',token:'uag_abc'}),/copy-grader-message/);
});

test('competition form sends the grader platform, default windows',()=>{
  assert.equal(readTournamentOptions(new Map([['format','swiss']])).grader_platform,'windows');
  assert.equal(readTournamentOptions(new Map([['format','swiss'],['grader_platform','linux']])).grader_platform,'linux');
});

const digest='d'.repeat(64);
const linuxPkg={...pkg,linux_rootfs:{file:`rootfs-${digest}.tar.zst`,sha256:digest,bytes:113901128}};

test('install panel offers Windows and Linux blocks and hides source-checkout commands',()=>{
  const html=gradersPage({graders:{items:[],queue:{}},graderPackage:linuxPkg,origin:'https://hub.example'});
  assert.match(html,/Cài máy chấm/);assert.match(html,/Windows 10\/11/);assert.match(html,/Ubuntu\/Linux hoặc WSL2/);
  assert.match(html,/install-grader\.ps1 -HubUrl https:\/\/hub\.example -Slots 2/);
  assert.match(html,/install-grader\.sh --hub https:\/\/hub\.example --slots 2 --rootfs https:\/\/hub\.example\/downloads\/rootfs-d{64}\.tar\.zst --sha256 d{64}/);
  assert.match(html,/109 MiB/);
  assert.doesNotMatch(html,/arena\.sandbox\.toolchain install|--backend bwrap/,'source-checkout panel only without a package');
  const windowsOnly=gradersPage({graders:{items:[],queue:{}},graderPackage:pkg,origin:'https://hub.example'});
  assert.doesNotMatch(windowsOnly,/install-grader\.sh/);assert.match(windowsOnly,/chưa phát rootfs/);
});

test('Linux install is one fail-fast chain that checks both SHA-256 values',()=>{
  const text=linuxInstallCommand(linuxPkg,'https://hub.example');
  const lines=text.split('\n');
  assert.equal(lines.length,5);
  assert.ok(lines.slice(0,-1).every(line=>line.endsWith(' \\')),'continued lines');
  assert.ok(lines.slice(1).every(line=>line.startsWith('  && ')),'each step runs only after the previous one succeeded');
  assert.match(text,/curl -fLO https:\/\/hub\.example\/downloads\/uet-arena-grader-0123456789ab\.zip/);
  assert.ok(text.includes(`echo "${pkg.sha256}  uet-arena-grader-0123456789ab.zip" | sha256sum -c -`));
  assert.match(text,/python3 -m zipfile -e uet-arena-grader-0123456789ab\.zip ~\/uet-grader/);
});

test('only a rootfs whose name matches its SHA-256 is offered',()=>{
  assert.equal(linuxRootfs(linuxPkg),linuxPkg.linux_rootfs);
  for(const bad of [{...linuxPkg.linux_rootfs,sha256:'e'.repeat(64)},{...linuxPkg.linux_rootfs,file:'../rootfs-'+digest+'.tar.zst'},{...linuxPkg.linux_rootfs,file:'rootfs-x.tar.zst'},null])
    assert.equal(linuxRootfs({...pkg,linux_rootfs:bad}),null);
  assert.equal(linuxRootfs({...linuxPkg,file:'bad.zip'}),null,'no valid package, no rootfs');
});

test('Linux install message carries the chain, token and stop/uninstall commands',()=>{
  const text=graderMessage({name:'ubuntu-1',token:'uag_lin'},linuxPkg,'https://hub.example','linux');
  assert.match(text,/ubuntu-1/);assert.match(text,/uag_lin/);assert.match(text,/Ubuntu 24\.04/);assert.match(text,/sudo/);
  assert.ok(text.includes(linuxInstallCommand(linuxPkg,'https://hub.example')));
  assert.match(text,/systemctl --user stop uet-arena-grader-default/);assert.match(text,/install-grader\.sh --uninstall/);
  assert.doesNotMatch(text,/PowerShell|install-grader\.ps1/);
  const modal=graderTokenModal({name:'ubuntu-1',token:'uag_lin'},linuxPkg,'https://hub.example');
  assert.match(modal,/id="grader-message-linux"/);assert.match(modal,/data-platform="linux"/);assert.match(modal,/data-platform="windows"/);
});
