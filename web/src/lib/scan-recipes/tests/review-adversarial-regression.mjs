import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runRecipe} from '../engine.mjs';
import {runPullbackMethod,EXTRA_CONTRACTS} from '../pullback-engine.mjs';
import catalog from '../catalog.mjs';
const methods=JSON.parse(readFileSync(new URL('../pullback-methods.json',import.meta.url),'utf8'));
const F=[];
const ok=(c,m)=>{if(!c)F.push(m)};
const near=(a,b,t=1e-9)=>Math.abs(a-b)<=t;

// Deterministic independent fixture (different construction from author's tests)
const calendar=[];for(let d=new Date('2021-01-04T00:00Z');calendar.length<600;d.setUTCDate(d.getUTCDate()+1))if(![0,6].includes(d.getUTCDay()))calendar.push(d.toISOString().slice(0,10));
const mk=closes=>closes.map((c,i)=>({date:calendar[i],open:c*0.998,high:c*1.012,low:c*0.987,close:c,volume:500000+((i*7919)%13)*50000}));
// hand-designed close path: drift up 300, sharp drop, partial recovery, flat tail
const closes=Array.from({length:600},(_,i)=> i<300?100+0.1*i : i<320?130-2*(i-299) : i<400?90+0.25*(i-319) : 110);
const bars=mk(closes);
const benchCloses=Array.from({length:600},(_,i)=>200*Math.exp(0.0001*i+0.08*Math.sin(i/17.3)+0.03*Math.sin(i/5.1)));
const bench=mk(benchCloses);
const panel={A:bars,B:mk(closes.map((c,i)=>c*(1.02+0.01*Math.sin(i/3.3)))),C:mk(closes.map((c,i)=>c*(0.97+0.012*Math.cos(i/4.1))))};
const memberships=Object.fromEntries(calendar.map(d=>[d,['A','B','C']]));
const input={symbol:'A',cutoff:calendar.at(-1),calendar,bars,panel,memberships,benchmark:bench,benchmark_symbol:'BENCH',sector_proxy:bench,sector_symbol:'SECTOR',source:'REVIEW INDEPENDENT FIXTURE',feed:'synthetic',price_basis:'invented',currency:'USD'};
const rec=(metric,view='level',scope='stock',horizon=21)=>catalog.find(r=>r.metric===metric&&r.view===view&&r.scope===scope&&r.horizon===horizon);
const run=n=>runPullbackMethod(methods.find(m=>m.number===n),input);

// ---- 1. Independent metric recomputation (my own math on my own fixture) ----
// drawdown h21: last close vs max of last 21 closes
const w=bars.slice(-21),peak21=Math.max(...w.map(b=>b.close));
ok(near(runRecipe(rec('drawdown'),input).components.value,100*(1-closes.at(-1)/peak21)),'drawdown recompute');
// flat tail of 200 sessions: last 21 closes all 110 -> drawdown 0, rebound 0
ok(near(runRecipe(rec('drawdown'),input).components.value,0),'flat-tail drawdown nonzero');
// underwater_age h21 flat: 0
ok(runRecipe(rec('underwater_age'),input).components.value===0,'underwater_age flat');
// rebound with trough inside window: craft window
{const x=structuredClone(input);const c2=[...closes];for(let i=580;i<595;i++)c2[i]=100;c2[598]=80;c2[599]=88;x.bars=mk(c2);
 ok(near(runRecipe(rec('rebound'),x).components.value,100*(88/80-1)),'rebound recompute');
 ok(near(runRecipe(rec('ma_distance'),x).components.value,100*(88/(c2.slice(-21).reduce((s,v)=>s+v,0)/21)-1)),'ma_distance recompute');}
// volatility h21: sample sd of 21 log returns * sqrt252 *100
{const r=[];for(let i=579;i<600;i++)r.push(Math.log(closes[i]/closes[i-1]));const m=r.reduce((s,v)=>s+v,0)/21;const sd=Math.sqrt(r.reduce((s,v)=>s+(v-m)**2,0)/20);
 ok(near(runRecipe(rec('volatility'),input).components.value,sd*Math.sqrt(252)*100,1e-9),'volatility recompute');}
// down_share h21 flat tail: all zero returns -> 0
ok(runRecipe(rec('down_share'),input).components.value===0,'down_share flat');
// gap+intraday reconstruction on my fixture
{const g=runRecipe(rec('gap_return'),input).components.value/100,i2=runRecipe(rec('intraday_return'),input).components.value/100,t=runRecipe(rec('return'),input).components.value/100;
 ok(near((1+g)*(1+i2),1+t,1e-10),'gap*intraday != total return');}
// relative_volume: current / mean(prior 21 volumes)
{const prior=bars.slice(-22,-1).map(b=>b.volume);const mean=prior.reduce((s,v)=>s+v,0)/21;
 ok(near(runRecipe(rec('relative_volume'),input).components.value,bars.at(-1).volume/mean),'relative_volume not lagged-correct');}

// ---- 2. View semantics ----
{const r=runRecipe(rec('return','change'),input);ok(near(r.components.change,r.components.value-r.components.prior_value),'change view arithmetic');
 const r2=runRecipe(rec('return','rank'),input);ok(r2.components.reference_count===252&&r2.components.current_excluded===true,'rank reference');
 ok(r2.components.reference_end===calendar[calendar.length-2],'rank ref end not n-2');
 ok(r2.components.reference_start===calendar[calendar.length-253],'rank ref start not n-253');
 // probe: alter a close INSIDE the reference window -> percentile must move
 const x=structuredClone(input);x.bars[calendar.length-100].close=5;x.bars[calendar.length-100].open=5;x.bars[calendar.length-100].high=5.1;x.bars[calendar.length-100].low=4.9;
 const r3=runRecipe(rec('return','rank'),x);ok(r3.components.percentile!==r2.components.percentile,'rank reference unresponsive to in-window change');
 // probe: alter a close BEFORE the reference window -> percentile must not change
 const y=structuredClone(input);y.bars[10].close=5;y.bars[10].open=5;y.bars[10].high=5.1;y.bars[10].low=4.9;
 const r4=runRecipe(rec('return','rank'),y);ok(near(r4.components.percentile,r2.components.percentile),'rank reference leaked pre-window data');
 const p=runRecipe(rec('drawdown','persistence'),input);ok(p.components.observation_sessions===5&&p.components.stress_readings+p.components.stress_readings>=0,'persistence shape');
 const t=runRecipe(rec('drawdown','threshold'),input);ok(t.components.threshold_validated===false&&typeof t.components.flag==='boolean','threshold honesty');}

// ---- 3. Fail-closed data requirements ----
{const x=structuredClone(input);x.bars=x.bars.slice(0,-1);ok(runRecipe(rec('return'),x).status==='missing','short bars accepted');
 const y=structuredClone(input);y.calendar=calendar.slice(0,-1);ok(runRecipe(rec('return'),y).status==='missing','calendar/bars length mismatch accepted');
 const z=structuredClone(input);delete z.memberships[calendar[calendar.length-2]];ok(runRecipe(rec('return','rank','market'),z).status==='missing','market rank accepted missing historical membership');
 const w2=structuredClone(input);w2.panel={A:bars,B:panel.B};ok(runRecipe(rec('return','rank','market'),w2).status==='missing','missing constituent accepted');
 const v=structuredClone(input);v.input=undefined;}

// ---- 4. Chapter method independent checks ----
// method 1 (market benchmark peak damage) on my fixture: peak = 130 at index 299, last=110
{const r=run(1);ok(r.status==='available','m1 missing: '+r.reason);
 ok(near(r.components.depth_pct,100*(1-benchCloses.at(-1)/Math.max(...benchCloses.slice(-252)))),'m1 depth mismatch (benchmark rolling252)');}
// method 51 stock: peak of last 252 closes
{const r=run(51);const expect=100*(1-closes.at(-1)/Math.max(...closes.slice(-252)));
 ok(near(r.components.depth_pct,expect),'m51 depth recompute');}
// method 4 (market scope, benchmark series): trough after peak in last-252 window
{const r=run(4);const p=Math.max(...benchCloses.slice(-252));const pi=benchCloses.lastIndexOf(p);const tail=benchCloses.slice(pi);const low=Math.min(...tail);
 ok(near(r.components.rebound_pct,100*(benchCloses.at(-1)/low-1)),'m4 rebound recompute');
 ok(near(r.components.gain_needed_pct,100*(p/benchCloses.at(-1)-1)),'m4 gain_needed recompute');
 const r54=run(54);const ps=Math.max(...closes.slice(-252));const pis=closes.lastIndexOf(ps);const lows=Math.min(...closes.slice(pis));
 ok(near(r54.components.rebound_pct,100*(closes.at(-1)/lows-1)),'m54 stock rebound recompute');}
// method 20/21/23/70/71 current_excluded on my fixture
for(const n of [20,21,23,70,71]){const r=run(n);ok(r.status==='available','m'+n+' missing: '+r.reason);ok(r.components.current_excluded===true,'m'+n+' current not excluded')}
// method 22 perfect correlation for scaled clones
{const wig=i=>100*Math.exp(0.0004*i+0.05*Math.sin(i/6.2));const x=structuredClone(input);
 x.panel={A:mk(Array.from({length:600},(_,i)=>wig(i))),B:mk(Array.from({length:600},(_,i)=>wig(i)*1.005)),C:mk(Array.from({length:600},(_,i)=>wig(i)*1.01))};
 const r=runPullbackMethod(methods.find(m=>m.number===22),x);ok(r.status==='available','m22 missing: '+r.reason);
 if(r.status==='available'){ok(near(r.components.mean_pairwise_correlation21,1,1e-9),'m22 correlation recompute');ok(r.components.pairs===3,'m22 pair count');}}
// method 86 beta: independent construction stock=1.5*bench+0.0002
{const x=structuredClone(input);const bc=[100];for(let i=1;i<600;i++)bc.push(bc.at(-1)*(1+0.0003+0.0005*Math.sin(i*0.7)));
 const sc=[100];for(let i=1;i<600;i++){const rb=bc[i]/bc[i-1]-1;sc.push(sc.at(-1)*(1+1.5*rb+0.0002))}
 // force recent peak at exactly session 400 so episode exists with >=253 pre sessions
 for(let i=401;i<600;i++)sc[i]=sc[400]*(1-0.0005*(i-400));
 x.bars=mk(sc);x.benchmark=mk(bc);
 const r=runPullbackMethod(methods.find(m=>m.number===86),x);
 ok(r.status==='available','m86 missing: '+r.reason);
 if(r.status==='available'){
  // fully independent recomputation: locate rolling-252 peak, fit OLS on 252 returns ending at peak, compound residuals after
  const sc2=x.bars.map(b=>b.close),bc2=x.benchmark.map(b=>b.close);
  const pk=Math.max(...sc2.slice(-252)),pi=sc2.lastIndexOf(pk);
  const sr=sc2.map((v,i)=>i?v/sc2[i-1]-1:0),br=bc2.map((v,i)=>i?v/bc2[i-1]-1:0);
  const fa=sr.slice(pi-251,pi+1),fd=br.slice(pi-251,pi+1);
  const ma=fa.reduce((s,v)=>s+v,0)/252,md=fd.reduce((s,v)=>s+v,0)/252;
  const beta=fa.reduce((s,v,i)=>s+(v-ma)*(fd[i]-md),0)/fd.reduce((s,v)=>s+(v-md)**2,0);
  const intercept=ma-beta*md;
  let comp=1;for(let i=pi+1;i<600;i++)comp*=1+(sr[i]-(intercept+beta*br[i]));
  ok(near(r.components.beta,beta,1e-12),'m86 beta independent recompute');
  ok(near(r.components.intercept_daily,intercept,1e-12),'m86 intercept independent recompute');
  ok(near(r.components.compounded_residual_return_pct,100*(comp-1),1e-9),'m86 residual independent recompute');
  ok(r.components.fit_sessions===252,'m86 fit size');
  ok(r.components.fit_end===r.components.episode_start,'m86 fit window ends at peak session (boundary day included in fit)');}}
// method 47 strict-prior normalization on my fixture
{const x=structuredClone(input);x.memberships=memberships;const r=runPullbackMethod(methods.find(m=>m.number===47),{...x,analogue_count:3});
 ok(r.status==='available','m47 missing: '+r.reason);
 if(r.status==='available'){for(const z of r.components.nearest)ok(z.fit_end<z.date&&z.date<x.cutoff,'m47 normalization not strictly prior');
  ok(r.components.forward_outcomes_used===false,'m47 forward flag');}}
// method 50 strictly prior
{const r=run(50);ok(r.status==='available','m50 missing: '+r.reason);if(r.status==='available'){ok(r.components.readings.every(z=>z.current_excluded),'m50 current included');ok(r.components.fit_end<r.components.as_of,'m50 fit window not strictly prior');}}
// method 96 forward outcomes strictly before cutoff
{const x={...input,analogue_depth_tolerance_pct:3};const r=runPullbackMethod(methods.find(m=>m.number===96),x);
 ok(r.status==='available','m96 missing: '+r.reason);
 if(r.status==='available'){for(const z of r.components.matches)ok(z.forward_end_date<input.cutoff,'m96 forward outcome at/after cutoff');}}
// method 90 timing edge cases
{const sessions=calendar.map(d=>({date:d,open_at:d+'T14:30:00Z',close_at:d+'T21:00:00Z'}));
 const base={...input,sessions,cutoff_at:input.cutoff+'T21:00:00Z'};
 // during-session release -> same session, honestly labeled
 let x={...base,earnings_releases:[{release_at:calendar.at(-1)+'T16:00:00Z',source:'REVIEW'}]};
 let r=runPullbackMethod(methods.find(m=>m.number===90),x);
 ok(r.status==='available'&&r.components.events[0].assigned_session===calendar.at(-1)&&r.components.events[0].timing==='during_session'&&r.components.events[0].causality_claimed===false,'m90 during-session assignment');
 // exactly at open -> before_or_at_open
 x={...base,earnings_releases:[{release_at:calendar.at(-1)+'T14:30:00Z',source:'REVIEW'}]};
 r=runPullbackMethod(methods.find(m=>m.number===90),x);
 ok(r.components.events[0].timing==='before_or_at_open'&&r.components.events[0].assigned_session===calendar.at(-1),'m90 at-open assignment');
 // between previous close and next open (evening) -> next session gap
 x={...base,earnings_releases:[{release_at:calendar.at(-2)+'T22:00:00Z',source:'REVIEW'}]};
 r=runPullbackMethod(methods.find(m=>m.number===90),x);
 ok(r.components.events[0].assigned_session===calendar.at(-1),'m90 evening release -> next session');
 // after final close, before cutoff_at
 x={...base,cutoff_at:input.cutoff+'T23:00:00Z',earnings_releases:[{release_at:input.cutoff+'T22:00:00Z',source:'REVIEW'}]};
 r=runPullbackMethod(methods.find(m=>m.number===90),x);
 ok(r.components.events[0].status==='awaiting_next_session','m90 post-close pre-cutoff status');
 // future release rejected from assignment
 x={...base,earnings_releases:[{release_at:'2030-01-01T00:00:00Z',source:'REVIEW'}]};
 r=runPullbackMethod(methods.find(m=>m.number===90),x);
 ok(r.components.events[0].status==='not_published_by_cutoff','m90 future release');
 // weekend release (Saturday) -> Monday session
 {const fri=calendar.at(-1);const sat=new Date(fri+'T00:00:00Z');sat.setUTCDate(sat.getUTCDate()+1); // may be Sat
  x={...base,earnings_releases:[{release_at:fri+'T23:30:00Z',source:'REVIEW'}],cutoff_at:fri+'T23:59:00Z'};
  r=runPullbackMethod(methods.find(m=>m.number===90),x);
  ok(r.components.events[0].status==='awaiting_next_session','m90 late-night release');}}
// method 91 edge cases
{const m91=methods.find(m=>m.number===91);
 const base={...input,raw_bars:bars,adjusted_bars:bars,adjustment_basis:'split_and_dividend',action_discontinuity_tolerance_pct:20,cutoff_at:input.cutoff+'T22:00:00Z',actions_coverage:{start:calendar[0],end:input.cutoff,source:'REVIEW',complete:true}};
 let r=runPullbackMethod(m91,{...base,corporate_actions:[]});
 ok(r.status==='available'&&r.components.block_severity_rankings===false,'m91 clean baseline blocks');
 // unpublished action does not block
 r=runPullbackMethod(m91,{...base,corporate_actions:[{date:calendar.at(-2),kind:'split',source:'REVIEW',published_at:'2030-01-01T00:00:00Z'}]});
 ok(r.components.block_severity_rankings===false&&r.components.events[0].status==='not_published_by_cutoff','m91 unpublished action blocks');
 // dividend under split-only basis blocks
 r=runPullbackMethod(m91,{...base,adjustment_basis:'split',corporate_actions:[{date:calendar.at(-2),kind:'dividend',source:'REVIEW',published_at:calendar.at(-3)+'T20:00:00Z'}]});
 ok(r.components.block_severity_rankings===true,'m91 split-only dividend not blocked');
 // incomplete coverage blocks even with no actions
 r=runPullbackMethod(m91,{...base,corporate_actions:[],actions_coverage:{start:calendar[0],end:input.cutoff,source:'REVIEW',complete:false}});
 ok(r.components.block_severity_rankings===true,'m91 incomplete coverage not blocked');
 // action at first session -> unresolved_missing_prior_close blocks
 r=runPullbackMethod(m91,{...base,corporate_actions:[{date:calendar[0],kind:'split',source:'REVIEW',published_at:calendar[0]+'T20:00:00Z'}]});
 ok(r.components.block_severity_rankings===true,'m91 day-0 action not blocked');}
// method 57 causal leg pairing: confirmation dates must be <= cutoff, observed_as_of honest
{const x={...input,pivot_policy:{left:3,right:3}};const r=runPullbackMethod(methods.find(m=>m.number===57),x);
 ok(r.status==='available','m57 missing: '+r.reason);
 if(r.status==='available'&&r.components.legs.length){for(const leg of r.components.legs){ok(leg.observed_as_of<=input.cutoff,'m57 leg observed after cutoff');ok(leg.low.confirmed_date<=input.cutoff&&leg.high.confirmed_date<=input.cutoff,'m57 pivot confirmed after cutoff')}}}
// method 58 higher-low break only after confirmation
{const x={...input,pivot_policy:{left:2,right:2}};const r=runPullbackMethod(methods.find(m=>m.number===58),x);
 ok(r.status==='available','m58 missing: '+r.reason);
 if(r.components&&r.components.break_date)ok(r.components.first_eligible_breach_after<r.components.break_date||r.components.first_eligible_breach_after<=r.components.break_date,'m58 break before confirmation');}
// method 83 exhaustion pending at boundary
{const r=run(83);ok(r.status==='available','m83 missing: '+r.reason);
 if(r.status==='available'){for(const e of r.components.events){if(e.status==='pending_two_closes')ok(e.confirmation_date===null,'m83 pending has confirmation');else ok(e.confirmation_date<=input.cutoff,'m83 confirmation after cutoff')}}}
// INPUT_CONTRACTS completeness probe: methods 43-46 need benchmark but declare no contract
{for(const n of [43,44,45,46]){const x=structuredClone(input);delete x.benchmark;delete x.benchmark_symbol;
  const r=runPullbackMethod(methods.find(m=>m.number===n),x);
  ok(r.status==='missing'&&/benchmark/.test(r.reason),'m'+n+' without benchmark not blocked');
  ok(['benchmark','benchmark_symbol'].every(k=>(EXTRA_CONTRACTS[n]||[]).includes(k)),'m'+n+' benchmark contract absent after fix');}
 console.log('REGRESSION: methods43-46nowdeclarebenchmark+symbol contracts. Probe adapted only for the fixed expected state; original independent suite retained.');
 const core84=methods.find(m=>m.number===84);const x=structuredClone(input);delete x.benchmark;
 const r84=runPullbackMethod(core84,x);ok(r84.status==='missing','m84 without benchmark not blocked');
 for(const n of [1,2,3,4,5,6,24,25,42,84,87])ok(['benchmark','benchmark_symbol'].every(k=>(EXTRA_CONTRACTS[n]||[]).includes(k)),'m'+n+' incomplete benchmark contract');ok(['sector_proxy','sector_symbol'].every(k=>(EXTRA_CONTRACTS[85]||[]).includes(k)),'m85 incomplete sector contract');}
// breadth honest blocking: chapter market method missing member
{const x=structuredClone(input);delete x.panel.C;x.memberships={[input.cutoff]:['A','B','C']};
 const r=runPullbackMethod(methods.find(m=>m.number===8),x);
 ok(r.status==='missing','m8 missing member accepted');
 const y=structuredClone(input);const r2=runPullbackMethod(methods.find(m=>m.number===8),y);
 ok(r2.status==='available'&&r2.coverage.computed===3&&r2.coverage.missing===0,'m8 coverage counts');}
// provenance honesty on every output class
{const a=runRecipe(rec('return'),input);ok(a.evidence_level==='E0'&&a.provenance.price_basis_certified===false,'recipe provenance honesty');
 const b=run(51);ok(b.evidence_level==='E0'&&b.provenance.price_basis_certified===false&&b.implementation.includes('offline'),'method provenance honesty');
 const miss=runRecipe(rec('return'),{...input,cutoff:'2020-01-01'});ok(miss.status==='missing'&&miss.components===null&&typeof miss.reason==='string','missing state shape');}

console.log(F.length?('FAILURES:\n'+F.join('\n')):'ALL ADVERSARIAL PROBES PASSED');
