/* Synthetic DOM replay of genuine isolated M6/S3c responses; not live enrollment. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),which=process.argv[3];
const nodes=new Map(),events={};let unsafeHTML=0,storageAccess=0;
class Element{
 constructor(id='',tag='div'){Object.assign(this,{id,tagName:tag,children:[],listeners:{},attrs:{},disabled:false,checked:false,_text:'',_value:''});}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 set innerHTML(v){unsafeHTML++;this._text=v;this.children=[];}
 set value(v){this._value=String(v);}get value(){return this._value || (this.tagName==='select'?this.children[0]?.value || '':'');}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[];this._text='';this._value='';this.append(...items);}
 setAttribute(k,v){this.attrs[k]=String(v);}addEventListener(k,f){(this.listeners[k] ||= []).push(f);}click(){}
}
const html=fs.readFileSync('frontend/index.html','utf8');
for(const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){const item=new Element(m[2],m[1]);item.disabled=/\sdisabled(?:\s|>)/.test(m[0]);nodes.set(m[2],item);}
const get=id=>nodes.get(id);
global.document={getElementById:get,createElement:tag=>new Element('',tag),querySelector:()=>null};
global.window=global;global.addEventListener=(name,fn)=>events[name]=fn;
global.localStorage={getItem(){storageAccess++;throw Error('No storage authority');},setItem(){storageAccess++;throw Error('No storage authority');}};
const clone=v=>JSON.parse(JSON.stringify(v));const response=(data,status=200)=>({ok:status===200,status,json:async()=>clone(data)});
let stage=0,calls=[],failure=null,delay=null;
global.fetch=async()=>response({detail:'TEST_ONLY_CALENDAR_HOST_NOT_CONFIGURED'},403);
async function host(url,options){
 const path=new URL(url).pathname.split('/test-only')[1],body=options.body?JSON.parse(options.body):null;calls.push({path,body,method:options.method});
 if(delay)return delay;if(failure){const f=failure;failure=null;return response({detail:f.code},f.status);}
 if(path==='/session')return response(fixture.session);
 if(path==='/history')return response(stage===0?fixture.history_approved:fixture.history_create);
 if(path==='/state')return response(stage===0?fixture.state_before:fixture.state_create);
 if(path==='/preview')return response(body.fixture_id==='update'?fixture.update:fixture.create);
 if(path==='/approve'){
  assert.equal(body.preview_digest,controller.plan.preview_digest);assert.equal(body.snapshot_digest,controller.plan.snapshot_digest);
  assert.equal(body.input_digest,controller.plan.input_digest);assert.equal(body.target_sandbox_id,fixture.session.target_sandbox_id);
  assert.deepEqual(body.operation_keys,controller.plan.operations.map(o=>o.operation_key));assert.equal(body.owner_confirmation,true);
  return response(body.preview_digest===fixture.update.preview_digest?fixture.update_approval:fixture.approval);
 }
 if(path==='/execute'){stage=1;return response(calls.filter(c=>c.path==='/execute').length>1?fixture.replay:fixture.executed);}
 if(path==='/reconcile')return response(fixture.executed);
 if(path==='/revoke-approval')return response({approval_id:body.approval_id,revoked:true,external_calendar:false});
 if(path==='/session/end')return response({revoked:true});throw Error('Unexpected path '+path);
}
vm.runInThisContext(fs.readFileSync('frontend/js/sandbox.js','utf8'));
const controller=new STAIRSandbox.Controller({base:'http://synthetic.invalid',hostRequest:host});controller.setup();
async function begin(){await controller.connect();await controller.preview();}
async function approve(){await begin();get('sandboxOwnerConfirmation').checked=true;await controller.approve();}
const cases={
 async public_default_deny(){controller.transport=null;await controller.connect();assert.equal(controller.session,null);assert(get('sandboxWorkflowStatus').textContent.includes('not configured'));assert(get('executeSandbox').disabled);assert.equal(calls.length,0);},
 async authorized_catalogue_only(){await controller.connect();assert(get('sandboxAuthorization').textContent.includes('TEST_ONLY'));assert.equal(get('sandboxFixture').children.length,3);assert(get('sandboxSource').textContent.includes('2033-02-07'));assert(!get('previewSandbox').disabled);assert.equal(calls.some(c=>c.path==='/execute'),false);},
 async exact_diff_and_explicit_approval(){await begin();assert(get('sandboxPreview').textContent.includes('CREATE'));assert(get('sandboxPreview').textContent.includes('before'));assert(get('sandboxPreview').textContent.includes('source_bindings'));assert(get('approveSandbox').disabled);await controller.approve();assert(!calls.some(c=>c.path==='/approve'));get('sandboxOwnerConfirmation').checked=true;await controller.approve();assert.equal(controller.approval.approval_id,fixture.approval.approval_id);assert(!calls.some(c=>c.path==='/execute'));},
 async execute_and_idempotent_replay(){await approve();await controller.execute();assert(get('sandboxExecution').textContent.includes('CONFIRMED'));assert(get('sandboxExecution').textContent.includes('local sandbox state only'));assert.equal(controller.approval,null);await controller.execute(true);assert(get('sandboxExecution').textContent.includes('not resent'));assert(!get('sandboxExecution').textContent.includes('SYNCHRONIZED'));},
 async update_has_meaningful_exact_difference(){await controller.connect();get('sandboxFixture').value='update';controller.selectFixture();await controller.preview();assert(get('sandboxPreview').textContent.includes('UPDATE'));assert(get('sandboxPreview').textContent.includes('SYNTHETIC ROOM C'));assert.equal(controller.approval,null);},
 async source_selection_invalidates_approval(){await approve();get('sandboxFixture').value='update';controller.selectFixture();assert.equal(controller.approval,null);assert.equal(controller.plan,null);assert(!get('sandboxOwnerConfirmation').checked);assert(get('executeSandbox').disabled);},
 async history_real_journal_and_recovery_selection(){await approve();await controller.execute();assert(get('sandboxHistory').textContent.includes('SUBMIT'));assert(get('sandboxHistory').textContent.includes('LOCAL_OUTCOME'));assert(get('sandboxHistory').textContent.includes('Persistent TEST_ONLY local journal'));assert.equal(controller.selectedApproval(),fixture.approval.approval_id);await controller.reconcile();assert(get('sandboxWorkflowStatus').textContent.includes('Reconciliation observed local state'));},
 async stale_preview_not_reported_as_approval(){await begin();get('sandboxOwnerConfirmation').checked=true;failure={code:'STALE_PREVIEW',status:409};await controller.approve();assert.equal(controller.approval,null);assert(get('sandboxWorkflowStatus').textContent.includes('fresh preview'));assert(get('executeSandbox').disabled);},
 async revoked_approval_preserves_host_session(){await approve();failure={code:'APPROVAL_REVOKED',status:403};await controller.execute();assert(controller.session);assert(!get('sandboxWorkflowStatus').textContent.includes('confirmed local outcomes'));assert(get('sandboxWorkflowStatus').textContent.includes('revoked'));},
 async interrupted_execution_requires_reconciliation(){await approve();failure={code:'EXECUTION_INTERRUPTED_RECONCILE_REQUIRED',status:409};await controller.execute();assert(controller.uncertain);assert(get('replaySandbox').disabled);assert(get('sandboxWorkflowStatus').textContent.includes('reconcile'));await controller.reconcile();assert(!controller.uncertain);},
 async duplicate_click_submits_once(){await approve();await Promise.all([controller.execute(),controller.execute()]);assert.equal(calls.filter(c=>c.path==='/execute').length,1);},
 async expiry_clears_private_views(){await approve();failure={code:'TEST_ONLY_CALENDAR_UNAUTHORIZED',status:403};await controller.history();assert.equal(controller.session,null);assert.equal(controller.plan,null);assert.equal(get('sandboxHistory').children.length,0);assert.equal(get('sandboxSource').children.length,0);assert(get('executeSandbox').disabled);},
 async late_reply_cannot_restore_session(){let release;delay=new Promise(resolve=>release=resolve);const running=controller.connect();controller.forget();release(response(fixture.session));await running;assert.equal(controller.session,null);assert.equal(get('sandboxFixture').children.length,0);},
 async safe_text_and_no_storage_authority(){fixture.session.fixtures[0].label='<img src=x onerror=alert(1)>';await controller.connect();assert(get('sandboxFixture').textContent.includes('<img'));assert.equal(unsafeHTML,0);assert.equal(storageAccess,0);await controller.end();assert.equal(controller.transport,null);assert.equal(get('sandboxHistory').children.length,0);},
 async session_expiry_timer_clears_authority(){const real=global.setTimeout;let expires;global.setTimeout=(fn,ms)=>{if(ms===30000)return real(fn,ms);expires=fn;return {unref(){}};};try{await controller.connect();}finally{global.setTimeout=real;}expires();assert.equal(controller.session,null);assert(get('sandboxWorkflowStatus').textContent.includes('expired'));}
 ,async approval_expiry_requires_new_preview(){await begin();get('sandboxOwnerConfirmation').checked=true;const real=global.setTimeout;let expires;global.setTimeout=(fn,ms)=>{if(ms===30000)return real(fn,ms);expires=fn;return {unref(){}};};try{await controller.approve();}finally{global.setTimeout=real;}assert(get('replaySandbox').disabled);expires();assert.equal(controller.approval,null);assert.equal(controller.plan,null);assert(!get('sandboxOwnerConfirmation').checked);assert(get('approveSandbox').disabled);}
};
(async()=>{await cases[which]();console.log(JSON.stringify({case:which,status:'passed'}));})().catch(e=>{console.error(e.stack);process.exitCode=1;});
