/* Invented records test rendering/contracts, never acceptance evidence. */
const assert=require('node:assert/strict');require('../frontend/js/upload.js');
const U=STAIRUpload,hash='a'.repeat(64),id='11111111-1111-4111-8111-111111111111';
const event={candidate_id:'synthetic-candidate',identity:{event_id:id},revision:{revision_id:id,content:{},observations:[]}};
const d={decision_id:id,candidate_id:event.candidate_id,event_id:id,revision_id:id,revision_digest:hash,
    decision:'ACCEPT',policy_profile:'m5-registered-v1',registration_mode:'TEST_ONLY',registration_id:hash,
    registration_signature:hash,registration_source_digest:hash,registration_evidence_digest:hash,
    registered_routing_eligible:true,calibrated_probability:null,reason_codes:['REGISTERED_ACCEPT']};
assert.equal(U.validatePolicy({profile:'m5-registered-v1',registered_routing_ready:true,decisions:[d]},[event])[0],d);
assert.equal(U.boundRouting(event,d).route,'ACCEPT');
assert.equal(U.boundRouting(event,{...d,revision_id:'stale'}),null);
assert.equal(U.boundRouting(event,{...d,registration_signature:null}),null);
assert.throws(()=>U.validatePolicy({profile:'m5-development-v1',registered_routing_ready:false,decisions:[d]},[event]),/promotion/);
assert.throws(()=>U.validatePolicy({profile:'m5-registered-v1',registered_routing_ready:true,decisions:[{...d,registration_id:null}]},[event]),/receipt/);
const view={canonical:event};const summary=U.routingSummary([view],{policy:[d]});
assert.equal(summary.accept,1);assert.equal(summary.testOnly,1);assert.equal(summary.automaticCoverage,1);assert.equal(summary.accuracy,null);
const bundle=U.calendarBundle({events:[event]},'COMPLETE_FINITE_SEARCH',[d]);
assert.equal(bundle.registered_assessment_refs.length,1);assert.equal(bundle.registered_assessment_refs[0].registration_signature,hash);
assert.deepEqual(bundle.assessments,[]);assert.equal(bundle.routing_diagnostics,undefined);
assert.equal(U.calendarBundle({events:[event]},'COMPLETE_FINITE_SEARCH',[{...d,revision_id:'old'}]).registered_assessment_refs,undefined);
console.log('PASS signed-reference forwarding, current-revision routing, TEST_ONLY labels and no fake accuracy');
