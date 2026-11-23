import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runPullbackMethod} from '../pullback-engine.mjs';
import {runRecipe} from '../engine.mjs';
import catalog from '../catalog.mjs';
import {makeCompleteFixture} from './fixtures.mjs';
const methods=JSON.parse(readFileSync(new URL('../pullback-methods.json',import.meta.url)));
const trim=(input,n)=>{
 const x=structuredClone(input),dates=x.calendar.slice(0,n);x.calendar=dates;x.cutoff=dates.at(-1);
 for(const k of ['bars','benchmark'])x[k]=x[k].slice(0,n);
 x.panel=Object.fromEntries(Object.entries(x.panel).map(([k,v])=>[k,v.slice(0,n)]));return x;
};
test('persistence accepts exactly horizon plus five observations; change still needs six',()=>{
 const x=trim(makeCompleteFixture(),10);
 for(const scope of ['stock','market']){
  const recipe=catalog.find(r=>r.horizon===5&&r.scope===scope&&r.metric==='return'&&r.view==='persistence');
  assert.equal(runRecipe(recipe,x).status,'available');
  assert.equal(runRecipe({...recipe,view:'change'},x).status,'missing');
 }
});
test('beta fit accepts peak index252 and rejects251',()=>{
 const x=trim(makeCompleteFixture(),278),make=peak=>x.calendar.map((date,i)=>{const c=i<=peak?100+i*.1:100+peak*.1-(i-peak)*.1;return{date,open:c,high:c+1,low:c-1,close:c,volume:1e6}});
 x.bars=make(252);assert.equal(runPullbackMethod(methods[85],x).status,'available');
 x.bars=make(251);assert.equal(runPullbackMethod(methods[85],x).status,'missing');
});
test('method50 accepts504 and rejects503 while method47 retains505 minimum',()=>{
 const x=trim(makeCompleteFixture(),504);
 assert.equal(runPullbackMethod(methods[49],x).status,'available');
 assert.equal(runPullbackMethod(methods[46],x).status,'missing');
 assert.equal(runPullbackMethod(methods[49],trim(x,503)).status,'missing');
});
test('old zero volumes outside reported windows do not block recent liquidity calculations',()=>{
 for(const n of [29,81,97]){
  const x=makeCompleteFixture();x.bars[1].volume=0;for(const bars of Object.values(x.panel))bars[1].volume=0;
  assert.equal(runPullbackMethod(methods[n-1],x).status,'available','method'+n);
 }
});
test('zero volume inside required liquidity observations still rejects',()=>{
 for(const n of [29,81,97]){
  const x=makeCompleteFixture();x.bars.at(-1).volume=0;for(const bars of Object.values(x.panel))bars.at(-1).volume=0;
  assert.equal(runPullbackMethod(methods[n-1],x).status,'missing','method'+n);
 }
});
test('confirmed higher-low breach survives a later lower pivot in methods58 and98',()=>{
 const x=makeCompleteFixture(),len=x.calendar.length;
 const values=[...Array(len-14).fill(100),120,100,80,100,110,100,90,100,105,100,85,90,95,100];
 x.bars=values.map((c,i)=>({date:x.calendar[i],open:c,high:c+1,low:c-1,close:c,volume:1e6}));
 for(const n of [58,98]){const r=runPullbackMethod(methods[n-1],x);const z=n===58?r.components:r.components.higher_low;assert.equal(z.break_date,x.calendar.at(-4));assert.equal(z.status,'higher_low');}
});
test('five-session underwater stress condition is reachable on a declining fixture',()=>{
 const x=trim(makeCompleteFixture(),10);x.bars=x.calendar.map((date,i)=>({date,open:100-i,high:101-i,low:99-i,close:100-i,volume:1e6}));x.panel={A:x.bars};x.memberships=Object.fromEntries(x.calendar.map(d=>[d,['A']]));
 for(const scope of ['stock','market']){const r=catalog.find(r=>r.scope===scope&&r.horizon===5&&r.metric==='underwater_age'&&r.view==='threshold');assert.equal(runRecipe(r,x).components.flag,true);}
});
