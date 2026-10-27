/* Synthetic DOM/controller checks. No credentials or external requests. */
const fs=require('fs'), vm=require('vm'), assert=require('assert');
class Element {
    constructor(){this.children=[];this.textContent='';this.value='';}
    append(n){this.children.push(n);}
    replaceChildren(){this.children=[];this.textContent='';}
}
const ids={};
for(const id of ['analysisResult','analysisStart','analysisEnd','analysisZone','analysisScope','analysisCohort'])ids[id]=new Element();
Object.assign(ids.analysisStart,{value:'2026-01-05'});Object.assign(ids.analysisEnd,{value:'2026-01-11'});
ids.analysisZone.value='Etc/UTC';ids.analysisScope.value='synthetic-table';
global.document={createElement:()=>new Element(),getElementById:id=>ids[id]};
vm.runInThisContext(fs.readFileSync('frontend/js/analysis.js','utf8'));
const text=e=>e.textContent+' '+e.children.map(text).join(' ');
const output={version:'m7-development-v1',completeness:{label:'OBSERVED EVENTS ONLY',reason_codes:['TRUSTED_COMPLETENESS_RECORD_REQUIRED']},
    facts:[{type:'observed_candidate_count',status:'COMPUTED',value:7,fact_id:'synthetic-fact',event_refs:[]}],
    recommendations:[],recommendation_status:'BLOCKED_INCOMPLETE_SCHEDULE',registered_p1a_status:'NOT_EXECUTED',registered_blockers:['PRE01_NOT_READY']};
let calls=[];
global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>output};};
let source={canonical:{source:{source_document_id:'synthetic-source'},events:[]}};
const c=new STAIRAnalysis.Controller({base:'http://synthetic',getSource:()=>source});
(async()=>{
    await c.run();assert.equal(calls.length,1);assert.equal(calls[0].options.credentials,'omit');
    const body=JSON.parse(calls[0].options.body);assert.equal(body.profile,'m7-development-v1');assert.equal(body.declared_kind,'UNVERIFIED_CANDIDATES');
    assert(!body.attestation_id);assert(!body.availability);assert(!body.token);
    assert(text(ids.analysisResult).includes('COMPUTED — 7'));assert(text(ids.analysisResult).includes('OBSERVED EVENTS ONLY'));
    assert(text(ids.analysisResult).includes('NOT_EXECUTED'));assert(!text(ids.analysisResult).includes('Free time available'));
    const upload={reconstruction:{canonical_output:{packet:{events:[{candidate_id:'selected'}]},unresolved_candidates:[{candidate_id:'unselected'}]}}};
    assert.equal(STAIRAnalysis.packetFrom(upload,null).events.length,2);
    const key={};c.invalidate(key);assert(text(ids.analysisResult).includes('NOT ASSESSED'));
    source={sample:true,canonical:source.canonical};await c.run();assert.equal(calls.length,1);
    source={canonical:null};await c.run();assert.equal(calls.length,1);
    source={canonical:{source:{source_document_id:'synthetic'}}};ids.analysisStart.value='';await c.run();assert.equal(calls.length,1);
    assert(text(ids.analysisResult).includes('INSUFFICIENT EVIDENCE'));
    ids.analysisStart.value='2026-01-05';global.fetch=async()=>({ok:false,status:422});await c.run();assert(text(ids.analysisResult).includes('HTTP 422'));
    const html=fs.readFileSync('frontend/index.html','utf8');assert(html.includes('id="view-analysis"'));assert(html.includes('./js/analysis.js'));
    const app=fs.readFileSync('frontend/js/app.js','utf8');assert(app.includes('analysisController.run()'));assert(app.includes('STAIRUpload.renderCards'));
    console.log('M7 synthetic frontend checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
