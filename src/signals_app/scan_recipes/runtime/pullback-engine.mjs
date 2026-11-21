// Chapter-specific offline calculators. Shares the recipe input validation contract.
import {validateFrame} from './engine.mjs';
import {EXTRA_METHODS,EXTRA_CONTRACTS,runExtraMethod,exhaustion} from './additional-methods.mjs';
import {MARKET_METHODS,MARKET_CONTRACTS,runMarketMethod} from './market-methods.mjs';
import {FINAL_METHODS,FINAL_CONTRACTS,runFinalMethod} from './final-methods.mjs';
const INPUT_CONTRACTS={...EXTRA_CONTRACTS,...MARKET_CONTRACTS,...FINAL_CONTRACTS};
for(const n of [1,2,3,4,5,6,24,25,42,43,44,45,46])INPUT_CONTRACTS[n]=[...new Set(['benchmark','benchmark_symbol',...(INPUT_CONTRACTS[n]||[])])];
for(const n of [84,87])INPUT_CONTRACTS[n]=['benchmark','benchmark_symbol'];
INPUT_CONTRACTS[85]=['sector_proxy','sector_symbol'];
export {INPUT_CONTRACTS as EXTRA_CONTRACTS};
export const SUPPORTED_METHODS=[1,2,3,4,5,6,8,9,10,11,13,14,15,17,18,24,26,27,28,29,51,54,55,56,63,66,67,73,74,79,82,92,20,21,22,23,25,39,42,52,53,62,64,65,68,70,71,72,76,77,80,81,84,85,87,...EXTRA_METHODS,...MARKET_METHODS,...FINAL_METHODS];
const avg=a=>a.reduce((s,x)=>s+x,0)/a.length,sum=a=>a.reduce((s,x)=>s+x,0);
const q=(a,p)=>{const z=[...a].sort((a,b)=>a-b),i=(z.length-1)*p,j=Math.floor(i);return z[j]+(z[Math.min(j+1,z.length-1)]-z[j])*(i-j)};
const div=(a,b)=>{if(!(b>0))throw Error('undefined nonpositive denominator');return a/b};
function frame(bars,calendar){
 const b=validateFrame(bars,calendar),c=b.map(b=>b.close),r=c.map((v,i)=>i?v/c[i-1]-1:0),tr=b.map((v,i)=>i?Math.max(v.high-v.low,Math.abs(v.high-c[i-1]),Math.abs(v.low-c[i-1])):v.high-v.low);
 const peak=Math.max(...c.slice(-252)),pi=c.lastIndexOf(peak),tail=c.slice(pi),low=Math.min(...tail),ti=pi+tail.lastIndexOf(low);
 return{b,c,r,tr,peak,pi,low,ti,last:c.at(-1),age:c.length-1-pi,depth:Math.round(100*(1-c.at(-1)/peak)*1e12)/1e12};
}
const stdev=a=>{const m=avg(a);return Math.sqrt(sum(a.map(v=>(v-m)**2))/(a.length-1))};
const rsiValues=c=>{let up=0,dn=0;return c.map((v,i)=>{if(!i)return 50;const d=v-c[i-1];if(i===1){up=Math.max(d,0);dn=Math.max(-d,0)}else{up=up*13/14+Math.max(d,0)/14;dn=dn*13/14+Math.max(-d,0)/14}return !dn?(up?100:50):100-100/(1+up/dn)})};
function one(n,x){
 const {b,c,r,tr,peak,pi,low,ti,last,age,depth}=x,N=c.length,base={peak_date:b[pi].date,latest_close:last,depth_pct:depth,sessions:age};
 switch(n){
 case 1:case 51:return base;
 case 2:return{peak_date:b[pi].date,sessions:age,calendar_days:(Date.parse(b.at(-1).date)-Date.parse(b[pi].date))/86400000};
 case 3:return{...base,depth_pct_per_session:age?depth/age:0};
 case 4:case 54:return{...base,trough_date:b[ti].date,rebound_pct:100*(last/low-1),loss_recovered_pct:peak>low?100*(last-low)/(peak-low):0,gain_needed_pct:100*(peak/last-1),no_decline:peak===low};
 case 5:case 55:return{...base,loss_atr14:div(peak-last,avg(tr.slice(-14)))};
 case 52:case 6:{const burden=sum(c.slice(pi+1).map(v=>100*(1-v/peak)));return{...base,burden_percent_sessions:burden,mean_underwater_depth_pct:age?burden/age:0}}
 case 56:{const a=ti-pi,d=100*(1-low/peak);return{worst_depth_pct:d,sessions_to_observed_trough:a,depth_pct_per_session:a?d/a:0,trough_date:b[ti].date,final_trough_known:false}}
 case 53:{const high=Math.max(...b.slice(pi).map(z=>z.high)),hi=b.map(z=>z.high).lastIndexOf(high);if(hi===N-1)throw Error('highest intraday high has no later observed session low');const low=Math.min(...b.slice(hi+1).map(z=>z.low)),li=b.findIndex((z,i)=>i>hi&&z.low===low);return{closing:base,highest_episode_intraday_high:high,lowest_later_intraday_low:low,intraday_decline_pct:100*(1-low/high),intraday_peak_date:b[hi].date,intraday_trough_date:b[li].date,same_bar_order_inference:false}}
 case 62:{let dv=0,vol=0,prior=null,reclaim=null;for(let i=pi;i<N;i++){dv+=(b[i].high+b[i].low+c[i])/3*b[i].volume;vol+=b[i].volume;const v=div(dv,vol);if(prior!==null&&prior<=0&&c[i]>v&&reclaim===null)reclaim=b[i].date;prior=c[i]-v}const v=div(dv,vol);return{anchor_date:b[pi].date,anchored_typical_vwap:v,current_gap_pct:100*(last/v-1),first_reclaim_date:reclaim}}
 case 64:case 42:{const slope=i=>100*(avg(c.slice(i-199,i+1))/avg(c.slice(i-219,i-19))-1),state=i=>(c[i]>=avg(c.slice(i-199,i+1))?'above':'below')+'_'+(slope(i)>=0?'rising':'falling'),transitions=[];for(let i=220;i<N;i++){const previous=n===42?state(i-1):Math.sign(slope(i-1)),current=n===42?state(i):Math.sign(slope(i));if(previous!==current)transitions.push({date:b[i].date,from:previous,to:current})}return{sma200_slope20_pct:slope(N-1),...(n===42?{state:state(N-1)}:{}),transitions}}
 case 65:{const state=i=>{const a=avg(c.slice(i-19,i+1)),d=avg(c.slice(i-49,i+1)),e=avg(c.slice(i-199,i+1));return c[i]>a&&a>d&&d>e?'bullish':c[i]<a&&a<d&&d<e?'bearish':'partial'},transitions=[];for(let i=200;i<N;i++)if(state(i-1)==='bullish'&&state(i)!=='bullish')transitions.push({date:b[i].date,from:'bullish',to:state(i)});return{ordering_state:state(N-1),first_bullish_breakdown:transitions[0]||null,transitions}}
 case 68:{const rs=rsiValues(c);let below=null,recovery=null;for(let i=1;i<N;i++){if(rs[i]<30&&rs[i-1]>=30){below=b[i].date;recovery=null}if(below&&rs[i]>=30&&rs[i-1]<30)recovery=b[i].date}return{rsi14:rs.at(-1),below30:rs.at(-1)<30,most_recent_below30_entry:below,recovery_date:recovery,pending_recovery:!!below&&!recovery,seed:'ewm alpha1/14, first observed change; flat=50'}}
 case 70:{if(N<274)throw Error('274 sessions required for252 prior rolling volatility readings');const vol=i=>stdev(r.slice(i-20,i+1).map(v=>Math.log1p(v)))*Math.sqrt(252)*100,current=vol(N-1),prior=Array.from({length:252},(_,j)=>vol(N-253+j));return{volatility21_pct_annualized:current,prior252_percentile:100*prior.filter(v=>v<=current).length/252,volatility63_pct_annualized:stdev(r.slice(-63).map(v=>Math.log1p(v)))*Math.sqrt(252)*100,current_excluded:true}}
 case 71:{if(N<254)throw Error('254 sessions required for252 prior returns');const k=Math.ceil(63*.05),tail=[...r.slice(-63)].sort((a,b)=>a-b).slice(0,k),prior=r.slice(-253,-1);return{worst_tail_mean_return_pct:100*avg(tail),tail_count:k,return_window:63,today_return_pct:100*r.at(-1),prior252_return_percentile:100*prior.filter(v=>v<=r.at(-1)).length/252,current_excluded:true}}
 case 76:{const gaps=[];for(let i=N-21;i<N;i++){const atr=avg(tr.slice(i-14,i));if(b[i].open<c[i-1]-atr)gaps.push({date:b[i].date,overnight_log_return:Math.log(b[i].open/c[i-1])})}return{gap_count:gaps.length,negative_overnight_log_sum:sum(gaps.map(g=>g.overnight_log_return)),gaps}}
 case 77:{const support=Math.min(...c.slice(-61,-1)),volume=avg(b.slice(-21,-1).map(b=>b.volume));return{prior60_closing_support:support,close_below_support:last<support,negative_daily_return:r.at(-1)<0,volume_to_prior20:div(b.at(-1).volume,volume),heavy_volume_breakdown:last<support&&r.at(-1)<0&&b.at(-1).volume>1.5*volume}}
 case 80:{const z=c.slice(-22),m=avg(z),t=10.5,den=sum(z.map((_,i)=>(i-t)**2)),slope=sum(z.map((v,i)=>(i-t)*(v-m)))/den;let obv=0;for(let i=N-21;i<N;i++)obv+=Math.sign(r[i])*b[i].volume;const normalized=div(obv,sum(b.slice(-21).map(b=>b.volume)));return{price_slope_per_session:slope,normalized_obv_change:normalized,disagreement:Math.sign(slope)!==Math.sign(normalized),return_sessions:21,price_slope_closes:22}}
 case 81:{const dollar=b.map(z=>z.close*z.volume),ill=b.map((z,i)=>i?div(Math.abs(r[i]),dollar[i]):0);return{median_dollar_volume5:q(dollar.slice(-5),.5),prior20_median_dollar_volume:q(dollar.slice(-25,-5),.5),median_turnover_ratio:div(q(dollar.slice(-5),.5),q(dollar.slice(-25,-5),.5)),illiquidity5:avg(ill.slice(-5)),prior20_illiquidity:avg(ill.slice(-25,-5))}}
 case 63:return{gaps_pct:Object.fromEntries([20,50,200].map(w=>[w,100*(last/avg(c.slice(-w))-1)]))};
 case 66:{const change=last-c[N-21],path=sum(c.slice(-20).map((v,i)=>Math.abs(v-c[N-21+i])));return{efficiency:div(Math.abs(change),path),direction:Math.sign(change),sessions:20}}
 case 67:{let streak=0;for(let i=N-1;i>0&&r[i]<0;i--)streak++;return{trailing_down_streak:streak,down_share_since_peak_pct:age?100*r.slice(pi+1).filter(v=>v<0).length/age:0,episode_sessions:age}}
 case 72:case 24:{const a=r.slice(-21),dn=Math.sqrt(avg(a.map(v=>Math.min(v,0)**2)))*Math.sqrt(252)*100,up=Math.sqrt(avg(a.map(v=>Math.max(v,0)**2)))*Math.sqrt(252)*100;return{downside_semideviation_pct_annualized:dn,upside_semideviation_pct_annualized:up,difference_pct_points:dn-up}}
 case 25:case 73:{const a=sum(b.slice(pi+1).map((v,i)=>Math.log(v.open/c[pi+i]))),d=sum(b.slice(pi+1).map(v=>Math.log(v.close/v.open))),total=Math.log(last/peak);return{overnight_log_return:a,intraday_log_return:d,total_log_return:total,reconstruction_error:a+d-total,sessions:age}}
 case 74:return{range_to_prior20_true_range:div(tr.at(-1),avg(tr.slice(-21,-1))),close_location_pct:100*div(last-b.at(-1).low,b.at(-1).high-b.at(-1).low)};
 case 79:{let dn=0,up=0,flat=0;for(let i=N-21;i<N;i++){if(r[i]<0)dn+=b[i].volume;else if(r[i]>0)up+=b[i].volume;else flat+=b[i].volume}return{down_share_volume:dn,up_share_volume:up,flat_share_volume:flat,down_up_ratio:div(dn,up)}}
 case 82:{const a=b.slice(-21),v=a.map(v=>v.volume),i=v.indexOf(Math.max(...v));return{max_day_share_of_volume_pct:100*div(v[i],sum(v)),date:a[i].date,daily_return_pct:100*r[N-21+i]}}
 case 92:{const p=Math.max(...c),i=c.lastIndexOf(p);return{rolling252:base,all_history:{peak_date:b[i].date,depth_pct:100*(1-last/p)}}}
 default:throw Error('method not implemented');
 }
}
function broad(n,xs){
 const N=xs.length,share=f=>100*xs.filter(f).length/N,depth=xs.map(x=>x.depth);
 switch(n){
 case 20:{if(xs[0].c.length<254)throw Error('254 sessions required for252 prior cross-sectional spreads');const N=xs[0].c.length,spread=i=>q(xs.map(x=>x.r[i]),.9)-q(xs.map(x=>x.r[i]),.1),current=spread(N-1),prior=Array.from({length:252},(_,j)=>spread(N-253+j)),rr=xs.map(x=>x.r.at(-1));return{p10_return_pct:100*q(rr,.1),median_return_pct:100*q(rr,.5),p90_return_pct:100*q(rr,.9),spread_pct_points:100*current,prior252_spread_percentile:100*prior.filter(v=>v<=current).length/252,current_excluded:true,membership_policy:'fixed cutoff members; historical dispersion NOT point-in-time historical index'}}
 case 21:{if(xs[0].c.length<254)throw Error('254 sessions required for252 prior returns');return{below_own_prior252_p05_member_share_pct:share(x=>x.r.at(-1)<q(x.r.slice(-253,-1),.05)),current_excluded:true}}
 case 22:{if(N<2)throw Error('at least2members required');const correlation=(a,b)=>{const ma=avg(a),mb=avg(b),den=Math.sqrt(sum(a.map(v=>(v-ma)**2))*sum(b.map(v=>(v-mb)**2)));return div(sum(a.map((v,i)=>(v-ma)*(b[i]-mb))),den)},means=(start,end)=>{const a=[];for(let i=0;i<N;i++)for(let j=i+1;j<N;j++)a.push(correlation(xs[i].r.slice(start,end),xs[j].r.slice(start,end)));return avg(a)},current=means(-21),prior=means(-42,-21);return{mean_pairwise_correlation21:current,preceding_nonoverlapping21:prior,change:current-prior,pairs:N*(N-1)/2}}
 case 23:{if(xs[0].c.length<274)throw Error('274 sessions required for252 prior rolling volatilities');return{above_own_prior252_p90_volatility_share_pct:share(x=>{const v=i=>stdev(x.r.slice(i-20,i+1).map(Math.log1p)),last=x.c.length-1,prior=Array.from({length:252},(_,j)=>v(last-252+j));return v(last)>q(prior,.9)}),current_excluded:true}}
 case 39:{const eligible=xs.filter(x=>x.peak>x.low);return{previously_underwater_members:eligible.length,recovered_half_share_pct:eligible.length?100*eligible.filter(x=>(x.last-x.low)/(x.peak-x.low)>=.5).length/eligible.length:null,members:eligible.map(x=>({symbol:x.symbol,peak_date:x.b[x.pi].date,recovered_pct:100*(x.last-x.low)/(x.peak-x.low),remaining_peak_gap_pct:x.depth})),no_decline_members:N-eligible.length}}
 case 8:return{median_drawdown_pct:q(depth,.5)};
 case 9:return{median_drawdown_pct:q(depth,.5),p90_drawdown_pct:q(depth,.9),iqr_drawdown_pct:q(depth,.75)-q(depth,.25)};
 case 10:return{disjoint_member_share_pct:{under5:share(x=>x.depth<5),'5_to_under10':share(x=>x.depth>=5&&x.depth<10),'10_to_under20':share(x=>x.depth>=10&&x.depth<20),at_least20:share(x=>x.depth>=20)}};
 case 11:return{below_sma_member_share_pct:Object.fromEntries([20,50,200].map(w=>[w,share(x=>x.last<avg(x.c.slice(-w)))]))};
 case 13:return{new_low_tie_policy:'inclusive current <= rolling minimum; equal/flat lows count, not strict breakouts',new_closing_low_member_share_pct:Object.fromEntries([20,252].map(w=>[w,share(x=>x.last<=Math.min(...x.c.slice(-w)))]))};
 case 14:{const hi=share(x=>x.last>=Math.max(...x.c.slice(-252))),lo=share(x=>x.last<=Math.min(...x.c.slice(-252)));return{new_high_member_share_pct:hi,new_low_member_share_pct:lo,net_high_low_pct_points:hi-lo,flat_range_members:xs.filter(x=>Math.max(...x.c.slice(-252))===Math.min(...x.c.slice(-252))).length}}
 case 15:{const a=xs.filter(x=>x.r.at(-1)>0).length,d=xs.filter(x=>x.r.at(-1)<0).length;return{advancing:a,declining:d,unchanged:N-a-d,net_advance_share_pct_points:100*(a-d)/N}}
 case 17:{const st=xs.map(x=>one(67,x).trailing_down_streak);return{at_least3_down_share_pct:100*st.filter(s=>s>=3).length/N,median_streak:q(st,.5)}}
 case 18:{const values=xs.map(x=>{let up=0,dn=0;x.c.slice(1).forEach((c,i)=>{const d=c-x.c[i];if(i===0){up=Math.max(d,0);dn=Math.max(-d,0)}else{up=up*13/14+Math.max(d,0)/14;dn=dn*13/14+Math.max(-d,0)/14}});return !dn?(up?100:50):100-100/(1+up/dn)});return{median_rsi14:q(values,.5),below30_share_pct:100*values.filter(v=>v<30).length/N,seed:'ewm alpha1/14, first observed change; flat=50'}}
 case 26:{const all=sum(xs.map(x=>x.b.at(-1).volume)),dollars=sum(xs.map(x=>x.b.at(-1).volume*x.last)),losers=xs.filter(x=>x.r.at(-1)<0);return{down_share_volume_pct:100*div(sum(losers.map(x=>x.b.at(-1).volume)),all),down_dollar_volume_pct:100*div(sum(losers.map(x=>x.b.at(-1).volume*x.last)),dollars),members:N,not_net_flow:true}}
 case 27:return{high_volume_down_member_share_pct:share(x=>x.r.at(-1)<0&&x.b.at(-1).volume>1.5*avg(x.b.slice(-21,-1).map(b=>b.volume)))};
 case 28:{const xs2=xs.filter(x=>x.r.at(-1)<0);if(!xs2.length)throw Error('no declining members');return{median_loser_dollar_turnover_expansion:q(xs2.map(x=>div(x.last*x.b.at(-1).volume,avg(x.b.slice(-21,-1).map(b=>b.close*b.volume)))),.5),losing_members:xs2.length}}
 case 29:{const a=xs.map(x=>{const z=x.b.map((b,i)=>i?div(Math.abs(x.r[i]),b.close*b.volume):0);return div(avg(z.slice(-5)),avg(z.slice(-25,-5)))});return{median_illiquidity_expansion:q(a,.5),p90_illiquidity_expansion:q(a,.9),not_observed_spread:true}}
 default:throw Error('method not implemented');
 }
}
export function runPullbackMethod(method,input){
 const out={method_id:method.id,method_version:method.version,status:'missing',percentile_tie_convention:'100 * count(reference <= current) / reference count, current excluded where specified',components:null,reason:null,evidence_level:'E0',coverage:null,provenance:null,algorithm_implemented:SUPPORTED_METHODS.includes(method.number),reason_code:null,input_contract:INPUT_CONTRACTS[method.number]||[],implementation:'chapter-specific offline browser/Node calculator; not evidence promotion'};
 try{
  if(!SUPPORTED_METHODS.includes(method.number))throw Error('definition only: chapter calculator not implemented');
  const {cutoff,calendar,source,feed,price_basis,currency}=input||{};
  const date=s=>typeof s==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&new Date(s+'T00:00Z').toISOString().slice(0,10)===s;
  if(!source||!feed||!price_basis||!currency||!date(cutoff))throw Error('source/feed/price basis/currency/cutoff required');
  if(!Array.isArray(calendar)||calendar.length<252||!calendar.every(date)||new Set(calendar).size!==calendar.length||calendar.some((d,i)=>i&&d<=calendar[i-1])||calendar.at(-1)!==cutoff)throw Error('252+ complete explicit unique increasing sessions ending at cutoff required');
  out.provenance={source,feed,price_basis_declared:price_basis,price_basis_certified:false,currency,as_of:cutoff};
  if(FINAL_METHODS.includes(method.number)){
   if(method.scope==='stock'&&!input.symbol)throw Error('missing input contract: explicit stock symbol required');
   const extra=runFinalMethod(method.number,input,{frame,one,rsiValues,calendar});out.components=extra.components;out.coverage=extra.coverage;out.input_contract=extra.extra_contract;
  }else if(MARKET_METHODS.includes(method.number)){
   const extra=runMarketMethod(method.number,input,{frame,one,rsiValues,calendar,exhaustion});out.components=extra.components;out.coverage=extra.coverage;out.input_contract=extra.extra_contract;
  }else if(EXTRA_METHODS.includes(method.number)){
   if(method.scope==='stock'&&!input.symbol)throw Error('missing input contract: explicit stock symbol required');
   const extra=runExtraMethod(method.number,input,{frame,one,rsiValues,calendar});out.components=extra.components;out.coverage=extra.coverage;out.input_contract=extra.extra_contract;
  }else if(method.scope==='stock'){
   if(!input.symbol)throw Error('explicit stock symbol required');
   const x=frame(input.bars,calendar);
   if([84,85,87].includes(method.number)){
    const key=method.number===85?'sector_proxy':'benchmark',symbolKey=method.number===85?'sector_symbol':'benchmark_symbol';
    if(!input[key]||!input[symbolKey])throw Error('declared aligned '+key+' and symbol required');const z=frame(input[key],calendar),N=x.c.length;
    if(method.number===87){const ratios=x.c.map((v,i)=>v/z.c[i]),peak=Math.max(...ratios.slice(-63));let breach=null;for(let i=N-63;i<N;i++)if(ratios[i]<Math.min(...ratios.slice(i-20,i))&&!breach)breach=calendar[i];out.components={ratio_peak_drawdown63_pct:100*(1-ratios.at(-1)/peak),first_prior20_ratio_low_breach_in63:breach,relative_proxy:input[symbolKey]}}
    else{const a=100*(x.last/x.c[x.pi]-1),d=100*(z.last/z.c[x.pi]-1);out.components={stock_peak_date:calendar[x.pi],proxy_peak_date:calendar[z.pi],stock_drawdown_pct:x.depth,proxy_drawdown_pct:z.depth,stock_episode_return_pct:a,proxy_same_dates_return_pct:d,difference_pct_points:a-d,proxy:input[symbolKey]}}
   }else out.components=one(method.number,x);out.coverage={declared:1,computed:1,missing:0,basis:'declared single stock'};
  }else if([1,2,3,4,5,6,24,25,42].includes(method.number)){
   if(!input.benchmark_symbol||!input.benchmark)throw Error('explicit broad benchmark symbol and bars required, NOT a constituent median');
   out.components=one(method.number,frame(input.benchmark,calendar));out.coverage={declared:1,computed:1,missing:0,basis:'declared benchmark, NOT constituent breadth',benchmark:input.benchmark_symbol};
  }else{
   const members=input.memberships?.[cutoff];
   if(!Array.isArray(members)||!members.length||new Set(members).size!==members.length)throw Error('explicit unique cutoff membership required');
   out.coverage={declared:members.length,computed:0,missing:members.length};
   if(!input.panel||members.some(s=>!Object.hasOwn(input.panel,s)))throw Error('complete declared member panel required; partial breadth blocked');
   const xs=members.map(s=>({...frame(input.panel[s],calendar),symbol:s}));out.components=broad(method.number,xs);out.coverage={declared:members.length,computed:members.length,missing:0,basis:'cutoff members; historical member-local windows, not a point-in-time historical index'};
  }
  const check=v=>{if(typeof v==='number'&&!Number.isFinite(v))throw Error('undefined or nonfinite metric');if(v&&typeof v==='object')Object.values(v).forEach(check)};check(out.components);out.status='available';
 }catch(e){out.reason=e instanceof Error?e.message:String(e);out.reason_code=!out.algorithm_implemented?'unimplemented_algorithm':/insufficient|sessions required|history|historical sample/.test(out.reason)?'insufficient_history_or_sample':/undefined|nonfinite/.test(out.reason)?'undefined_statistic':'missing_or_invalid_input_contract';out.components=null}
 return out;
}
