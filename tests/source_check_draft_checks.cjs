/* Nine synthetic entries; no browser credentials, sources or real human actions. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes={};function element(){return {textContent:'',disabled:false,value:'',append(){},replaceChildren(){},setAttribute(){}};}
for(const id of ['scheduleCheckStatus','saveSourceCheck'])nodes[id]=element();
global.document={getElementById:id=>nodes[id],createElement:()=>element()};
vm.runInThisContext(fs.readFileSync('frontend/js/schedule-check.js','utf8'));
const fixture=()=>({rows:Array.from({length:9},(_,i)=>({candidate_id:'synthetic-'+i,decision:i%2?'UNCERTAIN':'INCLUDE',page:'1',source_session:'Typed location '+i,evidence_ids:['synthetic-source'],note:'Typed note '+i})),checked_pages:[1],source_confirmed:true,scope_confirmed:true,note:'Synthetic unsaved note',extra:null});
function controller(){const c=new STAIRScheduleCheck.Controller({review:{session:{actor_reference:'SYNTHETIC'}},getSource:()=>({packet:{source:{content_sha256:'synthetic'}}}),getSnapshot:()=>({})});c.check={check_id:'synthetic-check',version:1,source_revision_digest:'synthetic-binding'};c.actor='SYNTHETIC';c.notice=element();c.captureForm=()=>structuredClone(c.form);c.form=fixture();c.dirty=true;c.flushDraft=async()=>true;c.extraBaseline='null';return c;}
(async()=>{
 const test=process.argv[2];const c=controller();
 if(test==='reopen_cancel'){let calls=0,confirmations=0;c.request=async()=>{calls++;return c.check};global.confirm=()=>{confirmations++;return false};const before=structuredClone(c.form);await c.restore();assert.equal(confirmations,1);assert.equal(calls,0);assert.deepEqual(c.form,before);}
 if(test==='failed_save'){let renders=0;c.render=()=>renders++;c.showErrors=e=>{c.seen=e};c.request=async()=>{throw Object.assign(Error('Invalid'),{status:422,fieldErrors:[{path:['rows',4,'evidence_ids'],message:'Select class evidence'}]})};const before=structuredClone(c.form);await c.save();assert.deepEqual(c.form,before);assert.equal(renders,0);assert.equal(c.seen.status,422);assert(c.dirty);assert.deepEqual(c.memory.get(c.check.check_id).form,before);}
 if(test==='reopen_late_edit'){c.dirty=false;global.sessionStorage={getItem:()=>c.check.check_id};c.key=()=>'';let resolve;c.request=()=>new Promise(r=>resolve=r);let renders=0;c.render=()=>renders++;const work=c.restore();await new Promise(r=>setImmediate(r));c.form.rows[8].note='Newer typing';c.editSequence++;resolve({check_id:'server'});await work;assert.equal(renders,0);assert.equal(c.form.rows[8].note,'Newer typing');}
 if(test==='new_edits_during_save'){let resolve;c.request=()=>new Promise(r=>resolve=r);c.render=()=>{c.dirty=false};let applied;c.applyDraft=f=>{applied=f;c.dirty=true};const work=c.save();await new Promise(r=>setImmediate(r));c.form.rows[8].note='Typed while saving';c.editSequence++;resolve({...c.check,version:2,primary:{}});await work;assert.equal(applied.rows[8].note,'Typed while saving');assert(c.dirty);}
 if(test==='dirty_analysis_blocked'){let calls=0;c.request=async()=>{calls++;};await assert.rejects(c.analyze(),/unsaved/);assert.equal(calls,0);}
 if(test==='temporal_inputs_preserved'){c.captureExtraDraft=()=>({temporal_weeks:1,controls:[]});c.request=async()=>({...c.check,version:2,primary:{}});c.render=()=>{c.dirty=false};let applied;c.applyDraft=f=>{applied=f;c.dirty=true};await c.save();assert(applied);assert(c.dirty);}

 if(['connected_navigation','packet_key_order','revision_stale_preserved','wrong_scope_hidden','scope_restored','expired_access_hidden','load_source_change'].includes(test)){
  const packet={source:{source_document_id:'synthetic-document',content_sha256:'synthetic'},events:[{candidate_id:'synthetic-event',revision:{version:1}}],evidence:[{evidence_id:'synthetic-source',raw_text:'Synthetic source'}]};
  c.getSource=()=>({packet});c.check.source=packet.source;c.review.session={actor_reference:'SYNTHETIC',source_bindings:[['synthetic-document','synthetic']],permissions:['read','correct','oversee']};c.sourceSignature=c.packetSignature(packet);let clears=0;nodes.scheduleCheckBody={replaceChildren(){clears++}};nodes.scheduleOriginal={replaceChildren(){clears++}};const before=structuredClone(c.form);
  if(test==='packet_key_order')packet.events[0]={revision:{version:1},candidate_id:'synthetic-event'};
  if(test==='revision_stale_preserved')packet.events[0].revision.version=2;
  if(test==='wrong_scope_hidden')c.review.session.source_bindings=[['wrong','wrong']];
  if(test==='expired_access_hidden')c.review.session.expires_at='2000-01-01T00:00:00Z';
  if(test==='load_source_change'){const check=c.check;c.check=null;let resolve,renders=0;c.request=()=>new Promise(r=>resolve=r);c.render=()=>renders++;const pending=c.loadExisting('synthetic-check');packet.events[0].revision.version=2;resolve(check);await pending;assert.equal(renders,0);assert.equal(c.check,null);}
  else if(test==='scope_restored'){for(const id of ['analysisStart','analysisEnd','analysisZone','analysisScope','analysisCohort'])nodes[id]=element();c.hydrateScope({start:'2026-01-05',end:'2026-01-11',timezone:'Etc/UTC',timetable_id:'synthetic',cohort_id:'synthetic-cohort'});assert.equal(nodes.analysisZone.value,'Etc/UTC');nodes.analysisZone.value='Asia/Singapore';c.hydrateScope({timezone:'Etc/UTC'});assert.equal(nodes.analysisZone.value,'Asia/Singapore');}
  else {c.syncReviewState();if(['wrong_scope_hidden','expired_access_hidden','load_source_change'].includes(test)){assert.equal(c.check,null);assert.equal(clears,2);assert(c.memory.size===1);}else{assert(c.check);assert.equal(clears,0);assert.deepEqual(c.form,before);assert(c.dirty);assert.equal(!!c.stale,test==='revision_stale_preserved');}}
 }
 console.log('Source draft regression passed: '+test);
})().catch(error=>{console.error(error);process.exitCode=1});
