import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {participantData} from '../dist/live/page-data.js';

test('navigation follows docs/UI.md request table, with at most two private API reads',async()=>{
 const doc=readFileSync(new URL('../../docs/UI.md',import.meta.url),'utf8');
 const rows=doc.split('\n').filter(x=>/^\| (home|team|submissions|standings|schedule|queue|competitions|live|announcements|benchmark|practice|jobs|matching|rulesets) \|/.test(x));
 assert.equal(rows.length,14);
 for(const row of rows){
  const [,route,apiPaths,snapshots]=row.split('|').map(x=>x.trim()),calls=[],files=[];
  const parse=x=>x==='—'?[]:x.split(', ').map(v=>v.replaceAll('{id}','cup').replaceAll('{job}','job'));
  const api={request:async path=>{calls.push(path);return {clock:{clock_id:'test',server_received_unix_ms:0,server_sent_unix_ms:0}};},snapshot:async path=>{files.push(path);return path==='competitions.json'?{items:[{id:'cup'}]}:path.startsWith('competitions/')?{id:'cup',config:{format:'elo'}}:{items:[]};}};
  await participantData(api,{team_id:'team'},route,route==='competitions'?['cup']:route==='jobs'?['job']:[],new URLSearchParams());
  assert.deepEqual(calls,parse(apiPaths),route);assert.deepEqual(files.sort(),parse(snapshots).sort(),route);assert.ok(calls.length<=2,route);
 }
});
