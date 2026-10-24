import english from './en.js';
const KEY='uet-arena-language';
let selected='vi';
try{if(globalThis.localStorage?.getItem(KEY)==='en')selected='en';}catch{}
export const language=()=>selected;
export const locale=()=>selected==='en'?'en-GB':'vi-VN';
export function setLanguage(value){
  if(!['vi','en'].includes(value))throw new RangeError('Unsupported language');
  selected=value;
  try{globalThis.localStorage?.setItem(KEY,value);}catch{}
  if(typeof document!=='undefined')document.documentElement.lang=value;
}
setLanguage(selected);
const escapeRegex=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const phrases=Object.keys(english).filter(k=>k.length>1).sort((a,b)=>b.length-a.length);
const pattern=new RegExp(phrases.map(escapeRegex).join('|'),'gu');
export function tr(value){
  if(selected==='vi')return value;
  return Object.hasOwn(english,value)?english[value]:value.replace(pattern,key=>english[key]);
}
// Translate only template source segments, before interpolated user values join.
export function html(parts,...values){return parts.map((part,i)=>tr(part)+(i<values.length?values[i]:'')).join('');}
export const languageButton=()=>`<button type="button" class="btn small secondary" data-action="language" aria-label="${selected==='vi'?'Switch to English':'Chuyển sang tiếng Việt'}" lang="${selected==='vi'?'en':'vi'}">${selected==='vi'?'EN':'VI'}</button>`;
