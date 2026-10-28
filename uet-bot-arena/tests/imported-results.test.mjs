import {test} from 'node:test';
import assert from 'node:assert/strict';
import {importedBanner,exportForm,importForm,importedEventFor} from '../dist/live/imported-results.js';
import {compCards} from '../dist/live/pages.js';
import {ArenaAPI} from '../dist/live/client.js';
const event={id:'event',name:'LAN <finals>',sha256:'a'.repeat(64),imported_ms:1000,competitions:[{id:'c',title:'Bảng A',format:'double_round_robin',teams:4,games:12,replays:12}]};
test('imported cards preserve order, escape names and show hide only to admin',()=>{
 const html=compCards({importedEvents:[event]});assert.match(html,/#\/competitions\/c/);assert.doesNotMatch(html,/<finals>|hide-imported/);
 assert.match(compCards({importedEvents:[event],user:{role:'admin'}}),/hide-imported/);
 assert.match(importedBanner(event),/aaaaaaaaaaaa/);
 assert.doesNotMatch(compCards({importedEvents:[]}),/real-card-grid/);
 assert.equal(importedEventFor([event],'replay',['c','f'],new URLSearchParams()),event);
});
test('export only offers finished competitions and import has explicit preview and confirmation',()=>{
 const html=exportForm({competitions:[{id:'c',title:'Done',status:'completed'},{id:'x',title:'Running',status:'running'}]});assert.match(html,/value="c"/);assert.doesNotMatch(html,/value="x"/);
 assert.doesNotMatch(importForm(),/results-confirm/);assert.match(importForm(event),/results-confirm/);
 assert.doesNotMatch(importForm({...event,competitions:[{...event.competitions[0],games:'<img src=x>'}]}),/<img/);
});
test('admin reads imported competition data from snapshots',async()=>{
 const api=new ArenaAPI();api.admin=true;api.imported=new Set(['c']);
 api.snapshot=async path=>({id:'c',snapshot:path});
 const result=await api.request('/competitions/c');assert.equal(result.snapshot,'competitions/c.json');
});
