import assert from 'node:assert/strict';
import {test} from 'node:test';
import {page} from '../dist/live/pages.js';
import {timeLabel} from '../dist/live/time-control.js';

test('check form shows a fixed half-second budget and has no match-clock selector',()=>{
 const html=page({route:'benchmark',ids:[],user:{role:'guest',team_id:'a',username:'fixture'},loading:false,submissions:[{id:'s',runtime:'cpp17',version:'v1.0.0'}],jobs:[],competitions:[]});
 assert.match(html,/0,5 giây riêng/);assert.match(html,/id="benchmark-form"/);
 assert.doesNotMatch(html,/name="match_ms"|name="turn_ms"/);
 assert.match(timeLabel({id:'ottv2-check-500ms-v1',turn_ms:500}),/0,5 giây/);
});
