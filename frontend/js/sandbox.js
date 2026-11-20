/* S3c TEST_ONLY client. Trusted host transport is intentionally absent at startup. */
(function(global){
    'use strict';
    const el=id=>global.document.getElementById(id);
    const node=(tag,text)=>{const n=global.document.createElement(tag);if(text!=null)n.textContent=String(text);return n;};
    const detail=(title,value)=>{const n=node('details');n.append(node('summary',title),node('pre',JSON.stringify(value,null,2)));return n;};
    const prefix='/api/calendar/v1/test-only';
    function dateLabel(value){
        if(!/^\d{4}-\d{2}-\d{2}$/.test(value || ''))return 'Date not confirmed';
        const date=new Date(value+'T00:00:00Z');
        return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(date) : 'Date not confirmed';
    }
    function occurrenceLabel(occurrence){
        const start=/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(occurrence.start);
        const end=/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(occurrence.end);
        return start && end ? `${dateLabel(start[1])} · ${start[2]} – ${start[1]===end[1]?'':dateLabel(end[1])+' '}${end[2]}` : 'Date or time not confirmed';
    }
    function eventCard(content, title='Invented class'){
        const card=node('article');card.setAttribute('class','sandbox-sample-card');
        card.append(node('h4',`${title}: ${content.course_code || content.course_name || 'Unresolved class'}`));
        if(content.course_name)card.append(node('p',content.course_name));
        const occurrence=content.occurrences?.[0];
        card.append(node('p',occurrence ? occurrenceLabel(occurrence) : `${dateLabel(content.date)} · ${content.start_time || '?'} – ${content.end_time || '?'}`),
            node('p',`${content.session_type || 'Class type not confirmed'} · Timezone: ${content.source_timezone || 'not confirmed'}`),node('p',`Venue: ${content.location || 'not confirmed'}`));
        if(content.lecturer)card.append(node('p','Lecturer: '+content.lecturer));
        if(content.section)card.append(node('p','Group: '+content.section));
        if(content.occurrences?.length>1)card.append(node('p',`${content.occurrences.length} dated occurrences; inspect every date in Advanced Details.`));
        if(content.recurrence)card.append(node('p','Repeat schedule is part of the exact proposal; inspect its dates in Advanced Details.'));
        return card;
    }
    function reasonMessage(code){
        if(code.includes('TIMEZONE'))return 'The source timezone is not confirmed.';
        if(code.includes('RECURRENCE') || code.includes('TERM') || code.includes('WEEK_'))return 'The repeat schedule or its date range is not fully supported.';
        if(code.includes('TIME') || code.includes('TEMPORAL') || code.includes('DATE'))return 'The class date or time needs confirmation.';
        if(code.includes('M5') || code.includes('POLICY') || code.includes('VERIFICATION'))return 'The required acceptance or verification evidence is unavailable.';
        return 'A required source or safety check is incomplete. See Advanced Details.';
    }

    const knownRefusals=new Set(['APPROVAL_EXPIRED','APPROVAL_REVOKED','APPROVAL_GRANT_MISMATCH','APPROVAL_NOT_FOUND','STALE_APPROVAL','STALE_PREVIEW','STALE_SOURCE_REVISION','SOURCE_REVISION_CONFLICT','NO_RECORDED_ATTEMPT']);
    const messages={
        TEST_ONLY_CALENDAR_HOST_NOT_CONFIGURED:'The approved TEST_ONLY host is not configured. Public calendar writes remain blocked.',
        STALE_SOURCE_REVISION:'The source revision changed. Request a fresh preview and owner approval.',
        SOURCE_REVISION_CONFLICT:'This source revision is older or has an unsupported predecessor. Select the current registered fixture.',
        STALE_APPROVAL:'The local target changed. Request a fresh preview and owner approval.',
        STALE_PREVIEW:'The local target changed. Inspect a fresh preview before approving.',
        EXACT_OWNER_APPROVAL_MISMATCH:'Approval does not match the exact displayed source, target, snapshot and operations.',
        APPROVAL_EXPIRED:'Approval expired. Request a fresh preview and owner approval; prior attempts can still be reconciled.',
        APPROVAL_REVOKED:'This approval was revoked. It cannot authorize execution.',
        APPROVAL_GRANT_MISMATCH:'This approval belongs to a different grant. Reconcile prior intent or obtain fresh approval.',
        RECONCILIATION_REQUIRED:'An earlier outcome is uncertain. Reconcile it before requesting another approval.',
        NO_RECORDED_ATTEMPT:'This approval has no recorded execution attempt. Use explicit execution for a valid current approval; there is nothing to replay.',
        EXECUTION_INTERRUPTED_RECONCILE_REQUIRED:'Execution was interrupted after durable submission. Inspect history and reconcile before retrying.',
        LOCAL_JOURNAL_UNAVAILABLE_OR_INVALID:'The local journal could not be safely accessed. No success is assumed. Inspect recovery with the trusted host.'
    };
    class SandboxError extends Error{
        constructor(status,code){super(messages[code] || (status===403?'TEST_ONLY access was refused or expired. Reconnect through the approved host.':'The sandbox request was refused. Refresh history and inspect the current source and target before retrying.'));Object.assign(this,{status,code});}
    }
    class Controller{
        constructor({base,hostRequest=null}){Object.assign(this,{base,transport:hostRequest});this.generation=0;this.inflight=new Set();this.session=null;this.plan=null;this.approval=null;this.historyData=null;this.busy=false;this.uncertain=false;}
        setup(){
            const listen=(id,event,fn)=>el(id)?.addEventListener(event,fn);
            listen('connectSandboxHost','click',()=>this.connect());listen('endSandboxSession','click',()=>this.end());
            listen('sandboxFixture','change',()=>this.selectFixture());listen('sandboxOwnerConfirmation','change',()=>this.controls());
            listen('previewSandbox','click',()=>this.preview());listen('approveSandbox','click',()=>this.approve());
            listen('executeSandbox','click',()=>this.execute());listen('replaySandbox','click',()=>this.execute(true));
            listen('reconcileSandbox','click',()=>this.reconcile());listen('revokeSandboxApproval','click',()=>this.revoke());
            listen('sandboxRecoveryApproval','change',()=>{this.recoveryId=el('sandboxRecoveryApproval').value;this.controls();});listen('refreshSandboxHistory','click',()=>this.history());
            listen('openCalendarJournal','click',()=>{global.document.querySelector('[data-view="calendar"]')?.click();this.history();});
            global.addEventListener?.('pagehide',()=>this.forget());this.controls();
        }
        attachTrustedHostTransport(request){if(typeof request!=='function')throw Error('Host-owned transport required');this.forget();this.transport=request;}
        status(text){el('sandboxWorkflowStatus').textContent=text;}
        forget(){
            this.generation++;for(const a of this.inflight)a.abort();this.inflight.clear();clearTimeout(this.expiry);clearTimeout(this.approvalExpiry);
            this.transport=null;this.session=null;this.plan=null;this.approval=null;this.historyData=null;this.recoveryId=null;this.uncertain=false;
            for(const id of ['sandboxSource','sandboxPreview','sandboxExecution','sandboxHistory','sandboxFixture','sandboxRecoveryApproval'])el(id)?.replaceChildren();
            el('sandboxOwnerConfirmation').checked=false;el('sandboxFailure').value='NONE';
            el('sandboxAuthorization').textContent='Public mode — no TEST_ONLY operator enrolled.';this.controls();
        }
        permitted(p){return this.session?.permissions.includes(p)===true;}
        selectedApproval(){return this.recoveryId || this.approval?.approval_id || el('sandboxRecoveryApproval').value;}
        controls(){
            const disable=(id,yes)=>{if(el(id))el(id).disabled=!!yes || this.busy;};
            const actionable=this.plan?.operations.some(o=>['CREATE','UPDATE','NO_OP'].includes(o.action));
            disable('connectSandboxHost',false);disable('endSandboxSession',!this.session);disable('sandboxFixture',!this.session);
            disable('previewSandbox',!this.permitted('preview') || !el('sandboxFixture')?.value);
            disable('sandboxOwnerConfirmation',!this.permitted('approve') || !actionable || !!this.approval);
            disable('approveSandbox',!this.permitted('approve') || !actionable || !el('sandboxOwnerConfirmation')?.checked || !!this.approval);
            disable('executeSandbox',!this.permitted('execute') || !this.approval || this.uncertain || Date.parse(this.approval?.expires_at)<=Date.now());
            disable('sandboxFailure',!this.permitted('execute') || !this.session?.failure_simulation_enabled);
            disable('refreshSandboxHistory',!this.session);disable('sandboxRecoveryApproval',!this.session || !this.historyData?.approvals.length);
            disable('reconcileSandbox',!this.permitted('reconcile') || !this.selectedApproval());
            disable('replaySandbox',!this.permitted('execute') || !this.historyData?.outcomes.some(o=>o.approval_id===this.selectedApproval()) || this.uncertain);
            disable('revokeSandboxApproval',!this.permitted('approve') || !this.selectedApproval());
            el('sandboxWorkflow')?.setAttribute('aria-busy',String(this.busy));
        }
        async request(method,path,body){
            // Every request in one action belongs to the selection that started it.
            const generation=this.actionGeneration ?? this.generation;
            if(generation!==this.generation)throw new SandboxError(403,'SESSION_ENDED');
            const abort=new AbortController();this.inflight.add(abort);const timer=setTimeout(()=>abort.abort(),30000);
            try{
                const options={method,credentials:'omit',cache:'no-store',signal:abort.signal,headers:{}};
                if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
                const response=await (this.transport?this.transport(this.base+prefix+path,options):global.fetch(this.base+prefix+path,options));
                const data=await response.json();if(generation!==this.generation)throw new SandboxError(403,'SESSION_ENDED');
                if(!response.ok)throw new SandboxError(response.status,typeof data.detail==='string'?data.detail:'REQUEST_REJECTED');
                return data;
            }finally{clearTimeout(timer);this.inflight.delete(abort);}
        }
        async action(work){
            if(this.busy)return;const generation=this.generation,opener=global.document.activeElement;this.actionGeneration=generation;this.busy=true;this.controls();this.status('Waiting for local backend confirmation…');
            try{await work();}catch(error){
                // A superseded response must neither replace the new guidance nor end a newer view.
                if(generation!==this.generation)return;
                if(knownRefusals.has(error.code)){this.plan=null;this.approval=null;el('sandboxOwnerConfirmation').checked=false;}
                if(error.status===403 && !['APPROVAL_REVOKED','APPROVAL_GRANT_MISMATCH'].includes(error.code))this.forget();
                this.status((error.confirmed?'The operation was confirmed, but the history refresh failed. ':'')+(error instanceof SandboxError?error.message:'Outcome could not be confirmed. Refresh history and reconcile the recorded attempt; do not assume failure or resubmit blindly.'));
            }finally{this.actionGeneration=undefined;this.busy=false;this.controls();if(global.document.activeElement===global.document.body && opener?.isConnected && !opener.disabled)opener.focus();}
        }
        async connect(){await this.action(async()=>{
            this.session=await this.request('GET','/session');
            if(this.session.mode!=='TEST_ONLY' || this.session.external_calendar!==false)throw new SandboxError(403,'INVALID_TEST_SCOPE');
            const remaining=Date.parse(this.session.expires_at)-Date.now();if(!Number.isFinite(remaining)||remaining<=0||remaining>61*60000)throw new SandboxError(403,'SESSION_EXPIRED');
            clearTimeout(this.expiry);this.expiry=setTimeout(()=>{this.forget();this.status('TEST_ONLY authorization expired. Private sandbox views cleared.');},remaining);this.expiry.unref?.();
            el('sandboxAuthorization').textContent=`Owner-authorized TEST_ONLY access · expires ${this.session.expires_at}. Invented data only; no external calendar.`;
            el('sandboxFixture').replaceChildren();for(const f of this.session.fixtures){const o=node('option',f.label);o.value=f.fixture_id;el('sandboxFixture').append(o);}
            this.selectFixture({initial:true});await this.history(false);this.status('Approved TEST_ONLY session loaded. Inspect a registered synthetic timetable, then request a new preview and explicit owner approval before execution.');
        });}
        selectFixture({initial=false}={}){
            // Initial catalogue rendering stays in connect's action; user changes invalidate it.
            if(!initial)this.generation++;
            this.plan=null;this.approval=null;this.uncertain=false;clearTimeout(this.approvalExpiry);
            el('sandboxOwnerConfirmation').checked=false;el('sandboxPreview').replaceChildren();el('sandboxExecution').replaceChildren();
            const f=this.session?.fixtures.find(f=>f.fixture_id===el('sandboxFixture').value);el('sandboxSource').replaceChildren();
            if(f){
                for(const event of f.events)el('sandboxSource').append(eventCard(event.revision.content,'Invented source class'));
                el('sandboxSource').append(detail('Advanced Details: registered TEST_ONLY source and canonical revisions',{source:f.source,events:f.events}));
            }
            this.status('Synthetic fixture selected. Previous preview and approval cleared from this view. A new preview and explicit owner approval are required. Check history for any earlier pending operation.');
            this.controls();
        }
        async preview(){if(!this.permitted('preview'))return;await this.action(async()=>{
            this.plan=null;this.approval=null;el('sandboxOwnerConfirmation').checked=false;el('sandboxPreview').replaceChildren();
            this.plan=await this.request('POST','/preview',{fixture_id:el('sandboxFixture').value});
            const p=this.plan,out=el('sandboxPreview');out.append(node('p',`TEST_ONLY · ${p.operations.length} proposed action(s). Preview is not execution.`));
            const issues=p.eligibility.filter(e=>!e.sandbox_write_permitted);
            out.append(node('p',issues.length?'Some invented classes are blocked. Check the reasons below.':'These invented classes are eligible only for this local test sandbox. Real calendar writes remain blocked.'));
            for(const issue of issues)for(const reason of [...new Set(issue.reason_codes.map(reasonMessage))])out.append(node('p',reason));
            for(const o of p.operations){
                const card=node('article');card.setAttribute('class','sandbox-operation'+(['BLOCKED','CONFLICT','MISSING_OBSERVATION'].includes(o.action)?' sandbox-blocked':''));
                card.append(node('h4',`${o.action==='MISSING_OBSERVATION'?'Source class missing':o.action} · ${o.after?.course_code || o.before?.course_code || 'Unresolved class'}`));
                if(o.before)card.append(eventCard(o.before,'Before'));
                if(o.after)card.append(eventCard(o.after,'After'));
                if(o.action==='NO_OP')card.append(node('p','Already matches the local calendar. No event will be added or changed.'));
                if(o.action==='CREATE')card.append(node('p','Add this invented class to the local calendar.'));
                if(o.action==='UPDATE')card.append(node('p','Change the existing local class; no duplicate will be created.'));
                if(o.reason_codes.length)for(const reason of [...new Set(o.reason_codes.map(reasonMessage))])card.append(node('p',reason));
                card.append(detail('Advanced Details: exact before/after action',{before:o.before,after:o.after,changed_fields:o.changed_fields,reason_codes:o.reason_codes}));out.append(card);
            }
            out.append(detail('Advanced Details: exact preview and source binding',{preview_digest:p.preview_digest,snapshot_digest:p.snapshot_digest,input_digest:p.input_digest,source_bindings:p.source_bindings}),detail('Advanced Details: eligibility assessment (operational writes remain BLOCKED)',p.eligibility));
            this.status('Preview confirmed. Inspect the exact before/after values and evidence before owner approval. No execution has occurred.');
        });}
        async approve(){
            if(!this.permitted('approve') || !this.plan || !el('sandboxOwnerConfirmation').checked)return;
            await this.action(async()=>{const p=this.plan;this.approval=await this.request('POST','/approve',{preview_digest:p.preview_digest,snapshot_digest:p.snapshot_digest,input_digest:p.input_digest,target_sandbox_id:p.target_sandbox_id,operation_keys:p.operations.filter(o=>['CREATE','UPDATE','NO_OP'].includes(o.action)).map(o=>o.operation_key),owner_confirmation:true});
                clearTimeout(this.approvalExpiry);const id=this.approval.approval_id;
                this.recoveryId=id;
                this.approvalExpiry=setTimeout(()=>{if(this.approval?.approval_id===id){this.approval=null;this.plan=null;el('sandboxOwnerConfirmation').checked=false;this.controls();this.status('Owner approval expired. Request a fresh preview before another execution. Recorded attempts can still be reconciled.');}},Math.max(0,Date.parse(this.approval.expires_at)-Date.now()));this.approvalExpiry.unref?.();
                this.status(`Exact TEST_ONLY approval recorded; expires ${this.approval.expires_at}. No execution yet.`);try{await this.history(false);}catch(e){e.confirmed=true;throw e;}
            });
        }
        results(data){
            const out=el('sandboxExecution');out.replaceChildren();
            for(const o of data.outcomes){
                const operation=[...(this.plan?.operations || []),...(this.historyData?.approved_previews || []).flatMap(p=>p.operations)].find(p=>p.operation_key===o.operation_key);
                const replayConfirmed=o.duplicate_submission && o.status==='CONFIRMED';
                const action=replayConfirmed?'NO_OP (replay)':o.duplicate_submission?'Recorded attempt (not resent)':operation?.action || 'Local action';
                const outcome=o.status==='CONFIRMED'?'Confirmed in local calendar':o.status==='FAILED_CONFIRMED'?'Not applied':o.status==='PREVIEW_REQUIRED'?'Fresh preview and approval required':'Needs reconciliation';
                out.append(node('p',`${action} · ${operation?.after?.course_code || operation?.before?.course_code || 'Invented class'} · ${outcome}${replayConfirmed?' · previously confirmed; not resent':o.duplicate_submission?' · no new action was sent':''}`),detail('Advanced Details: observed result',o));
            }
            out.append(node('p','Confirmation concerns observed local sandbox state only. No Google Calendar synchronization is claimed.'));
        }
        async execute(replay=false){
            const id=replay?this.selectedApproval():this.approval?.approval_id;if(!id||!this.permitted('execute')||this.uncertain)return;
            this.recoveryId=id;
            if(!replay && Date.parse(this.approval.expires_at)<=Date.now()){this.approval=null;this.controls();this.status(messages.APPROVAL_EXPIRED);return;}
            await this.action(async()=>{let confirmed=false;try{
                const data=await this.request('POST','/execute',{approval_id:id,failure_scenario:replay?'NONE':el('sandboxFailure').value,replay_only:replay});confirmed=true;
                this.results(data);this.uncertain=data.outcomes.some(o=>['PENDING','OUTCOME_UNKNOWN','RECONCILIATION_REQUIRED'].includes(o.status));
                this.approval=null;this.plan=null;el('sandboxOwnerConfirmation').checked=false;
                el('sandboxPreview').append(node('p','This preview is now historical. Request a fresh preview before another approval.'));
                this.status(this.uncertain?'Outcome uncertain. Reconcile the recorded attempt before another approval.':'Backend reported local outcomes. Inspect each result; request a fresh preview before another approval.');
                await this.history(false);
            }catch(e){if(confirmed)e.confirmed=true;else if(this.actionGeneration===this.generation)this.uncertain=!knownRefusals.has(e.code);throw e;}});
        }
        async reconcile(){const id=this.selectedApproval();if(!id||!this.permitted('reconcile'))return;await this.action(async()=>{
            const data=await this.request('POST','/reconcile',{approval_id:id});this.results(data);this.uncertain=data.outcomes.some(o=>['PENDING','OUTCOME_UNKNOWN','RECONCILIATION_REQUIRED'].includes(o.status));this.approval=null;
            this.status(this.uncertain?'Local state still mismatches intent. Host investigation is required; no blind retry.':'Reconciliation observed local state. PREVIEW_REQUIRED means obtain fresh preview and approval, not resubmit the old operation.');
            try{await this.history(false);}catch(e){e.confirmed=true;throw e;}
        });}
        async revoke(){const id=this.selectedApproval();if(!id||!this.permitted('approve'))return;await this.action(async()=>{
            await this.request('POST','/revoke-approval',{approval_id:id});this.approval=null;this.status('Backend confirmed approval revocation. Existing entries were not deleted.');try{await this.history(false);}catch(e){e.confirmed=true;throw e;}
        });}
        async history(withAction=true){
            const work=async()=>{const history=await this.request('GET','/history'),state=await this.request('GET','/state');this.historyData=history;
                const out=el('sandboxHistory');out.replaceChildren();out.append(node('h4','Local TEST_ONLY calendar'),node('p',`${state.entries.length} invented class(es) currently saved. History survives restart; fresh access and approval are required.`));
                for(const entry of state.entries)out.append(eventCard(entry.payload,'Saved test class'));
                const names={INITIALISE:'Sandbox created',PREVIEW:'Preview prepared',APPROVAL:'Owner approval recorded',SUBMIT:'Approved action submitted',LOCAL_OUTCOME:'Local result recorded',RECONCILE:'Recorded attempt checked',REVOKE_APPROVAL:'Approval revoked'};
                for(const record of history.journal.slice(-12))out.append(node('p',`${names[record.operation] || 'Local history recorded'} · ${record.recorded_at}`));
                const advanced=node('details');advanced.append(node('summary','Advanced Details: Persistent TEST_ONLY local journal'),node('p',`Target version ${state.calendar_version}; ${history.staging_file_count} retained staging file(s). Integrity checked, not encrypted or externally signed.`),detail('Current local entries',state.entries),detail('Authorized owner/session',this.session));
                for(const r of history.journal)advanced.append(detail(`${r.sequence} · ${r.operation} · ${r.recorded_at}`,r));
                for(const p of history.approved_previews || [])advanced.append(detail(`Historical approved exact diff · ${p.preview_digest}`,{source_bindings:p.source_bindings,operations:p.operations}));
                advanced.append(detail('Recorded outcomes and recovery state',history.outcomes));out.append(advanced);
                const select=el('sandboxRecoveryApproval'),old=this.recoveryId || select.value;select.replaceChildren();
                for(const a of [...history.approvals].reverse()){const o=node('option',`${a.recorded_at} · ${a.revoked?'Revoked':Date.parse(a.expires_at)<=Date.now()?'Expired':'Recorded'}`);o.value=a.approval_id;select.append(o);}
                if(history.approvals.some(a=>a.approval_id===old))select.value=old;
            };
            if(withAction)await this.action(work);else await work();
        }
        async end(){await this.action(async()=>{let confirmed=false;try{await this.request('POST','/session/end',{});confirmed=true;}finally{this.forget();this.status(confirmed?'Backend confirmed TEST_ONLY grant revocation. Local journal retained.':'Browser access cleared; host grant revocation was not confirmed. Ask the host to revoke it.');}});}
    }
    global.STAIRSandbox={Controller,SandboxError};
})(typeof window!=='undefined'?window:globalThis);
