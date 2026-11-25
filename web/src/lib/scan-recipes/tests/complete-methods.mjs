import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {runPullbackMethod,SUPPORTED_METHODS} from '../pullback-engine.mjs';
const methods=JSON.parse(readFileSync(new URL('../pullback-methods.json',import.meta.url)));
import {makeCompleteFixture} from './fixtures.mjs';
const input=makeCompleteFixture();const {calendar,cutoff}=input;
const results=[],started=performance.now();
for(const method of methods){const r=runPullbackMethod(method,input);results.push(r);assert.equal(r.status,'available',`${method.number} ${method.title}: ${r.reason}`);assert.equal(r.algorithm_implemented,true);assert.ok(r.components!==null);assert.equal(r.evidence_level,'E0');JSON.stringify(r)}
assert.equal(SUPPORTED_METHODS.length,100);assert.equal(new Set(SUPPORTED_METHODS).size,100);
assert.equal(results[89].components.events[0].assigned_session,calendar.at(-3),'after-close earnings maps next explicit session');
assert.equal(results[90].components.block_severity_rankings,false,'clean explicitly complete invented action log');
assert.ok(results[42].components.sample>=20,'historical depth has20 completed episodes');
for(const z of results[46].components.nearest)assert.ok(z.fit_end<z.date&&z.date<cutoff,'analogue fit strictly before candidate');
assert.ok(results[49].components.readings.every(z=>z.current_excluded));
const report={synthetic:true,source:input.source,chapter_definitions:100,implemented_algorithms:100,available_results:results.filter(z=>z.status==='available').length,extra_contracts_explicit:true,evidence_level:'E0',independent_predictive_edges:0,production_integration_verified:false,wall_seconds:(performance.now()-started)/1000};
if(process.env.SE_SAVE_FIXTURES)writeFileSync(new URL('./complete-fixture.json',import.meta.url),JSON.stringify(input));
if(process.env.SE_SAVE_FIXTURES)writeFileSync(new URL('./complete-method-evidence.json',import.meta.url),JSON.stringify(report,null,2));
if(process.env.SE_SAVE_FIXTURES)writeFileSync(new URL('./complete-method-results.json',import.meta.url),JSON.stringify(results,null,2));console.log(JSON.stringify(report,null,2));
