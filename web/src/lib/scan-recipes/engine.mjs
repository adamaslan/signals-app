// One pure offline engine shared by the Signals CLI and NWF route.
// No provider fetch, model, database, account or trade effects.
export const ENGINE_VERSION='se-recipes-0.1.0';
const avg=a=>a.reduce((s,x)=>s+x,0)/a.length;
const sd=a=>{const m=avg(a);return Math.sqrt(a.reduce((s,x)=>s+(x-m)**2,0)/(a.length-1))};
const median=a=>{const z=[...a].sort((a,b)=>a-b);return z.length%2?z[(z.length-1)/2]:(z[z.length/2-1]+z[z.length/2])/2};
const sum=a=>a.reduce((s,x)=>s+x,0);
const div=(a,b)=>{if(!(b>0))throw Error('undefined nonpositive denominator');return a/b};
const realDate=s=>typeof s==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
export function validateFrame(frame,calendar){
 if(!Array.isArray(frame)||frame.length!==calendar.length)throw Error('frame/session calendar length mismatch');
 const sorted=[...frame].sort((a,b)=>String(a.date).localeCompare(String(b.date)));
 for(let i=0;i<sorted.length;i++){
  const b=sorted[i];if(b.date!==calendar[i])throw Error('missing, duplicate, invalid or misaligned session');
  for(const k of ['open','high','low','close','volume'])if(typeof b[k]!=='number'||!Number.isFinite(b[k]))throw Error('nonfinite or missing '+k);
  if(Math.min(b.open,b.high,b.low,b.close)<=0||b.volume<0||b.high<Math.max(b.open,b.low,b.close)||b.low>Math.min(b.open,b.high,b.close))throw Error('invalid OHLCV');
 }
 return sorted;
}
function rawMetric(metric,bars,i,h){
 if(i<h)throw Error('insufficient warmup');
 const a=bars.slice(i-h+1,i+1),c=a.map(b=>b.close),last=c.at(-1),prev=bars[i-h].close;
 const r=a.map((b,j)=>b.close/bars[i-h+j].close-1),lr=r.map(r=>Math.log1p(r));
 const tr=a.map((b,j)=>Math.max(b.high-b.low,Math.abs(b.high-bars[i-h+j].close),Math.abs(b.low-bars[i-h+j].close)));
 const peak=Math.max(...c),low=Math.min(...c);
 switch(metric){
 case 'return':return 100*(last/prev-1);
 case 'drawdown':return 100*(1-last/peak);
 case 'rebound':return 100*(last/low-1);
 case 'recovery_required':return 100*(peak/last-1);
 case 'underwater_age':return c.length-1-c.lastIndexOf(peak);
 case 'mean_underwater':{let p=prev;return avg(c.map(v=>{p=Math.max(p,v);return 100*(1-v/p)}));}
 case 'volatility':return sd(lr)*Math.sqrt(252)*100;
 case 'downside_deviation':return Math.sqrt(avg(r.map(v=>Math.min(v,0)**2)))*Math.sqrt(252)*100;
 case 'upside_deviation':return Math.sqrt(avg(r.map(v=>Math.max(v,0)**2)))*Math.sqrt(252)*100;
 case 'atr_pct':return avg(tr)/last*100;
 case 'down_share':return 100*r.filter(v=>v<0).length/h;
 case 'worst_return':return 100*Math.min(...r);
 case 'ma_distance':return 100*(last/avg(c)-1);
 case 'efficiency':return div(Math.abs(last-prev),sum(a.map((b,j)=>Math.abs(b.close-bars[i-h+j].close))));
 case 'gap_return':return 100*Math.expm1(sum(a.map((b,j)=>Math.log(b.open/bars[i-h+j].close))));
 case 'intraday_return':return 100*Math.expm1(sum(a.map(b=>Math.log(b.close/b.open))));
 case 'relative_volume':return div(bars[i].volume,avg(bars.slice(i-h,i).map(b=>b.volume)));
 case 'down_volume_share':return 100*div(sum(a.filter((b,j)=>r[j]<0).map(b=>b.volume)),sum(a.map(b=>b.volume)));
 case 'dollar_turnover':return avg(a.map(b=>b.close*b.volume));
 case 'illiquidity':return avg(a.map((b,j)=>div(Math.abs(r[j]),b.close*b.volume)));
 default:throw Error('unrecognized metric');
 }
}
export function runRecipe(recipe,input){
 const out={recipe_id:recipe.id,recipe_version:recipe.version,engine_version:ENGINE_VERSION,status:'missing',components:null,reason:null,evidence_level:'E0',coverage:null,provenance:null};
 try{
  if(!input||!['stock','market'].includes(recipe.scope))throw Error('input and known recipe scope required');
  const {cutoff,calendar,source,feed,price_basis,currency}=input;
  if(!source||!feed||!price_basis||!currency||!realDate(cutoff))throw Error('valid cutoff, source/feed/price basis and currency required');
  if(!Array.isArray(calendar)||calendar.length<2||!calendar.every(realDate)||new Set(calendar).size!==calendar.length||calendar.some((d,i)=>i>0&&d<=calendar[i-1])||calendar.at(-1)!==cutoff)throw Error('explicit unique increasing calendar ending at cutoff required; no stale/future sessions');
  const h=recipe.horizon,n=calendar.length,required=recipe.view==='rank'?h+253:recipe.view==='change'?h+6:recipe.view==='persistence'?h+5:h+1;
  if(!Number.isInteger(h)||h<2||n<required)throw Error('insufficient sessions: '+required+' required');
  out.provenance={as_of:cutoff,source,feed,price_basis_declared:price_basis,currency,price_basis_certified:false,calendar_start:calendar[0],sessions:n};
  const panel={};
  if(recipe.scope==='stock'){
   if(!input.symbol)throw Error('explicit stock symbol required');panel[input.symbol]=validateFrame(input.bars,calendar);
  }else{
   if(!input.panel||!input.memberships)throw Error('constituent panel and point-in-time memberships required; benchmark alone is not breadth');
   for(const [sym,bars] of Object.entries(input.panel))panel[sym]=validateFrame(bars,calendar);
  }
  const coverage=[];
  function at(i){
   const members=recipe.scope==='stock'?[input.symbol]:input.memberships[calendar[i]];
   if(!Array.isArray(members)||!members.length||new Set(members).size!==members.length)throw Error('unique nonempty dated membership missing for '+calendar[i]);
   if(members.some(s=>!Object.hasOwn(panel,s)))throw Error('missing constituent: complete point-in-time universe required');
   const vals=members.map(s=>rawMetric(recipe.metric,panel[s],i,h));
   if(vals.some(v=>!Number.isFinite(v)))throw Error('undefined or nonfinite metric');
   coverage.push({date:calendar[i],declared:members.length,computed:members.length,missing:0});return recipe.scope==='stock'?vals[0]:median(vals);
  }
  if(recipe.metric==='underwater_age'&&['threshold','persistence'].includes(recipe.view)&&recipe.stress_side==='above'&&recipe.threshold>=h-1)throw Error('unsupported research condition: underwater age threshold is unreachable in the declared peak window; strategy definition requires review');
  const current=at(n-1),stress=v=>recipe.stress_side==='below'?v<recipe.threshold:v>recipe.threshold;
  let components={value:current,unit:recipe.unit,horizon_sessions:h,scope:recipe.scope,aggregation:recipe.scope==='market'?'median of member-local readings':'single series'};
  switch(recipe.view){
   case 'level':break;
   case 'change':{const prior=at(n-6);components={...components,prior_value:prior,change:current-prior,comparison_sessions:5};break;}
   case 'rank':{const ref=[];for(let i=n-253;i<n-1;i++)ref.push(at(i));components={...components,percentile:100*ref.filter(v=>v<=current).length/ref.length,reference_count:ref.length,current_excluded:true,reference_start:calendar[n-253],reference_end:calendar[n-2]};break;}
   case 'threshold':components={...components,threshold:recipe.threshold,stress_side:recipe.stress_side,flag:stress(current),threshold_validated:false};break;
   case 'persistence':{const values=[];for(let i=n-5;i<n;i++)values.push(i===n-1?current:at(i));components={...components,threshold:recipe.threshold,stress_side:recipe.stress_side,stress_readings:values.filter(stress).length,observation_sessions:5,share_pct:100*values.filter(stress).length/5};break;}
   default:throw Error('unknown evaluation view');
  }
  out.coverage=coverage;out.components=components;out.status='available';
 }catch(e){out.reason=e instanceof Error?e.message:String(e);out.components=null;}
 return out;
}
export function listRecipes(catalog,{scope,metric,query=''}={}){return catalog.filter(r=>(!scope||r.scope===scope)&&(!metric||r.metric===metric)&&`${r.id} ${r.title} ${r.question}`.toLowerCase().includes(query.toLowerCase()));}
