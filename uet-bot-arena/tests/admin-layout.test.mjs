import assert from 'node:assert/strict';
import test from 'node:test';
import {trend} from '../dist/live/admin-ui.js';
import {shell} from '../dist/live/view.js';

test('each day column, its count and its date share one centre with or without failures',()=>{
  const days=[{day:'2026-10-24',jobs:4,failed:0},{day:'2026-10-25',jobs:119,failed:7},{day:'2026-10-26',jobs:0,failed:0}];
  const groups=trend(days).match(/<g>.*?<\/g>/g);
  assert.equal(groups.length,3);
  for(const group of groups){
    const rects=[...group.matchAll(/<rect class="trend-(total|failed)" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"/g)].map(m=>Number(m[2])+Number(m[3])/2);
    const texts=[...group.matchAll(/<text x="([\d.]+)"/g)].map(m=>Number(m[1]));
    assert.equal(texts.length,2);
    for(const centre of [...rects,...texts])assert.equal(centre,texts[0],group);
  }
  assert.match(groups[1],/>119<\/text>/);assert.match(groups[1],/7 lỗi/);
  assert.match(trend(days),/Ngày theo giờ Việt Nam/);
});

test('top bar keeps the mobile menu and a plain Refresh button, without the breadcrumb text',()=>{
  const html=shell({route:'competitions',user:{role:'team'}},'');
  const bar=html.match(/<header class="topbar">.*?<\/header>/s)[0];
  assert.match(bar,/data-action="menu"/);
  assert.doesNotMatch(bar,/UET Bot Arena/);
  assert.match(bar,/data-action="refresh"[^>]*>Làm mới</);
  assert.doesNotMatch(html,/Cập nhật dữ liệu/);
});
