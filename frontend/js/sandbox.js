/* S3c TEST_ONLY client. Trusted host transport is intentionally absent at startup. */
(function(global){
    'use strict';
    const el=id=>global.document.getElementById(id);
    const node=(tag,text)=>{const n=global.document.createElement(tag);if(text!=null)n.textContent=String(text);return n;};
    const detail=(title,value)=>{const n=node('details');n.append(node('summary',title),node('pre',JSON.stringify(value,null,2)));return n;};
    const prefix='/api/calendar/v1/test-only';
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
                if(error.status===403 && !['APPROVAL_REVOKED','APPROVAL_GRANT_MISMATCH'].includes(error.code))this.forget();
                this.status((error.confirmed?'The operation was confirmed, but the history refresh failed. ':'')+(error instanceof SandboxError?error.message:'Outcome could not be confirmed. Refresh history and reconcile the recorded attempt; do not assume failure or resubmit blindly.'));
            }finally{this.actionGeneration=undefined;this.busy=false;this.controls();if(global.document.activeElement===global.document.body && opener?.isConnected && !opener.disabled)opener.focus();}
        }
        async connect(){await this.action(async()=>{
            this.session=await this.request('GET','/session');
            if(this.session.mode!=='TEST_ONLY' || this.session.external_calendar!==false)throw new SandboxError(403,'INVALID_TEST_SCOPE');
            const remaining=Date.parse(this.session.expires_at)-Date.now();if(!Number.isFinite(remaining)||remaining<=0||remaining>61*60000)throw new SandboxError(403,'SESSION_EXPIRED');
            clearTimeout(this.expiry);this.expiry=setTimeout(()=>{this.forget();this.status('TEST_ONLY authorization expired. Private sandbox views cleared.');},remaining);this.expiry.unref?.();
            el('sandboxAuthorization').textContent=`TEST_ONLY operator ${this.session.actor_reference} · target ${this.session.target_sandbox_id} · expires ${this.session.expires_at}. Local journal only; no external calendar.`;
            el('sandboxFixture').replaceChildren();for(const f of this.session.fixtures){const o=node('option',f.label);o.value=f.fixture_id;el('sandboxFixture').append(o);}
            this.selectFixture({initial:true});await this.history(false);this.status('Approved TEST_ONLY session loaded. Inspect a registered synthetic timetable, then request a new preview and explicit owner approval before execution.');
        });}
        selectFixture({initial=false}={}){
            // Initial catalogue rendering stays in connect's action; user changes invalidate it.
            if(!initial)this.generation++;
            this.plan=null;this.approval=null;this.uncertain=false;clearTimeout(this.approvalExpiry);
            el('sandboxOwnerConfirmation').checked=false;el('sandboxPreview').replaceChildren();el('sandboxExecution').replaceChildren();
            const f=this.session?.fixtures.find(f=>f.fixture_id===el('sandboxFixture').value);el('sandboxSource').replaceChildren();
            if(f)el('sandboxSource').append(detail('Registered TEST_ONLY source and canonical revisions',{source:f.source,events:f.events}));
            this.status('Synthetic fixture selected. Previous preview and approval cleared from this view. A new preview and explicit owner approval are required. Check history for any earlier pending operation.');
            this.controls();
        }
        async preview(){if(!this.permitted('preview'))return;await this.action(async()=>{
            this.plan=null;this.approval=null;el('sandboxOwnerConfirmation').checked=false;el('sandboxPreview').replaceChildren();
            this.plan=await this.request('POST','/preview',{fixture_id:el('sandboxFixture').value});
            const p=this.plan,out=el('sandboxPreview');out.append(node('p',`TEST_ONLY target ${p.target_sandbox_id}; snapshot ${p.snapshot_version}; ${p.operations.length} operation(s). Preview is not execution.`),detail('Exact preview and source binding',{preview_digest:p.preview_digest,snapshot_digest:p.snapshot_digest,input_digest:p.input_digest,source_bindings:p.source_bindings}),detail('Eligibility assessment (operational writes remain BLOCKED)',p.eligibility));
            for(const o of p.operations)out.append(detail(`${o.action} · ${o.after?.course_code || o.before?.course_code || 'unresolved'} · ${o.operation_key}`,{before:o.before,after:o.after,changed_fields:o.changed_fields,reason_codes:o.reason_codes}));
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
            for(const o of data.outcomes)out.append(node('p',`${o.status} · ${o.reason_code} · ${o.operation_key}${o.duplicate_submission?' · duplicate request observed; not resent':''}`));
            out.append(node('p','Confirmation concerns observed local sandbox state only. No Google Calendar synchronization is claimed.'));
        }
        async execute(replay=false){
            const id=replay?this.selectedApproval():this.approval?.approval_id;if(!id||!this.permitted('execute')||this.uncertain)return;
            this.recoveryId=id;
            if(!replay && Date.parse(this.approval.expires_at)<=Date.now()){this.approval=null;this.controls();this.status(messages.APPROVAL_EXPIRED);return;}
            await this.action(async()=>{let confirmed=false;try{
                const data=await this.request('POST','/execute',{approval_id:id,failure_scenario:replay?'NONE':el('sandboxFailure').value,replay_only:replay});confirmed=true;
                this.results(data);this.uncertain=data.outcomes.some(o=>['PENDING','OUTCOME_UNKNOWN','RECONCILIATION_REQUIRED'].includes(o.status));
                this.approval=null;this.status(this.uncertain?'Outcome uncertain. Reconcile the recorded attempt before another approval.':'Backend reported local outcomes. Inspect each result; partial failure is not full success.');
                await this.history(false);
            }catch(e){if(confirmed)e.confirmed=true;else if(this.actionGeneration===this.generation)this.uncertain=true;throw e;}});
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
                const out=el('sandboxHistory');out.replaceChildren();out.append(node('h4','Persistent TEST_ONLY local journal'),node('p',`${state.entries.length} local entries; target version ${state.calendar_version}; ${history.staging_file_count} retained staging file(s). Integrity checked, not encrypted or externally signed.`),detail('Current local entries',state.entries));
                for(const r of history.journal)out.append(detail(`${r.sequence} · ${r.operation} · ${r.recorded_at}`,r));
                for(const p of history.approved_previews || [])out.append(detail(`Historical approved exact diff · ${p.preview_digest}`,{source_bindings:p.source_bindings,operations:p.operations}));
                out.append(detail('Recorded outcomes and recovery state',history.outcomes));
                const select=el('sandboxRecoveryApproval'),old=this.recoveryId || select.value;select.replaceChildren();
                for(const a of [...history.approvals].reverse()){const o=node('option',`${a.approval_id} · ${a.revoked?'REVOKED':Date.parse(a.expires_at)<=Date.now()?'EXPIRED':'recorded'} · ${a.preview_digest}`);o.value=a.approval_id;select.append(o);}
                if(history.approvals.some(a=>a.approval_id===old))select.value=old;
            };
            if(withAction)await this.action(work);else await work();
        }
        async end(){await this.action(async()=>{let confirmed=false;try{await this.request('POST','/session/end',{});confirmed=true;}finally{this.forget();this.status(confirmed?'Backend confirmed TEST_ONLY grant revocation. Local journal retained.':'Browser access cleared; host grant revocation was not confirmed. Ask the host to revoke it.');}});}
    }
    global.STAIRSandbox={Controller,SandboxError};
})(typeof window!=='undefined'?window:globalThis);
