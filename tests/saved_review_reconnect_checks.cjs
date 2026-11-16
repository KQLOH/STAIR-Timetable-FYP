const fs=require('node:fs'),assert=require('node:assert/strict');
const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),caseName=process.argv[3];
const nodes=new Map();global.document={getElementById:id=>nodes.get(id)||null,createElement:tag=>({tagName:tag,append(){},replaceChildren(){},setAttribute(){},addEventListener(){}})};
nodes.set('savedReviewStatus',{textContent:''});
require('../frontend/js/upload.js');require('../frontend/js/review.js');const U=STAIRUpload;
const copy=x=>JSON.parse(JSON.stringify(x));
const response=(x,status=200)=>({ok:status===200,status,json:async()=>copy(x)});
function setup({snapshot=f.snapshot,queue=f.queue}={}){
 let active={packet:copy(f.original),result:copy(f.original_result)},projected=null,views=U.displayedEvents(active.result),calls=[];
 global.fetch=async(url,options)=>{assert(url.endsWith('/api/calendar/v1/preview'));assert.equal(JSON.parse(options.body).bundle.packet.events.length,snapshot.packet.events.length);return response(f.preview);};
 const controller=new STAIRReview.Controller({base:'http://synthetic.invalid',getSource:()=>active,openDialog(){},closeDialog(){},onForget(){projected=null;views=U.displayedEvents(active.result);},onSnapshot(s){projected=s;views=U.mergeSavedViews(U.displayedEvents(active.result),s);return true;},hostRequest:async(url,options)=>{
  calls.push({url,method:options.method});assert.equal(options.method,'GET','Review restore must never mutate');
  if(url.includes('/queue?'))return response(queue);if(url.endsWith('/snapshot'))return response(snapshot);throw Error(url);
 }});controller.session=copy(f.session);
 return {controller,calls,get views(){return views},get projected(){return projected},setActive(v){active=v},get active(){return active}};
}
(async()=>{
 if(caseName==='restore'){
  const x=setup();assert.equal(x.views.filter(U.needsStudentReview).length,1);
  const result=await x.controller.restoreSavedSource();assert.equal(result.status,'RESTORED');assert.equal(result.corrected,2);assert.equal(x.views.length,2);
  assert.equal(x.views.find(v=>v.subject==='CS101').venue,'Z018');assert.equal(x.views.find(v=>v.subject==='MA202').lecturer,'Alex River');
  assert.equal(x.views.filter(U.needsStudentReview).length,0);assert(x.views.every(v=>U.humanCorrected(v.canonical)));assert(x.views.every(v=>!U.independentlyVerified(v.canonical)));
  assert(x.views.every(v=>v.canonical.calendar.approval_status==='NOT_APPROVED'));assert(x.calls.every(c=>c.method==='GET'));
  const original=U.displayedEvents(f.original_result);assert.equal(original.find(v=>v.subject==='CS101').venue,'Unresolved venue');
 }else if(caseName==='unauthorized'){
  const x=setup();x.controller.session=null;assert.equal((await x.controller.restoreSavedSource()).status,'NOT_AUTHORIZED');assert.equal(x.calls.length,0);assert.equal(x.projected,null);
  const y=setup();y.controller.session.source_bindings[0][1]='b'.repeat(64);assert.equal((await y.controller.restoreSavedSource()).status,'WRONG_SCOPE');assert.equal(y.calls.length,0);
 }else if(caseName==='binding'){
  const wrongHash=copy(f.snapshot);wrongHash.packet.source.content_sha256='b'.repeat(64);const a=setup({snapshot:wrongHash});assert.equal((await a.controller.restoreSavedSource()).status,'NO_SAVED_REVIEW');assert.equal(a.projected,null);
  const wrongIdentity=copy(f.snapshot);for(const e of wrongIdentity.packet.events)e.identity.event_id='00000000-0000-4000-8000-000000000999';const b=setup({snapshot:wrongIdentity});assert.equal((await b.controller.restoreSavedSource()).status,'IDENTITY_MISMATCH');assert.equal(b.projected,null);
 }else if(caseName==='ambiguous'){
  const queue=copy(f.queue);queue.tasks.push({...queue.tasks[0],task_id:'00000000-0000-4000-8000-000000000888',run_id:'competing-run'});const x=setup({queue});assert.equal((await x.controller.restoreSavedSource()).status,'AMBIGUOUS_RUNS');assert.equal(x.projected,null);
 }else if(caseName==='machine_run'){
  const queue=copy(f.queue),other='00000000-0000-4000-8000-000000000777';queue.tasks.unshift({...queue.tasks[0],task_id:other,run_id:'machine-only-run'});
  const x=setup({queue});x.controller.transport=async(url,options)=>{
   assert.equal(options.method,'GET');if(url.includes('/queue?'))return response(queue);
   if(url.includes(other)){const snapshot=copy(f.snapshot);snapshot.packet=f.original;snapshot.tasks=[];return response(snapshot);}
   return response(f.snapshot);
  };
  assert.equal((await x.controller.restoreSavedSource()).status,'RESTORED');assert.equal(x.views.filter(v=>U.humanCorrected(v.canonical)).length,2);
 }else if(caseName==='source_change'){
  const x=setup();let release,started;const ready=new Promise(r=>started=r);x.controller.transport=async(url,options)=>{if(url.includes('/queue?'))return response(f.queue);started();await new Promise(r=>release=r);return response(f.snapshot);};const pending=x.controller.restoreSavedSource();await ready;x.setActive({...x.active,result:{...x.active.result}});release();assert.equal((await pending).status,'SOURCE_CHANGED');assert.equal(x.projected,null);
 }else if(caseName==='pending'){
  const x=setup({snapshot:f.pending_snapshot});await x.controller.restoreSavedSource();assert.equal(x.views.filter(U.needsStudentReview).length,2);assert(x.views.every(v=>U.humanCorrected(v.canonical)));assert(x.views.every(v=>!U.independentlyVerified(v.canonical)));
 }else if(caseName==='subset'){
  const s=copy(f.snapshot);s.packet.events=s.packet.events.slice(0,1);s.tasks=s.tasks.filter(t=>t.current_event.candidate_id===s.packet.events[0].candidate_id);const x=setup({snapshot:s});await x.controller.restoreSavedSource();assert.equal(x.views.length,2);assert.equal(x.views.filter(v=>v.savedReview).length,1);
 }else if(caseName==='verification'){
  const e=copy(f.snapshot.packet.events[0]);assert(!U.independentlyVerified(e));e.human_verification={status:'INDEPENDENTLY_VERIFIED',revision_id:'stale',actor_id:'synthetic',recorded_at:'2033-02-07T00:00:00Z',evidence_record_id:'synthetic-reference'};assert(!U.independentlyVerified(e));e.human_verification.revision_id=e.revision.revision_id;assert(U.independentlyVerified(e));e.human_verification.status='MANUALLY_VERIFIED_REFERENCE';assert(!U.independentlyVerified(e));
 }else throw Error(caseName);
 console.log('PASS '+caseName);
})().catch(e=>{console.error(e);process.exit(1)});
