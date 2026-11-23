import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runPullbackMethod} from '../pullback-engine.mjs';
import {makeCompleteFixture} from './fixtures.mjs';
const method=JSON.parse(readFileSync(new URL('../pullback-methods.json',import.meta.url)))[47];
for(const [name,mutate] of [
 ['equity close after research cutoff',x=>{x.cutoff_at=x.cutoff+'T15:00:00Z'}],
 ['timezone-free research cutoff',x=>{x.cutoff_at=x.cutoff+'T21:00:00'}],
 ['timezone-free session close',x=>{x.sessions.at(-1).close_at=x.cutoff+'T21:00:00'}],
 ['reversed session close times',x=>{x.sessions.at(-2).close_at=x.cutoff+'T22:00:00Z'}]
]) test('credit comparison rejects '+name,()=>{
 const x=makeCompleteFixture();mutate(x);const result=runPullbackMethod(method,x);
 assert.equal(result.status,'missing',JSON.stringify(result.components));
 assert.equal(result.components,null);
});
test('credit comparison accepts observed session exactly at cutoff and equivalent offset',()=>{
 for(const offset of [false,true]){
  const x=makeCompleteFixture();if(offset)x.cutoff_at=x.cutoff+'T17:00:00-04:00';
  const result=runPullbackMethod(method,x);assert.equal(result.status,'available',result.reason);
  assert.ok(Date.parse(result.components.credit_latest.published_at)<=Date.parse(x.cutoff_at));
 }
});
