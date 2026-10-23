import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../dist/live/theme.js',import.meta.url),'utf8');
function boot(dark=false,saved=null,blocked=false){
 const values=new Map(saved?[['uet-arena-theme',saved]]:[]),handlers={};
 const root={dataset:{},style:{}},media={matches:dark,addEventListener:(type,fn)=>handlers.media=fn};
 const document={documentElement:root,addEventListener:(type,fn)=>handlers[type]=fn},window={};
 const storage={getItem:k=>{if(blocked)throw Error('blocked');return values.get(k);},setItem:(k,v)=>{if(blocked)throw Error('blocked');values.set(k,v);},removeItem:k=>values.delete(k)};
 vm.runInNewContext(source,{window,document,localStorage:storage,matchMedia:()=>media});
 return {root,media,values,handlers,api:window.arenaTheme};
}
test('system preference applies before UI, manual choice persists and OS changes respect it',()=>{
 const x=boot(true);assert.equal(x.root.dataset.theme,'dark');
 x.api.toggle();assert.equal(x.root.dataset.theme,'light');assert.equal(x.values.get('uet-arena-theme'),'light');
 x.media.matches=false;x.handlers.media();assert.equal(x.root.dataset.theme,'light');
 x.media.matches=true;x.handlers.media();assert.equal(x.root.dataset.theme,'light');
 assert.equal(boot(true,'light').root.dataset.theme,'light');
 x.api.system();assert.equal(x.root.dataset.theme,'dark');assert.equal(x.values.size,0);
 x.media.matches=false;x.handlers.media();assert.equal(x.root.dataset.theme,'light');
});
test('blocked or invalid browser storage keeps the control usable',()=>{
 const x=boot(false,null,true);x.api.toggle();assert.equal(x.root.dataset.theme,'dark');
 assert.equal(boot(false,'invalid').root.dataset.theme,'light');
 assert.deepEqual([...x.values.keys()],[]);
});
