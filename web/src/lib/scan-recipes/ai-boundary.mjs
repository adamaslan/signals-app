// Pure preparation boundary. This module imports no provider or network client.
// A prepared request is not authorization to send it.
import {runRecipe} from './engine.mjs';
const KEYS = new Set(['value','unit','horizon_sessions','scope','aggregation','prior_value','change','comparison_sessions','percentile','reference_count','current_excluded','reference_start','reference_end','threshold','stress_side','flag','threshold_validated','stress_readings','observation_sessions','share_pct']);
function finiteRecord(value) {
 if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('computed components required');
 const out={};
 for(const [key,v] of Object.entries(value)){
  if(!KEYS.has(key)) throw Error('unknown component: '+key);
  if(typeof v==='number' && Number.isFinite(v)) out[key]=v;
  else if(typeof v==='boolean') out[key]=v;
  else if(typeof v==='string' && ['scope','aggregation','unit','stress_side','reference_start','reference_end'].includes(key)) out[key]=v;
  else throw Error('unsupported component type');
 }
 return out;
}
function allPricesZero(pricing) {
 if(!pricing || typeof pricing!=='object' || Array.isArray(pricing) || !Object.hasOwn(pricing,'prompt') || !Object.hasOwn(pricing,'completion')) return false;
 return Object.values(pricing).every(v => (typeof v==='number' || (typeof v==='string' && v.trim().length>0)) && Number.isFinite(Number(v)) && Number(v)===0);
}
export function resolveRecipeSelection(catalog, selection) {
 if(!selection || typeof selection!=='object' || Array.isArray(selection) || Object.keys(selection).some(k=>k!=='recipe_id')) throw Error('selection must contain only recipe_id');
 const recipe=catalog.find(r=>r.id===selection.recipe_id);
 if(!recipe) throw Error('unknown recipe_id');
 return recipe;
}
// Recompute locally rather than trusting a model-provided result, metric or threshold.
export function computeRecipeEvidence(catalog, selection, input) {
 const recipe=resolveRecipeSelection(catalog,selection);
 const result=runRecipe(recipe,input);
 return {recipe,result};
}
export function prepareFreeExplanation(catalog, selection, input, policy={}) {
 const {recipe,result}=computeRecipeEvidence(catalog,selection,input);
 const base={status:'non_ai',reason:null,result,request:null};
 if(result.status!=='available')return {...base,reason:'missing_input'};
 if(policy.remaining_calls!==1)return {...base,reason:'one_call_budget_required'};
 if(!Number.isInteger(policy.max_tokens)||policy.max_tokens<1||policy.max_tokens>256)return {...base,reason:'token_limit_required'};
 // The caller must supply a freshly verified catalog row, not just a :free suffix.
 const row=policy.model;
 if(!row||typeof row.id!=='string'||row.id.length>200||!/^[-a-zA-Z0-9_./:]+$/.test(row.id)||!allPricesZero(row.pricing)||!Number.isFinite(Date.parse(policy.catalog_checked_at||''))||!Number.isFinite(Date.parse(policy.now||''))||Date.parse(policy.now)-Date.parse(policy.catalog_checked_at)<0||Date.parse(policy.now)-Date.parse(policy.catalog_checked_at)>3600000)return {...base,reason:'verified_zero_price_model_required'};
 const components=finiteRecord(result.components);
 // Only known catalog fields and computed values enter the evidence. Raw prices,
 // user text, arbitrary provenance strings, account data and symbols stay local.
 const packet={recipe_id:recipe.id,metric:recipe.metric,scope:recipe.scope,view:recipe.view,horizon_sessions:recipe.horizon,components,as_of:result.provenance.as_of,evidence_level:'E0',price_basis_certified:false,live_market_verified:false};
 return {...base,status:'prepared_not_sent',reason:'caller_authority_and_server_transport_still_required',request:{model:row.id,max_tokens:policy.max_tokens,stream:false,provider:{max_price:{prompt:0,completion:0},allow_fallbacks:false},messages:[{role:'system',content:'Explain only the supplied computed scan evidence in plain words. It is E0 research, not a validated prediction or recommendation. Do not compute new metrics, promote evidence, invent causes, change the recipe or claim current live data. No tools or follow-up calls.'},{role:'user',content:JSON.stringify(packet)}]},catalog_checked_at:policy.catalog_checked_at};
}
