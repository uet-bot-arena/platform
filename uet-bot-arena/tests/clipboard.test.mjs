import {test} from 'node:test';
import assert from 'node:assert/strict';
import {copyText} from '../dist/live/client.js';

test('clipboard API and HTTP LAN selection preserve the exact multiline text and focus',async()=>{
  const original=Object.fromEntries(['navigator','document'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
  const text='Tài khoản: đội-thử\nMật khẩu tạm: kiểm thử',calls=[];
  let appended,removed,focused,selected,copied;
  const document={activeElement:{focus(){focused=true;}},body:{append(field){appended=field;}},createElement(tag){assert.equal(tag,'textarea');return{style:{},setAttribute(){},focus(){},select(){selected=true;},remove(){removed=true;}};},execCommand(command){assert.equal(command,'copy');assert.ok(selected);copied=appended.value;return true;}};
  const set=(key,value)=>Object.defineProperty(globalThis,key,{configurable:true,value});
  try{
    set('document',document);set('navigator',{clipboard:{writeText:async value=>calls.push(value)}});
    assert.equal(await copyText(text),true);assert.deepEqual(calls,[text]);assert.equal(appended,undefined);
    set('navigator',{});assert.equal(await copyText(text),true);assert.equal(copied,text);assert.ok(removed&&focused);
    set('navigator',{clipboard:{writeText:async()=>{throw new Error('permission denied');}}});
    assert.equal(await copyText(text),true);assert.equal(copied,text);
    for(const exec of [()=>false,()=>{throw new Error('copy unavailable');}]){
      document.execCommand=exec;removed=focused=false;
      assert.equal(await copyText(text),false);assert.ok(removed&&focused);
    }
  }finally{for(const [key,value] of Object.entries(original)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
