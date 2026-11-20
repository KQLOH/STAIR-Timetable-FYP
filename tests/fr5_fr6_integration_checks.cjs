/* Synthetic routing/replay contracts only; no credentials or human records. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class E{constructor(){this.children=[];this._text='';}set textContent(v){this._text=String(v);this.children=[]}get textContent(){return this._text+this.children.map(c=>c.textContent).join(' ')}append(...v){this.children.push(...v)}replaceChildren(){this.children=[];this._text=''}}
const out=new E();global.document={getElementById:()=>out,createElement:()=>new E()};
vm.runInThisContext(fs.readFileSync('frontend/js/upload.js','utf8'));vm.runInThisContext(fs.readFileSync('frontend/js/sandbox.js','utf8'));
const event={candidate_id:'SYNTHETIC-EVENT',identity:{event_id:'SYNTHETIC-IDENTITY'},revision:{revision_id:'SYNTHETIC-REVISION'}};
const d={candidate_id:event.candidate_id,event_id:event.identity.event_id,revision_id:event.revision.revision_id,revision_digest:'a'.repeat(64),decision:'REVIEW',policy_profile:'m5-source-review-v1',registered_routing_eligible:false};
const test=process.argv[2];
if(test==='routing_projection'){const bundle=STAIRUpload.calendarBundle({events:[event]},'NOT_PROVIDED',[d]);assert.equal(bundle.routing_diagnostics[0].route,'REVIEW');assert.deepEqual(bundle.assessments,[]);assert.equal(bundle.reconstruction_status,'NOT_PROVIDED');assert.equal(bundle.routing_diagnostics[0].registered_accept_eligible,false);}
if(test==='stale_projection'){const bundle=STAIRUpload.calendarBundle({events:[event]},'NOT_PROVIDED',[{...d,revision_id:'OLD'}]);assert(!bundle.routing_diagnostics);}
if(test==='accept_projection_refused'){for(const x of [{...d,decision:'ACCEPT'},{...d,registered_routing_eligible:true}])assert(!STAIRUpload.calendarBundle({events:[event]},'NOT_PROVIDED',[x]).routing_diagnostics);}
if(test==='current_population'){
 const partial={...event,candidate_id:'SYNTHETIC-UNRESOLVED',identity:{event_id:'SYNTHETIC-OTHER-ID'},revision:{revision_id:'SYNTHETIC-OTHER-REV'}};
 const packet={events:[event]},decision={...d,candidate_id:partial.candidate_id,event_id:partial.identity.event_id,revision_id:partial.revision.revision_id,decision:'ABSTAIN'};
 const result={reconstruction:{canonical_output:{packet,unresolved_candidates:[partial],status:'SEARCH_INCOMPLETE'}},policy:[d,decision]};
 const bundle=STAIRUpload.calendarBundleFromResult(result);assert.deepEqual(bundle.packet.events,[event,partial]);assert.deepEqual(packet.events,[event]);assert.equal(bundle.routing_diagnostics.length,2);assert.equal(bundle.routing_diagnostics[1].route,'ABSTAIN');assert.equal(bundle.reconstruction_status,'SEARCH_INCOMPLETE');
 const fallback=STAIRUpload.calendarBundleFromResult({extraction:packet,stages:{reconstruction:'EXECUTION_ABORTED'}});assert.equal(fallback.packet,packet);assert.equal(fallback.reconstruction_status,'EXECUTION_ABORTED');
}
if(test.startsWith('replay_')){const status={replay_confirmed:'CONFIRMED',replay_unknown:'OUTCOME_UNKNOWN',replay_failed:'FAILED_CONFIRMED',replay_preview_required:'PREVIEW_REQUIRED'}[test];const c=new STAIRSandbox.Controller({base:''});c.results({outcomes:[{operation_key:'SYNTHETIC-OP',status,duplicate_submission:true}]});if(status==='CONFIRMED'){assert(out.textContent.includes('NO_OP (replay)'));assert(out.textContent.includes('previously confirmed'));}else{assert(!out.textContent.includes('previously confirmed'));assert(!out.textContent.includes('Confirmed in local calendar'));assert(out.textContent.includes('Recorded attempt (not resent)'));if(status==='PREVIEW_REQUIRED')assert(out.textContent.includes('Fresh preview and approval required'));}}
console.log('PASS FR5/FR6 frontend: '+test);
