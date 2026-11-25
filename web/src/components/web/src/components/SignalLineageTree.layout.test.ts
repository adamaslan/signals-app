import {describe,it,expect,vi}from'vitest';
import{writeFileSync}from'node:fs';
import {renderToStaticMarkup}from'react-dom/server';
import React,{createElement}from'react';
Object.assign(globalThis,{React});
const {runs}=vi.hoisted(()=>({runs:Array.from({length:50},(_,i)=>({id:i+1,ticker:'SYNTHETIC',period:'3mo',resolvedPeriod:'3mo',noLlm:true,signal:i%2?'buy':'sell',confidence:.7,aiDegraded:false,ts:Date.UTC(2026,10,24)-i*86400000}))}));
vi.mock('dexie-react-hooks',()=>({useLiveQuery:()=>runs}));
import{SignalLineageTree}from'./SignalLineageTree';
describe('long signal lineage mobile layout',()=>{
 it('bounds indentation while retaining every chronological streak and current marker',()=>{
 const html=renderToStaticMarkup(createElement(SignalLineageTree,{ticker:'SYNTHETIC'}));
 if(process.env.LINEAGE_RENDER_PATH)writeFileSync(process.env.LINEAGE_RENDER_PATH,html);
 const indentation=[...html.matchAll(/padding-left:(\d+)px/g)].map(x=>Number(x[1]));
 expect(Math.max(...indentation)).toBeLessThanOrEqual(60);
 expect((html.match(/<li[ >]/g)||[])).toHaveLength(50);
 expect(html).toContain('50');expect((html.match(/>current</g)||[])).toHaveLength(1);
 });
});
