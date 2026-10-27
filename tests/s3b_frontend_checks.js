/* UI/controller contract tests replay actual isolated synthetic TestClient records.
 * This is not an authorized live browser demonstration. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),which=process.argv[3];
const nodes=new Map();let htmlWrites=0;const windowEvents={};
class Element {
    constructor(id='',tag='div'){this.id=id;this.tagName=tag;this.children=[];this.listeners={};this.attrs={};this.style={};this.dataset={};this.disabled=false;this.checked=false;this.hidden=false;this._text='';this._value='';this.parent=null;this.classList={add(){},remove(){},toggle(){},contains(){return false;}};}
    set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
    set innerHTML(v){htmlWrites++;this._html=v;this.children=[];}get innerHTML(){return this._html || '';}
    set value(v){this._value=String(v);}get value(){return this._value || (this.tagName==='select'?this.children.find(c=>c.selected)?.value || this.children[0]?.value || '':'');}
    get selectedOptions(){return this.children.filter(c=>c.selected);}get options(){return this.children;}
    append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
    replaceChildren(...items){this.children=[];this._text='';this._value='';this.append(...items);}
    setAttribute(k,v){this.attrs[k]=String(v);}removeAttribute(k){delete this.attrs[k];}
    addEventListener(k,f){(this.listeners[k] ||= []).push(f);}
    querySelectorAll(){return this.children.flatMap(c=>[c,...c.querySelectorAll()]);}
    focus(){document.activeElement=this;}scrollIntoView(){}
}
function get(id){if(id==null)return null;if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);}
const html=fs.readFileSync('frontend/index.html','utf8');
for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){const item=get(match[2]);item.tagName=match[1];item.disabled=/\sdisabled(?:\s|>)/.test(match[0]);}
global.document={getElementById:id=>nodes.get(id)||null,createElement:tag=>new Element('',tag),querySelector:()=>null,querySelectorAll:()=>[],
    addEventListener(){},activeElement:get('loadReviewQueue')};
global.window=global;global.addEventListener=(name,callback)=>{windowEvents[name]=callback;};
let storageAccess=0;global.localStorage={getItem(){storageAccess++;throw Error('Storage is not authority');},setItem(){storageAccess++;throw Error('Storage is not authority');}};
const clone=x=>JSON.parse(JSON.stringify(x));
const response=(data,status=200)=>({ok:status===200,status,headers:{get:()=> 'application/json'},json:async()=>clone(data),text:async()=>JSON.stringify(data)});
global.fetch=async()=>response({detail:'REVIEW_HOST_BOUNDARY_NOT_CONFIGURED'},403);
vm.runInThisContext(fs.readFileSync('frontend/js/upload.js','utf8'));
vm.runInThisContext(fs.readFileSync('frontend/js/review.js','utf8'));
let stage=0,calls=[],invalidations=[],snapshots=[],highlights=[],opened=0,forgotten=0;
let activePacket=clone(fixture.reconstruction.canonical_output.packet);
const originalResult={extraction:fixture.source_packet,reconstruction:fixture.reconstruction,stages:{},diagnostics:[],pipeline:'evidence'};
let failure=null;
async function host(url,options){
    const path=new URL(url).pathname,body=options.body?JSON.parse(options.body):null;calls.push({path,method:options.method,body});
    if(failure){const current=failure;failure=null;return response({detail:current},current==='STALE_REVIEW_TASK'?409:403);}
    if(path==='/api/review/session')return response(fixture.session);
    if(path==='/api/review/queue')return response(fixture.queue);
    if(path.endsWith('/snapshot'))return response(fixture.snapshots[stage]);
    if(path.endsWith('/history'))return response(fixture.histories[stage===3?1:0]);
    if(path.endsWith('/transition')){assert.equal(body.action,'start');stage=1;return response(fixture.started);}
    if(path.endsWith('/corrections')){
        assert.equal(body.expected_etag,fixture.started.etag);assert.equal(body.expected_revision_id,fixture.started.current_event.revision.revision_id);
        assert.equal(body.fields[0].value,'A102');stage=2;return response(fixture.submitted);
    }
    if(path.endsWith('/revalidate')){assert.equal(body.expected_etag,fixture.submitted.etag);stage=3;return response(fixture.revalidated);}
    if(path.endsWith('/end'))return response({revoked:true});
    if(path.startsWith('/api/review/tasks/'))return response(stage<2?fixture.assigned:{...fixture.assigned,task:stage===2?fixture.submitted:fixture.revalidated});
    throw Error('Unexpected synthetic request '+path);
}
global.fetch=async(url,options)=>{
    const path=new URL(url).pathname;
    if(path==='/api/calendar/v1/preview')return response(fixture.previews[stage]);
    return response({detail:'REVIEW_HOST_BOUNDARY_NOT_CONFIGURED'},403);
};
const controller=new STAIRReview.Controller({base:'http://synthetic.invalid',getSource:()=>({packet:activePacket,result:originalResult}),
    openDialog:()=>opened++,closeDialog:()=>opened--,onInvalidate:s=>invalidations.push(s),onSnapshot:s=>snapshots.push(s),onHighlight:h=>highlights.push(h),onForget:()=>forgotten++,hostRequest:host});
controller.setup();controller.updateSource();
async function begin(){await controller.refreshQueue();await controller.selectTask(fixture.id);await controller.transition('start');}
function change(){get('reviewField').value='location';controller.populateField();get('reviewFieldState').value='PRESENT';get('reviewRawValue').value='A102';get('reviewNormalizedValue').value='A102';
    controller.selectedRefs=new Set(fixture.correction_body.fields[0].evidence_ids);get('reviewCorrectionReason').value='Read the printed synthetic amendment';controller.addChange();}
const cases={
    async public_default_deny(){controller.transport=null;await controller.refreshQueue();assert.equal(controller.session,null);assert(get('reviewCorrectionControls').disabled);assert(get('reviewWorkflowStatus').textContent.includes('not configured'));assert.equal(calls.length,0);},
    async public_canonical_details(){controller.inspectPublic();assert.equal(opened,1);assert(get('reviewEventDetails').textContent.includes('Normalized: A101'));assert(get('reviewEventDetails').textContent.includes('Raw: A101'));assert(get('reviewCorrectionControls').disabled);assert.equal(calls.length,0);},
    async scoped_queue_and_task(){await controller.refreshQueue();assert(get('reviewAuthorization').textContent.includes('TEST_ONLY'));assert(get('reviewQueue').textContent.includes('A101'));await controller.selectTask(fixture.id);assert(get('reviewTaskStatus').textContent.includes('ASSIGNED_REVIEW'));assert(get('reviewCorrectionControls').disabled);await controller.transition('start');assert(!get('reviewCorrectionControls').disabled);},
    async supported_correction_revision_and_history(){await begin();change();await controller.submit();assert.equal(controller.task.current_event.revision.version,2);assert.equal(controller.task.current_event.revision.content.location,'A102');assert.equal(controller.task.state,'REVALIDATION_REQUIRED');assert(invalidations.length);assert(get('reviewHistory').textContent.includes('A101 → A102'));assert(get('reviewValidationFeedback').textContent.includes('confirmed revision 2'));assert.equal(controller.task.current_event.human_verification.status,'NOT_VERIFIED');},
    async revalidation_and_new_preview(){await begin();change();await controller.submit();await controller.revalidate();assert.equal(controller.task.state,fixture.revalidated.state);assert.equal(controller.task.current_event.policy.decision,'ABSTAIN');assert(get('reviewDiagnostics').textContent.includes('Calendar writes blocked'));assert(get('reviewHistory').textContent.includes('REVALIDATE'));assert.equal(snapshots.at(-1).packet.events[0].revision.version,2);},
    async disposition_reason_remains_usable_after_revalidation(){
        await begin();change();await controller.submit();await controller.revalidate();
        assert(get('reviewCorrectionControls').disabled);assert(!get('reviewDispositionReason').disabled);
        get('reviewDispositionReason').value='Synthetic task closure preserves abstention and unverified status';
        const prior=controller.transport;controller.transport=async(url,options)=>{
            if(url.endsWith('/transition')){const body=JSON.parse(options.body);assert.equal(body.action,'close');assert.equal(body.expected_etag,fixture.revalidated.etag);assert(body.reason.includes('Synthetic task closure'));return response(fixture.closed);}
            return prior(url,options);
        };
        await controller.transition('close');assert.equal(controller.task.state,'ABSTAINED_CLOSED');
        assert(get('reviewDispositionReason').disabled);assert.equal(controller.task.current_event.human_verification.status,'NOT_VERIFIED');
    },
    async wrong_operator_controls_disabled(){await begin();controller.session={...controller.session,actor_reference:'SYNTHETIC-OTHER'};controller.controls();assert(get('reviewCorrectionControls').disabled);await controller.submit();assert(!calls.some(c=>c.path.endsWith('/corrections')));},
    async missing_evidence_and_field_states(){await begin();get('reviewField').value='location';controller.populateField();controller.selectedRefs.clear();assert.throws(()=>controller.addChange(),/Select 1/);controller.selectedRefs=new Set(fixture.correction_body.fields[0].evidence_ids);get('reviewFieldState').value='SOURCE_ABSENT';get('reviewJustification').value='';assert.throws(()=>controller.addChange(),/justification/);get('reviewJustification').value='Synthetic source absence assertion for negative UI contract';controller.addChange();assert.equal(controller.pending.get('location').correction.value,null);assert.equal(controller.pending.get('location').correction.state,'SOURCE_ABSENT');},
    async conflicting_and_unresolved_states(){await begin();get('reviewField').value='location';controller.populateField();get('reviewFieldState').value='CONFLICTING';get('reviewAlternatives').value='[]';assert.throws(()=>controller.addChange(),/two distinct/);get('reviewFieldState').value='UNRESOLVED';get('reviewRawValue').value='TBA';controller.addChange();assert.equal(controller.pending.get('location').correction.value,null);assert.equal(controller.pending.get('location').correction.raw_value,'TBA');},
    async human_assertion_requires_explicit_original_evidence(){await begin();change();get('reviewHumanAssertion').checked=true;get('reviewJustification').value='';assert.throws(()=>controller.addChange(),/requires raw text/);get('reviewJustification').value='Reading the printed synthetic source amendment';controller.addChange();const item=controller.pending.get('location');assert(item.human.evidence_id.startsWith('human:'));assert.equal(item.human.bbox,null);assert.equal(item.human.page,1);assert.equal(item.human.source_evidence_ids[0],fixture.correction_body.fields[0].evidence_ids[0]);},
    async expiry_clears_private_form_and_history(){await begin();change();get('reviewHistory').append(document.createElement('p'));failure='UNAUTHORIZED_REVIEW';await controller.submit();assert.equal(controller.session,null);assert.equal(controller.pending.size,0);assert.equal(get('reviewEvidenceRefs').children.length,0);assert.equal(get('reviewRawValue').value,'');assert.equal(get('reviewHistory').children.length,0);assert(get('reviewCorrectionControls').disabled);},
    async stale_etag_does_not_claim_success(){await begin();change();failure='STALE_REVIEW_TASK';await controller.submit();assert.equal(controller.task.current_event.revision.version,1);assert(get('reviewValidationFeedback').textContent.includes('task changed'));assert.equal(controller.pending.size,0);},
    async duplicate_click_does_not_duplicate_submission(){await begin();change();await Promise.all([controller.submit(),controller.submit()]);assert.equal(calls.filter(c=>c.path.endsWith('/corrections')).length,1);},
    async revocation_and_no_storage_authority(){await begin();await controller.endSession();assert.equal(controller.transport,null);assert.equal(controller.session,null);assert.equal(forgotten,1);assert.equal(controller.expiryTimer,null);assert(get('reviewWorkflowStatus').textContent.includes('confirmed review grant revocation'));assert.equal(storageAccess,0);},
    async expiry_timer_clears_private_projection(){
        const realTimer=global.setTimeout;let expiry;
        global.setTimeout=(callback,delay)=>{if(delay===30000)return realTimer(callback,delay);expiry=callback;return {unref(){}};};
        try {await controller.refreshQueue();}finally{global.setTimeout=realTimer;}
        assert.equal(typeof expiry,'function');expiry();
        assert.equal(controller.session,null);assert.equal(forgotten,1);assert.equal(controller.expiryTimer,null);
        assert(get('reviewWorkflowStatus').textContent.includes('expired'));
    },
    async late_reply_after_forget_cannot_restore_private_state(){let release;const pending=new Promise(resolve=>release=resolve);controller.transport=()=>pending;const running=controller.refreshQueue();controller.forget();release(response(fixture.session));await running;assert.equal(controller.session,null);assert.equal(get('reviewQueue').children.length,0);},
    async safe_text_rendering_and_source_links(){const unsafe=clone(activePacket);unsafe.events[0].revision.content.course_name='<img src=x onerror=alert(1)>';activePacket=unsafe;controller.updateSource();const before=htmlWrites;controller.inspectPublic();assert(get('reviewEventDetails').textContent.includes('<img src=x onerror=alert(1)>'));assert.equal(htmlWrites,before);const link=get('reviewEventDetails').querySelectorAll().find(n=>n.tagName==='button');await link.listeners.click[0]();assert.equal(highlights[0].source.content_sha256,activePacket.source.content_sha256);assert.equal(highlights[0].evidence.page,1);},
    async cross_page_selection_rejected(){await begin();get('reviewField').value='location';controller.populateField();controller.evidence.push({...controller.evidence[0],evidence_id:'SYNTHETIC-PAGE-2',page:2});controller.selectedRefs=new Set([controller.evidence[0].evidence_id,'SYNTHETIC-PAGE-2']);assert.throws(()=>controller.addChange(),/different or unknown page/);},
    async raw_ingestion_body_preserves_original_envelope(){
        const fetcher=async(url,options)=>{const path=new URL(url).pathname;if(path==='/api/extract')return response({success:true,pipeline:'evidence',data:fixture.source_packet});if(path==='/api/reconstruct')return {...response(fixture.reconstruction),text:async()=>fixture.reconstruction_json};if(path==='/api/policy/assess')return response(fixture.public_policy);if(path==='/api/calendar/v1/eligibility')return response(fixture.eligibility);if(path==='/api/calendar/v1/preview')return response(fixture.previews[0]);throw Error('Unexpected upload route');};
        const upload=new STAIRUpload.Controller({base:'http://synthetic.invalid',fetcher});const file=new File([Buffer.from(fixture.pdf_base64,'base64')],'S3B-SYNTHETIC.pdf',{type:'application/pdf'});
        const result=await upload.run(file,'evidence',{layout:false});assert(result.reconstruction,result.diagnostics.join('; '));
        const body=STAIRUpload.reviewIngestBody(result);assert(body.includes('"reconstruction":'+fixture.reconstruction_json));upload.reset();
        console.log(JSON.stringify({ingestion_body:body}));
    },
    async application_invalidation_and_source_isolation(){
        global.STAIRAnalysis={Controller:class {constructor(options){Object.assign(this,options);this.invalidations=0;}invalidate(){this.invalidations++;}}};
        vm.runInThisContext(fs.readFileSync('frontend/js/app.js','utf8'));vm.runInThisContext('renderEverything=()=>{}; showToast=()=>{};');
        const state=vm.runInThisContext('state');state.upload={...originalResult};state.events=STAIRUpload.displayedEvents(state.upload);
        const original=JSON.stringify(state.upload.reconstruction);vm.runInThisContext('invalidateReviewDiagnostics(state.upload.extraction.source)');
        assert(state.reviewStale);assert.equal(vm.runInThisContext('analysisController.getSource().canonical'),null);
        vm.runInThisContext('renderCalendar()');assert(get('calendarRange').textContent.includes('invalidated'));
        global.testReviewSnapshot=fixture.snapshots[3];vm.runInThisContext('applyReviewSnapshot(global.testReviewSnapshot)');
        assert.equal(state.events[0].venue,'A102');assert.equal(JSON.stringify(state.upload.reconstruction),original);
        assert.equal(vm.runInThisContext('analysisController.getSource().canonical.events[0].revision.version'),2);
        vm.runInThisContext('clearReviewSessionProjection()');
        assert.equal(state.reviewSnapshot,null);assert.equal(state.events[0].venue,'A101');
        assert.equal(JSON.stringify(state.upload.reconstruction),original);
        state.source='authorized_review';state.upload=null;state.canonical=fixture.snapshots[3].packet;
        state.reviewSnapshot=fixture.snapshots[3];state.events=[{venue:'A102'}];
        vm.runInThisContext('clearReviewSessionProjection()');
        assert.equal(state.canonical,null);assert.equal(state.events.length,0);assert.equal(state.source,null);
        state.upload={...originalResult};
        const other=clone(fixture.snapshots[3]);other.packet.source.source_document_id='DIFFERENT-CAPTURE';global.testReviewSnapshot=other;
        assert.equal(vm.runInThisContext('applyReviewSnapshot(global.testReviewSnapshot)'),false);
    },
    async different_capture_and_image_page_not_highlighted(){
        vm.runInThisContext(fs.readFileSync('frontend/js/app.js','utf8'));vm.runInThisContext('showToast=()=>{};');
        const state=vm.runInThisContext('state');state.upload=clone(originalResult);let highlighted=[];global.STAIRUpload={...STAIRUpload,highlightEvidence:id=>highlighted.push(id)};
        global.highlightInput={source:{...fixture.source_packet.source,source_document_id:'OTHER'},evidence:{page:1},id:'source-word'};
        vm.runInThisContext('highlightReviewEvidence(global.highlightInput)');assert.equal(highlighted.length,0);
        state.upload.extraction.source.media_type='image/png';global.highlightInput={source:state.upload.extraction.source,evidence:{page:2},id:'source-word'};
        vm.runInThisContext('highlightReviewEvidence(global.highlightInput)');assert.equal(highlighted.length,0);
    }
};
(async()=>{await cases[which]();console.log(JSON.stringify({case:which,status:'passed'}));})().catch(error=>{console.error(error.stack);process.exitCode=1;});
