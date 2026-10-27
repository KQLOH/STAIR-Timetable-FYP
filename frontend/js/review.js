/* Application repair client. Authentication comes only from a host transport;
 * this module cannot issue grants and never stores credentials in web storage. */
(function (global) {
    'use strict';
    const STATES = ['PRESENT','MISSING','SOURCE_ABSENT','UNRESOLVED','CONFLICTING','NOT_APPLICABLE'];
    const HELP = {
        REVIEW_HOST_BOUNDARY_NOT_CONFIGURED: 'The trusted review host is not configured. Public inspection remains read-only.',
        UNAUTHORIZED_REVIEW: 'Review authorization expired, was revoked, or is unavailable. Ask the approved host to reconnect.',
        REVIEW_PERMISSION_DENIED: 'This operator does not have permission for this action.',
        REVIEW_SOURCE_SCOPE_DENIED: 'This source capture is outside the operator grant.',
        REVIEW_ORIGIN_DENIED: 'This website origin is not approved by the review host.',
        TASK_ACCESS_DENIED: 'This task is not assigned to this operator.',
        STALE_REVIEW_TASK: 'The task changed. Refresh it and inspect the new ETag before preparing another submission.',
        STALE_EVENT_REVISION: 'The event revision changed. Refresh the task; do not resubmit the old proposal.',
        CORRECTION_REPLAY: 'This request was already recorded. Inspect task history before another submission.',
        BROKEN_CORRECTION_EVIDENCE: 'A selected evidence reference is unavailable in this source.',
        UNSUPPORTED_CORRECTION_VALUE: 'The raw reading is not supported by the selected evidence. If OCR is wrong, explicitly record a reading from the original source.',
        CORRECTION_NORMALIZATION_MISMATCH: 'The normalized value does not match the source reading under the existing correction rules.',
        CORRECTION_SCHEMA_INVALID: 'The field value or state is invalid. Check date, time, alternatives and required justification.',
        CORRECTION_CROSS_PAGE_EVIDENCE: 'One field correction cannot merge evidence from different pages.',
        HUMAN_EVIDENCE_PAGE_MISMATCH: 'The source reading must refer to one original page.',
        INVALID_HUMAN_EVIDENCE: 'A human assertion must link original evidence from this capture.',
        REVALIDATION_REQUIRED: 'Revalidate the current revision before resolving the task.',
        UNRESOLVED_HARD_CONSTRAINT: 'Hard constraints are still unresolved. Do not mark this record resolved.',
        ASSIGNMENT_REQUIRED: 'A current host assignment and an active review state are required.',
        RECONSTRUCTION_ALREADY_QUEUED: 'This reconstruction is already queued. Reload the authorized queue.',
        INVALID_LAYOUT_REVIEW_BINDING: 'Source/layout/reconstruction bindings differ. Re-extract the source rather than relabeling its evidence.',
        STRUCTURED_REVIEW_BINDING_NOT_APPROVED: 'Structured TAR UMT review enrollment has not been approved. Its existing inspection path remains available.'
    };
    const el = id => global.document.getElementById(id);
    const label = name => name.replaceAll('_',' ');
    const text = value => value == null ? 'Not supplied' : typeof value === 'object' ? JSON.stringify(value,null,2) : String(value);
    function node(tag, value, className) {
        const result = global.document.createElement(tag);
        if (value != null) result.textContent = String(value);
        if (className) result.className = className;
        return result;
    }
    function details(title, value) {
        const result=node('details');result.append(node('summary',title),node('pre',text(value)));return result;
    }
    function set(id, value) { if(el(id)) el(id).textContent=value; }
    function sourceKey(source) { return source ? `${source.source_document_id}:${source.content_sha256}` : null; }
    function normalizedValue(field, value) {
        if (['day_of_week','end_day_offset'].includes(field)) {
            if (!/^\d+$/.test(value.trim())) throw Error('Use an explicit integer for this field.');
            return Number(value);
        }
        if (['teaching_weeks','excluded_weeks'].includes(field)) {
            if (!/^\s*\d+(?:\s*,\s*\d+)*\s*$/.test(value)) throw Error('Enter an explicit comma-separated list of week numbers.');
            return value.split(',').map(v=>Number(v.trim()));
        }
        if (field==='recurrence') {
            let result;try {result=JSON.parse(value);}catch {throw Error('Recurrence must use the existing JSON contract.');}
            if (!result || typeof result!=='object' || Array.isArray(result)) throw Error('Recurrence must be a JSON object.');
            return result;
        }
        if (!value.trim()) throw Error('A present field requires a normalized value.');
        return value.trim();
    }
    class ReviewClientError extends Error {
        constructor(status, code, retained=false) {
            super(HELP[code] || (status===403 ? 'Protected review access was refused.' : `Review request was rejected (${status}). Refresh the task and inspect its validation feedback.`));
            this.status=status;this.code=code;this.retained=retained;
        }
    }

    class Controller {
        constructor({base,getSource,openDialog,closeDialog,onInvalidate=()=>{},onSnapshot=()=>{},onHighlight=()=>{},onForget=()=>{},hostRequest=null}) {
            Object.assign(this,{base,getSource,openDialog,closeDialog,onInvalidate,onSnapshot,onHighlight,onForget});
            this.transport=hostRequest;this.session=null;this.task=null;this.capture=null;this.evidence=[];
            this.pending=new Map();this.human=[];this.selectedRefs=new Set();this.generation=0;this.busy=false;this.uncertain=false;this.inflight=new Set();
        }
        setup() {
            const listen=(id,event,callback)=>el(id)?.addEventListener(event,callback);
            listen('loadReviewQueue','click',()=>this.refreshQueue());
            listen('moreReviewTasks','click',()=>this.refreshQueue(this.nextOffset || 0));
            listen('ingestReviewSource','click',()=>this.ingest());
            listen('endReviewSession','click',()=>this.endSession());
            listen('inspectCanonical','click',()=>this.inspectPublic());
            listen('closeReviewDetails','click',()=>this.closeDialog());
            listen('reviewField','change',()=>this.populateField());
            listen('reviewFieldState','change',()=>this.stateInputs());
            listen('reviewEvidenceFilter','input',()=>this.evidenceChoices());
            listen('reviewEvidenceRefs','change',()=>{this.selectedRefs=new Set([...el('reviewEvidenceRefs').selectedOptions].map(o=>o.value));});
            listen('addReviewChange','click',()=>{try{this.addChange();}catch(error){this.feedback(error.message);}});
            listen('reviewCorrectionForm','submit',event=>{event.preventDefault();this.submit();});
            listen('claimReviewTask','click',()=>this.transition('claim'));
            listen('startReviewTask','click',()=>this.transition(this.task?.state==='ASSIGNED_REVIEW'?'start':'resume'));
            listen('reloadReviewTask','click',()=>this.selectTask(this.task?.task_id));
            listen('revalidateReviewTask','click',()=>this.revalidate());
            listen('loadReviewHistory','click',()=>this.history());
            for(const [id,action] of [['resolveReviewTask','resolve'],['escalateReviewTask','escalate'],['closeReviewTask','close']]) listen(id,'click',()=>this.transition(action));
            global.addEventListener?.('pagehide',()=>this.forget());
            this.controls();
        }
        // Future approved host bootstrap may attach an authenticated transport.
        // Its presence is not authority: every real request is checked by M5.
        attachTrustedHostTransport(request) {
            if(typeof request!=='function') throw Error('A host-owned request transport is required.');
            this.forget();this.transport=request;
        }
        forget() {
            const hadPrivateSession=!!this.session;
            clearTimeout(this.expiryTimer);this.expiryTimer=null;
            this.generation++;for(const abort of this.inflight)abort.abort();this.inflight.clear();this.transport=null;this.session=null;this.task=null;
            this.capture=null;this.evidence=[];this.pending.clear();this.human=[];this.uncertain=false;
            this.selectedRefs.clear();
            for(const id of ['reviewQueue','reviewEventDetails','reviewHistory','reviewDiagnostics','reviewPendingChanges','reviewEvidenceRefs']) el(id)?.replaceChildren();
            for(const id of ['reviewRawValue','reviewNormalizedValue','reviewJustification','reviewCorrectionReason','reviewDispositionReason','reviewAlternatives','reviewEvidenceFilter']) if(el(id)) el(id).value='';
            if(el('reviewHumanAssertion'))el('reviewHumanAssertion').checked=false;
            if(hadPrivateSession)this.onForget();
            set('reviewAuthorization','Public read-only mode. No active scoped review authorization.');this.controls();
            set('reviewTaskStatus','Private task details cleared.');
        }
        async request(method,path,body,publicRequest=false) {
            const abort=new AbortController();const timer=setTimeout(()=>abort.abort(),30000);
            const generation=this.generation;this.inflight.add(abort);
            const options={method,credentials:'omit',cache:'no-store',signal:abort.signal,headers:{}};
            if(body!==undefined) {options.headers['Content-Type']='application/json';options.body=typeof body==='string'?body:JSON.stringify(body);}
            try {
                const response=await (!publicRequest && this.transport ? this.transport(this.base+path,options) : global.fetch(this.base+path,options));
                const payload=await response.json();
                if(generation!==this.generation)throw new ReviewClientError(403,'UNAUTHORIZED_REVIEW');
                if(!response.ok) {
                    const detail=payload?.detail, code=typeof detail==='string'?detail:detail?.code;
                    throw new ReviewClientError(response.status,typeof code==='string'&&/^[A-Z0-9_]+$/.test(code)?code:'REVIEW_REQUEST_REJECTED',detail?.proposal_retained===true);
                }
                return payload;
            } finally {clearTimeout(timer);this.inflight.delete(abort);}
        }
        permitted(permission) { return !!this.session?.scoped_workflow_enabled && this.session.permissions.includes(permission); }
        canCorrect() {return this.permitted('correct') && this.task?.assignee===this.session.actor_reference && this.task.state==='IN_PROGRESS' && !!this.task.current_event && !this.uncertain;}
        async action(work) {
            if(this.busy) return;
            this.busy=true;this.feedback('Loading review state — waiting for backend confirmation.');this.controls();
            try{return await work();}
            catch(error) {
                if(error.status===403) this.forget();
                this.feedback((error.operationConfirmed?'The revision operation was confirmed; downstream refresh is incomplete. ':'')+(error instanceof ReviewClientError ? error.message : 'The operation could not be confirmed. Refresh task/history before retrying.'));
            } finally {this.busy=false;this.controls();}
        }
        feedback(message) {set('reviewWorkflowStatus',message);set('reviewValidationFeedback',message);}
        controls() {
            const enabled=!!this.session?.scoped_workflow_enabled, task=!!this.task;
            const disabled=(id,yes)=>{if(el(id))el(id).disabled=yes || this.busy;};
            disabled('loadReviewQueue',false);disabled('endReviewSession',!this.session);
            disabled('inspectCanonical',!this.getSource()?.packet?.events?.length);
            const source=this.getSource()?.packet?.source;
            const inScope=this.session?.source_bindings?.some(([id,hash])=>source?.source_document_id===id && source.content_sha256===hash);
            disabled('ingestReviewSource',!this.permitted('queue') || !inScope || !this.getSource()?.result?.reconstruction || source?.route==='tarumt_html');
            disabled('reviewCorrectionControls',!this.canCorrect());
            disabled('claimReviewTask',!task || !this.permitted('assign') || !['PENDING_REVIEW','ASSIGNED_REVIEW','ESCALATED'].includes(this.task.state));
            disabled('startReviewTask',!task || !this.permitted('correct') || this.task.assignee!==this.session?.actor_reference || !['ASSIGNED_REVIEW','REVALIDATED','REVALIDATION_REQUIRED'].includes(this.task.state));
            disabled('reloadReviewTask',!enabled || !task);disabled('loadReviewHistory',!enabled || !task);
            disabled('revalidateReviewTask',!task || !this.permitted('revalidate') || !['IN_PROGRESS','REVALIDATION_REQUIRED'].includes(this.task.state));
            for(const id of ['resolveReviewTask','escalateReviewTask','closeReviewTask']) disabled(id,!task || !this.permitted('resolve') || ['RESOLVED','ABSTAINED_CLOSED'].includes(this.task.state));
            disabled('reviewDispositionReason',!task || !this.permitted('resolve') || ['RESOLVED','ABSTAINED_CLOSED'].includes(this.task.state));
            disabled('resolveReviewTask',!task || !this.permitted('resolve') || this.task.state!=='REVALIDATED'
                || this.task.decision?.findings.some(f=>f.severity==='HARD' && f.passed!==true));
            if(el('reviewCorrectionForm'))el('reviewCorrectionForm').setAttribute('aria-busy',String(this.busy));
            for(const id of ['reviewQueue','reviewEventDetails'])el(id)?.setAttribute('aria-busy',String(this.busy));
        }
        updateSource() {
            const current=this.getSource(),packet=current?.packet;
            const key=packet ? sourceKey(packet.source)+':'+packet.events.map(e=>e.revision.revision_id).join(':') : null;
            if(this.publicKey!==key) {
                this.publicKey=key;const select=el('reviewPublicCandidate');
                if(select) {select.replaceChildren();for(const event of packet?.events || []) {const option=node('option',`${event.revision.content.course_code || 'Unresolved course'} · ${event.revision.content.date || 'date unresolved'} · revision ${event.revision.version}`);option.value=event.candidate_id;select.append(option);}}
            }
            if(el('inspectCanonical'))el('inspectCanonical').disabled=!packet?.events?.length;
            this.controls();
        }
        inspectPublic() {
            const packet=this.getSource()?.packet;
            const event=packet?.events.find(e=>e.candidate_id===el('reviewPublicCandidate').value);
            if(!event)return;
            this.task=null;this.capture=packet.source;this.evidence=packet.evidence;
            this.pending.clear();this.renderEvent(event,'Public read-only candidate; no protected task selected.');
            this.controls();this.openDialog();
        }
        async refreshQueue(offset=0) {
            await this.action(async()=>{
                this.session=await this.request('GET','/api/review/session');
                if(!this.session.scoped_workflow_enabled || !Array.isArray(this.session.source_bindings) || !Array.isArray(this.session.permissions)
                    || !['APPLICATION_OPERATOR','TEST_ONLY'].includes(this.session.mode)) throw new ReviewClientError(403,'REVIEW_SOURCE_SCOPE_DENIED');
                const remaining=Date.parse(this.session.expires_at)-Date.now();
                if(!Number.isFinite(remaining) || remaining<=0 || remaining>61*60000)throw new ReviewClientError(403,'UNAUTHORIZED_REVIEW');
                clearTimeout(this.expiryTimer);
                this.expiryTimer=setTimeout(()=>{this.forget();this.feedback('Review authorization expired. Private review views have been cleared.');},remaining);
                this.expiryTimer.unref?.(); // Node synthetic tests must not wait for grant expiry.
                set('reviewAuthorization',`${this.session.mode==='TEST_ONLY'?'TEST_ONLY synthetic demonstration':'Authorized application operator'} · ${this.session.actor_reference} · expires ${this.session.expires_at}. Application repair, not independent research verification. Storage is process-local.`);
                const queue=await this.request('GET',`/api/review/queue?offset=${offset}&limit=50`);
                const out=el('reviewQueue');out.replaceChildren();
                if(!queue.tasks.length)out.append(node('p','No tasks are visible within this assignment and source scope.'));
                for(const task of queue.tasks) {
                    const c=task.current_event?.revision.content || {},missing=task.current_event?.revision.observations.filter(o=>o.state!=='PRESENT').map(o=>label(o.field_name)) || [];
                    const button=node('button',`${c.course_code || 'Unresolved course'} · ${c.date || 'date unresolved'} / ${c.day_of_week || 'weekday unresolved'} · ${c.start_time || '?'}–${c.end_time || '?'} · ${c.session_type || 'type unresolved'} · ${c.location || 'venue unresolved'} · section ${c.section || '?'} / cohort ${c.cohort_id || '?'} · ${task.state} · extraction ${task.current_event?.extraction_status || 'unavailable'} · missing/uncertain: ${missing.join(', ') || 'none by field presence'} · source ${task.current_event?.revision.source_document_id || 'no candidate'}`,'button secondary');
                    button.type='button';button.addEventListener('click',()=>this.selectTask(task.task_id));out.append(button);
                }
                this.nextOffset=queue.next_offset;el('moreReviewTasks').hidden=queue.next_offset==null;
                this.feedback(`${queue.total} task(s) visible. Select a task; field completeness is not source verification.`);
            });
        }
        async ingest() {
            if(!this.permitted('queue'))return;
            await this.action(async()=>{
                const result=this.getSource()?.result;
                await this.request('POST','/api/review/ingest',global.STAIRUpload.reviewIngestBody(result));
                this.feedback('Backend confirmed application repair tasks. Host assignment is still required.');
            });
            if(this.session)await this.refreshQueue();
        }
        async selectTask(id) {
            if(!id || !this.session)return;
            await this.action(async()=>{
                const result=await this.request('GET',`/api/review/tasks/${encodeURIComponent(id)}`);
                this.task=result.task;this.capture=result.source;this.evidence=result.evidence;
                this.pending.clear();this.human=[];this.uncertain=false;el('reviewPendingChanges').replaceChildren();
                el('reviewDispositionReason').value='';el('reviewCorrectionReason').value='';
                el('reviewHistory').replaceChildren();el('reviewDiagnostics').replaceChildren();
                this.renderTask();this.openDialog();
                if(this.task.current_event)await this.refreshSnapshot();
            });
        }
        renderTask() {
            const task=this.task;
            if(!task?.current_event) {set('reviewTaskStatus','No correctable candidate is available. Preserve this abstention.');el('reviewEventDetails').replaceChildren();return;}
            this.renderEvent(task.current_event,`${task.state} · revision ${task.current_event.revision.version} · ETag ${task.etag} · assignee ${task.assignee || 'not assigned'} · application repair only.`);
            const fields=el('reviewField');fields.replaceChildren();
            for(const name of this.session.correction_contract.fields) {const option=node('option',label(name));option.value=name;fields.append(option);}
            this.populateField();this.controls();
        }
        renderEvent(event,status) {
            set('reviewTaskStatus',status);const out=el('reviewEventDetails');out.replaceChildren();
            out.append(node('p',`Source ${this.capture.source_document_id}; capture ${this.capture.content_sha256}; route ${this.capture.route || 'retained source'}. Original coordinates are not combined across pages.`));
            out.append(node('p',`Policy ${event.policy?.decision || 'NOT ASSESSED'}; human review ${event.human_review.status}; verification ${event.human_verification.status}; calendar ${event.calendar.approval_status} / ${event.calendar.sync_status}.`));
            for(const [field,value] of Object.entries(event.revision.content)) {
                const observation=event.revision.observations.find(o=>o.field_name===field);
                const box=node('section',null,'review-field-record');box.append(node('strong',label(field)),node('p',`Normalized: ${text(value)}`),node('p',`Raw: ${text(observation?.raw_value)} · ${observation?.state || 'MISSING'} · origin ${observation?.origin || 'unknown'}`));
                if(observation?.alternatives.length)box.append(details('Unresolved alternatives',observation.alternatives));
                if(observation)box.append(details('Field provenance',observation));
                for(const id of observation?.evidence_ids || []) {
                    const span=this.evidence.find(e=>e.evidence_id===id),button=node('button',`Source evidence · page ${span?.page ?? 'unknown'} · ${id}`,'text-button');button.type='button';
                    button.addEventListener('click',()=>this.onHighlight({source:this.capture,evidence:span,id}));box.append(button);
                }
                out.append(box);
            }
            out.append(details('Current constraint findings',this.task?.decision?.findings || event.validation),details('Revision and status contract',event));
        }
        populateField() {
            const event=this.task?.current_event,field=el('reviewField').value;if(!event || !field)return;
            const observation=event.revision.observations.find(o=>o.field_name===field),value=event.revision.content[field];
            el('reviewFieldState').value=observation?.state || 'MISSING';el('reviewRawValue').value=observation?.raw_value || '';
            el('reviewNormalizedValue').value=Array.isArray(value)?value.join(', '):value==null?'':typeof value==='object'?JSON.stringify(value):String(value);
            el('reviewJustification').value='';el('reviewHumanAssertion').checked=false;
            el('reviewAlternatives').value=JSON.stringify(observation?.alternatives || [],null,2);
            this.selectedRefs=new Set(observation?.evidence_ids || []);el('reviewEvidenceFilter').value='';this.evidenceChoices();this.stateInputs();
            set('reviewValueHint',['day_of_week','end_day_offset'].includes(field)?'Use ISO weekday 1 (Monday) to 7 (Sunday), or the supported end-day offset. The raw reading remains separate.':field==='recurrence'?'Use the existing recurrence JSON schema; unknown boundaries stay absent.':field.endsWith('weeks')?'Enter only explicit week numbers separated by commas; retain the printed range in raw source reading.':'Use the source-supported normalized value. Dates use YYYY-MM-DD and times HH:MM.');
        }
        stateInputs() {
            const state=el('reviewFieldState').value;el('reviewNormalizedValue').disabled=state!=='PRESENT';
            el('reviewRawValue').disabled=state==='MISSING';
        }
        evidenceChoices() {
            const query=el('reviewEvidenceFilter').value.trim().toLowerCase(),select=el('reviewEvidenceRefs');select.replaceChildren();
            const eligible=this.evidence.filter(e=>this.selectedRefs.has(e.evidence_id) || `${e.raw_text} ${e.page} ${e.evidence_id}`.toLowerCase().includes(query));
            for(const e of eligible.slice(0,300)) {const option=node('option',`page ${e.page ?? '?'} · ${e.kind==='HUMAN_ASSERTION_NOT_GOLD' || e.evidence_id.startsWith('human:')?'human assertion':'source/derived evidence'} · ${e.raw_text.slice(0,130)} · ${e.evidence_id}`);option.value=e.evidence_id;option.selected=this.selectedRefs.has(e.evidence_id);select.append(option);}
            set('reviewEvidenceCount',`Showing ${Math.min(eligible.length,300)} of ${eligible.length} matches. ${this.selectedRefs.size} selected. Search to find other source references.`);
        }
        addChange() {
            if(!this.canCorrect())throw Error('Assigned, scoped authorization and an active review are required.');
            const field=el('reviewField').value,state=el('reviewFieldState').value;
            if(!this.session.correction_contract.fields.includes(field) || !STATES.includes(state))throw Error('Unsupported correction field or state.');
            if(!this.pending.has(field) && this.pending.size>=19)throw Error('One batch supports at most 19 fields.');
            const refs=[...this.selectedRefs];if(!refs.length || refs.length>20)throw Error('Select 1–20 valid source references.');
            const spans=refs.map(id=>this.evidence.find(e=>e.evidence_id===id));if(spans.some(e=>!e))throw Error('An evidence reference is unavailable.');
            if(new Set(spans.map(e=>e.page ?? null)).size>1)throw Error('One field cannot combine different or unknown page scopes.');
            const raw=state==='MISSING'?null:el('reviewRawValue').value.trim() || null,justification=el('reviewJustification').value.trim() || null;
            if(['SOURCE_ABSENT','NOT_APPLICABLE'].includes(state)&&!justification)throw Error('Source absence and applicability require a justification.');
            let alternatives;try{alternatives=JSON.parse(el('reviewAlternatives').value || '[]');}catch{throw Error('Alternatives must use the existing JSON array contract.');}
            if(!Array.isArray(alternatives))throw Error('Alternatives must be an array.');
            if(state==='MISSING')alternatives=[];
            if(state==='CONFLICTING' && new Set(alternatives.map(a=>a.raw_value)).size<2)throw Error('Conflicting state requires two distinct source alternatives.');
            if(state==='UNRESOLVED' && !raw && !alternatives.length)throw Error('Unresolved state needs retained raw text or alternatives.');
            if(state==='PRESENT'&&!raw)throw Error('A present field requires a raw source reading.');
            let human=null,evidenceIds=refs;
            if(el('reviewHumanAssertion').checked) {
                if(!raw || !justification || refs.some(id=>id.startsWith('human:')))throw Error('A new human reading requires raw text, a note and original source references.');
                const id='human:'+global.crypto.randomUUID().replaceAll('-','');
                human={evidence_id:id,raw_text:raw,source_evidence_ids:refs,page:spans[0].page ?? null,bbox:null,note:justification};evidenceIds=[id];
            }
            const correction={field_name:field,state,value:state==='PRESENT'?normalizedValue(field,el('reviewNormalizedValue').value):null,
                raw_value:raw,evidence_ids:evidenceIds,alternatives,justification};
            this.pending.set(field,{correction,human});this.renderPending();
        }
        renderPending() {
            const out=el('reviewPendingChanges');out.replaceChildren();
            for(const [field,item] of this.pending) {
                const row=node('div',null,'review-field-record'),remove=node('button','Remove field change','text-button');remove.type='button';remove.addEventListener('click',()=>{this.pending.delete(field);this.renderPending();});
                row.append(node('p',`${label(field)} → ${item.correction.state}: ${text(item.correction.value)}${item.human?' · explicit human assertion, not original OCR':''}`),remove);out.append(row);
            }
        }
        async transition(action) {
            if(!this.task || !this.session)return;
            const reason=el(['resolve','escalate','close'].includes(action)?'reviewDispositionReason':'reviewCorrectionReason').value.trim();
            if(['resolve','escalate','close'].includes(action)&&!reason) {this.feedback('Enter a reason before changing the task disposition.');return;}
            await this.action(async()=>{
                this.task=await this.request('POST',`/api/review/tasks/${this.task.task_id}/transition`,{expected_etag:this.task.etag,action,reason:reason || 'Operator requested assigned application review'});
                this.renderTask();this.feedback(`Backend confirmed task state ${this.task.state}. No Gold or calendar approval was granted.`);
            });
        }
        async submit() {
            if(!this.canCorrect() || this.busy)return;
            if(!this.pending.size){this.feedback('Add a source-supported field change first.');return;}
            const reason=el('reviewCorrectionReason').value.trim();if(!reason){this.feedback('A correction reason is required.');return;}
            const source=this.capture,taskId=this.task.task_id,items=[...this.pending.values()];
            const body={request_id:global.crypto.randomUUID(),expected_revision_id:this.task.current_event.revision.revision_id,
                expected_etag:this.task.etag,reason,fields:items.map(i=>i.correction),human_evidence:items.filter(i=>i.human).map(i=>i.human)};
            this.onInvalidate(source);this.feedback('Submitting correction — success has not yet been confirmed.');
            await this.action(async()=>{
                let confirmed=false;
                try {
                    this.task=await this.request('POST',`/api/review/tasks/${taskId}/corrections`,body);
                    confirmed=true;
                    this.pending.clear();this.renderPending();this.renderTask();
                    this.feedback(`Backend confirmed revision ${this.task.current_event.revision.version}. Revalidation is required; ACCEPT and calendar writes remain blocked.`);
                    await this.refreshSnapshot();await this.history(false);
                } catch(error) {
                    if(confirmed) {error.operationConfirmed=true;throw error;}
                    if(error.retained || error.status===409) {
                        const current=await this.request('GET',`/api/review/tasks/${taskId}`);this.task=current.task;this.capture=current.source;this.evidence=current.evidence;
                        this.pending.clear();this.renderPending();this.renderTask();await this.refreshSnapshot();
                    } else if(!(error instanceof ReviewClientError)) this.uncertain=true;
                    throw error;
                }
            });
        }
        async revalidate() {
            if(!this.task || !this.permitted('revalidate'))return;
            this.onInvalidate(this.capture);
            await this.action(async()=>{
                this.task=await this.request('POST',`/api/review/tasks/${this.task.task_id}/revalidate`,{expected_etag:this.task.etag});
                this.renderTask();this.feedback(`Revalidation confirmed: ${this.task.current_event.validation.status}; task ${this.task.state}; policy ${this.task.decision.decision}. Source correctness and schedule completeness remain unverified.`);
                try {await this.refreshSnapshot();await this.history(false);}catch(error){error.operationConfirmed=true;throw error;}
            });
        }
        async refreshSnapshot() {
            const snapshot=await this.request('GET',`/api/review/tasks/${this.task.task_id}/snapshot`);
            this.onSnapshot(snapshot,this.task);const out=el('reviewDiagnostics');out.replaceChildren();
            out.append(node('h3','Current revision read-only diagnostics'),node('p',`${snapshot.scope}. Complete schedule verified: false. Old previews are invalidated.`));
            // Reconstruction relationships are not calendar authority. Preserve
            // them in the snapshot; do not silently map them into M6 context.
            const bundle={packet:snapshot.packet,context:{},assessments:[],reconstruction_status:'NOT_PROVIDED'};
            const preview=await this.request('POST','/api/calendar/v1/preview',{bundle},true);
            if(preview.write_authorized!==false)throw Error('Unexpected calendar authorization response.');
            out.append(node('p','Calendar writes blocked. No old approval or preview is reused.'),details('Current read-only preview',preview));
        }
        async history(withAction=true) {
            if(!this.task)return;
            const render=async()=>{
                const history=await this.request('GET',`/api/review/tasks/${this.task.task_id}/history`),out=el('reviewHistory');out.replaceChildren();
                out.append(node('h3','Application correction history'),node('p','Process-local records; restart persistence is not provided. This is not independent annotation or Gold.'));
                if(!history.corrections.length)out.append(node('p','No corrections have been recorded for this task.'));
                for(const entry of history.corrections) {
                    const before=JSON.parse(entry.before_json),after=entry.after_json?JSON.parse(entry.after_json):null,proposal=JSON.parse(entry.proposal_json);
                    const row=node('section',null,'review-history-record');row.append(node('strong',`Revision ${before.revision.version} → ${after?.revision.version ?? 'not applied'} · ${entry.status}`),node('p',`${entry.recorded_at} · operator ${entry.actor_id}`),node('p',`Reason: ${entry.reason}`));
                    for(const change of proposal.fields)row.append(node('p',`${label(change.field_name)}: ${text(before.revision.content[change.field_name])} → ${text(after?.revision.content[change.field_name])}; evidence ${change.evidence_ids.join(', ')}`));
                    row.append(node('p',`Validation: ${entry.error_codes.join(', ') || 'see revision-bound revalidation entries below'}`));out.append(row);
                }
                for(const audit of history.audit)out.append(details(`${audit.recorded_at} · ${audit.operation} · ${audit.actor_id} · ${audit.from_state} → ${audit.to_state}`,{reason:audit.reason,payload:JSON.parse(audit.payload_json),entry_hash:audit.entry_hash}));
            };
            if(withAction)await this.action(render);else await render();
        }
        async endSession() {
            await this.action(async()=>{
                let revoked=false;
                try {await this.request('POST','/api/review/session/end',{});revoked=true;}catch {/* Explicit unconfirmed-revocation feedback is shown below. */}
                finally {this.forget();this.feedback(revoked?'Backend confirmed review grant revocation. Public inspection remains available.':'Browser review access was removed; host revocation could not be confirmed. Ask the trusted host to revoke the grant.');}
            });
        }
    }
    global.STAIRReview={Controller,normalizedValue,sourceKey,ReviewClientError};
})(typeof window!=='undefined'?window:globalThis);
