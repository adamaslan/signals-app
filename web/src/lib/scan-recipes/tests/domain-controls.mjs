import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runPullbackMethod} from '../pullback-engine.mjs';
const methods=JSON.parse(readFileSync(new URL('../pullback-methods.json',import.meta.url)));
import {makeCompleteFixture} from './fixtures.mjs';
const rich=makeCompleteFixture();
const run=(n,x=rich)=>runPullbackMethod(methods[n-1],x),copy=()=>structuredClone(rich);
const bars=(values)=>values.map((c,i)=>({date:rich.calendar[i],open:c,high:c+1,low:c-1,close:c,volume:100}));
let x=copy();delete x.beginning_weights;assert.equal(run(36,x).reason_code,'missing_or_invalid_input_contract');
x=copy();delete x.earnings_releases;assert.equal(run(90,x).reason_code,'missing_or_invalid_input_contract');
x=copy();delete x.pivot_policy;assert.equal(run(58,x).reason_code,'missing_or_invalid_input_contract');
x=copy();delete x.memberships[rich.calendar.at(-60)];assert.equal(run(47,x).status,'missing');
x=copy();x.thesis_checks={earnings:{status:'PRESENT'},balance_sheet:{status:'MISSING'},news:{status:'MISSING'}};assert.equal(run(99,x).status,'missing','PRESENT without dated source must not be invented');
x=copy();x.thesis_checks.earnings={status:'PRESENT',source:'INVENTED',published_at:'2099-01-01T00:00:00Z'};assert.equal(run(99,x).status,'missing','future source rejected');
x=copy();x.earnings_releases=[{release_at:rich.calendar.at(-2)+'T21:01:00Z',source:'INVENTED'}];assert.equal(run(90,x).components.events[0].assigned_session,rich.cutoff);
x=copy();x.earnings_releases=[{release_at:rich.cutoff+'T21:01:00Z',source:'INVENTED'}];assert.equal(run(90,x).components.events[0].status,'not_published_by_cutoff');
x=copy();x.cutoff_at=rich.cutoff+'T22:00:00Z';x.earnings_releases=[{release_at:rich.cutoff+'T21:01:00Z',source:'INVENTED'}];assert.equal(run(90,x).components.events[0].status,'awaiting_next_session');
x=copy();x.actions_coverage.complete=false;assert.equal(run(91,x).components.block_severity_rankings,true);
x=copy();x.corporate_actions=[{date:rich.calendar.at(-2),kind:'dividend',source:'INVENTED',published_at:rich.calendar.at(-3)+'T20:00:00Z'}];x.adjustment_basis='split';assert.equal(run(91,x).components.block_severity_rankings,true,'dividend not certified by split-only basis');
x=copy();x.corporate_actions=[{date:rich.calendar.at(-2),kind:'split',source:'INVENTED',published_at:rich.calendar.at(-3)+'T20:00:00Z'}];x.adjusted_bars[x.adjusted_bars.length-2].close*=.5;x.adjusted_bars[x.adjusted_bars.length-2].open=x.adjusted_bars[x.adjusted_bars.length-2].close;x.adjusted_bars[x.adjusted_bars.length-2].high=x.adjusted_bars[x.adjusted_bars.length-2].close+1;x.adjusted_bars[x.adjusted_bars.length-2].low=x.adjusted_bars[x.adjusted_bars.length-2].close-1;assert.equal(run(91,x).components.block_severity_rankings,true,'unresolved adjusted discontinuity blocks rankings');
x=copy();x.peers.A=structuredClone(x.peers.A);x.peers.A.at(-1).close*=.999;assert.equal(run(88,x).status,'missing','conflicting own peer entry');
assert.ok(run(96).components.matches.every(z=>z.forward_end_date<rich.cutoff),'strictly before research cutoff');
for(const z of run(47).components.nearest)assert.ok(z.fit_end<z.date,'strict prior candidate scaling');
// Known daily rebalance path. Each day has zero average return; buy-and-hold differs.
x=copy();const len=x.calendar.length,cl=[...Array(len-2).fill(100),110,99],dl=[...Array(len-2).fill(100),90,99];x.panel={A:bars(cl),B:bars(dl)};x.memberships=Object.fromEntries(x.calendar.map(d=>[d,['A','B']]));x.benchmark=bars(Array(len).fill(100));assert.ok(Math.abs(run(7,x).components.equal_weight_total_return_pct)<1e-10);
// Known largest-weight exclusion sensitivity and weight/count disagreement.
x=copy();const names=Array.from({length:12},(_,i)=>'S'+String(i).padStart(2,'0'));x.panel=Object.fromEntries(names.map((s,i)=>[s,bars([...Array(len-1).fill(100),i<5?80:110])]));x.memberships={[x.cutoff]:names};x.beginning_weights={[x.cutoff]:Object.fromEntries(names.map(s=>[s,1/12]))};let z=run(37,x);assert.ok(Math.abs(z.components.full_return_pct+2.5)<1e-10);assert.ok(Math.abs(z.components.excluded5_return_pct-10)<1e-10);assert.equal(z.components.removed.length,5);
x.beginning_weights[x.cutoff]=Object.fromEntries(names.map((s,i)=>[s,i<5?.15:.25/7]));z=run(38,x);assert.ok(Math.abs(z.components.weighted_below50_share_pct-75)<1e-10);assert.ok(Math.abs(z.components.count_below50_share_pct-100*5/12)<1e-10);
// Beta/intercept recovered from252 pre-episode returns; post-episode residuals are zero.
x=copy();let bc=100,sc=100;const bp=[bc],sp=[sc];for(let i=1;i<len;i++){const r=i<len-25?.001+.0004*Math.sin(i):-.005-.0002*Math.sin(i);bc*=1+r;sc*=1+2*r+.0001;bp.push(bc);sp.push(sc)}x.bars=bars(sp);x.benchmark=bars(bp);z=run(86,x);assert.equal(z.status,'available',z.reason);assert.ok(Math.abs(z.components.beta-2)<1e-9);assert.ok(Math.abs(z.components.intercept_daily-.0001)<1e-9);assert.ok(Math.abs(z.components.compounded_residual_return_pct)<1e-8);
// A confirmed higher low is not dated until the fixed right bars have been observed.
x=copy();x.bars=bars([...Array(len-12).fill(100),120,100,80,100,110,100,90,100,105,100,85,90]);z=run(58,x);assert.equal(z.components.status,'higher_low');assert.equal(z.components.break_date,x.calendar.at(-2));assert.ok(z.components.first_eligible_breach_after<z.components.break_date);
// Historical depth comparisons preserve the all-history anchor, separate from rolling252.
x=copy();const cc=Array.from({length:len},(_,i)=>100+5*Math.sin(i/4));cc[0]=1000;x.bars=bars(cc);assert.notEqual(run(92,x).components.rolling252.depth_pct,run(92,x).components.all_history.depth_pct);assert.equal(run(93,x).status,'missing','no20 recovered all-history episodes after old unreachable peak');
console.log('Domain controls passed: explicit missing contracts, event-time assignment, future publication rejection, action-contamination blocking, strict forward cutoff, pre-candidate normalization, daily rebalancing, lagged-weight exclusion, weight/count disagreement, known beta fit, causally confirmed pivots and matched episode anchors. Invented fixtures only.');
