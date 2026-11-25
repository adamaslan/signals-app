import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runPullbackMethod,SUPPORTED_METHODS} from '../pullback-engine.mjs';
const methods=JSON.parse(readFileSync(new URL('../pullback-methods.json',import.meta.url)));
const calendar=[];for(let d=new Date('2023-01-02Z');calendar.length<300;d.setUTCDate(d.getUTCDate()+1))if(![0,6].includes(d.getUTCDay()))calendar.push(d.toISOString().slice(0,10));
const bars=calendar.map((date,i)=>{const c=i===299?90:100+Math.sin(i/7);return{date,open:c,high:c+1,low:c-1,close:c,volume:100}});
const input={symbol:'INVENTED',calendar,cutoff:calendar.at(-1),bars,benchmark_symbol:'INVENTED_BENCH',benchmark:bars,sector_proxy:bars,sector_symbol:'INVENTED_SECTOR',panel:{A:bars,B:bars},memberships:{[calendar.at(-1)]:['A','B']},source:'INVENTED FIXTURE',feed:'synthetic',price_basis:'invented coherent OHLCV',currency:'USD'};
const run=(n,x=input)=>runPullbackMethod(methods[n-1],x);
for(const n of SUPPORTED_METHODS){const r=run(n);assert.equal(r.algorithm_implemented,true);if(r.status!=='available')assert.ok(['insufficient_history_or_sample','missing_or_invalid_input_contract','undefined_statistic'].includes(r.reason_code),`${n}: ${r.reason}`);JSON.stringify(r)}
let r=run(10);assert.equal(Object.values(r.components.disjoint_member_share_pct).reduce((a,b)=>a+b),100);assert.equal(r.components.disjoint_member_share_pct['10_to_under20'],100);
assert.equal(run(10,{...input,panel:{A:bars}}).status,'missing');
assert.equal(run(1,{...input,benchmark:null}).status,'missing');
assert.equal(run(51,{...input,bars:bars.slice(1)}).status,'missing');
assert.equal(run(99).status,'missing');assert.equal(run(99).reason_code,'missing_or_invalid_input_contract');assert.equal(run(56).components.final_trough_known,false);
assert.ok(Math.abs(run(73).components.reconstruction_error)<1e-12);
const equal=bars.map((b,i)=>({...b,close:100,open:100,high:101,low:99}));assert.equal(run(18,{...input,panel:{A:equal,B:equal}}).components.median_rsi14,50);
const latestTrough=bars.map((b,i)=>({...b,close:i<298?100:90,open:i<298?100:90,high:i<298?101:91,low:i<298?99:89}));assert.equal(run(56,{...input,bars:latestTrough}).components.trough_date,calendar.at(-1));
console.log(JSON.stringify({supported_chapter_calculators:SUPPORTED_METHODS.length,all_supported_dispatch_checked:true,regressions:['disjoint breadth denominator','missing member blocked','benchmark distinct','missing session','missing contract distinct from unimplemented algorithm','gap reconstruction','flat RSI neutral','latest equal trough'],evidence:'E0 invented fixture only',production_integration:false},null,2));
