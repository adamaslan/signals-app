import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {runRecipe,listRecipes} from './engine.mjs';
import {runPullbackMethod,SUPPORTED_METHODS,EXTRA_CONTRACTS} from './pullback-engine.mjs';
const methods=JSON.parse(readFileSync(new URL('./pullback-methods.json',import.meta.url),'utf8'));
import catalog from './catalog.mjs';
export function execute(request){
 if(request.action==='list-methods')return {methods:methods.map(m=>({...m,implemented:SUPPORTED_METHODS.includes(m.number),input_contract:EXTRA_CONTRACTS[m.number]||[]})),implemented_count:SUPPORTED_METHODS.length,evidence_level:'E0'};
 if(request.method_id){const method=methods.find(m=>m.id===request.method_id);return method?runPullbackMethod(method,request.input):{status:'missing',reason:'unknown method ID'}}
 if(request.action==='list')return {engine:'shared offline',recipes:listRecipes(catalog,request.filter??{}),recipe_count:catalog.length,metric_functions:20};
 const recipe=catalog.find(r=>r.id===request.recipe_id);
 if(!recipe)return {status:'missing',reason:'unknown recipe ID'};
 return runRecipe(recipe,request.input);
}
// Explicit JSON stdin protocol for Signals; importable for NWF and parity tests.
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 try{const req=JSON.parse(readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(execute(req))+'\n');}
 catch(e){process.stderr.write('Invalid offline request: '+e.message+'\n');process.exitCode=2;}
}
