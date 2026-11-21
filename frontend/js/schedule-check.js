/* Protected source-first inventory. No browser role minting or approval inference. */
(function(g){
'use strict';
const el=id=>document.getElementById(id), node=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;return n;};
class Controller {
 constructor(options){Object.assign(this,options);this.check=null;this.generation=0;this.sourceSignature=null;this.memory=new Map();this.clientIDs=new Map();this.dirty=false;this.editSequence=0;this.recoveryStatus="";}
 setup(){
  for(const [id,fn] of [['scheduleStart',()=>this.start()],['scheduleRestore',()=>this.restore()],['scheduleAnalyze',()=>this.analyze()],['scheduleSource',()=>this.source()]])el(id)?.addEventListener('click',()=>this.action(fn));
  el('scheduleGrounded')?.addEventListener('click',()=>this.action(()=>this.grounded()));
  el('scheduleCheckBody')?.addEventListener('input',()=>this.edited());el('scheduleCheckBody')?.addEventListener('change',()=>this.edited());
  g.addEventListener('beforeunload',event=>{if(this.dirty){event.preventDefault();event.returnValue='';}});
  document.addEventListener('stair-review-update',()=>this.syncReviewState());
  for(const id of ['analysisStart','analysisEnd','analysisZone','analysisScope','analysisCohort'])el(id)?.addEventListener('input',()=>{this.remember();this.status('Analysis range changed. Your current source-check edits are retained; start a new check explicitly to use a different scope.');});
 }
 packetSignature(packet){
  if(!packet)return null;
  const sorted=value=>Array.isArray(value)?value.map(sorted):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,sorted(value[k])])):value;
  return JSON.stringify(sorted({...packet,events:[...(packet.events||[])].sort((a,b)=>a.candidate_id.localeCompare(b.candidate_id)),evidence:[...(packet.evidence||[])].sort((a,b)=>a.evidence_id.localeCompare(b.evidence_id))}));
 }
 accessState(){
  const session=this.review.session,source=this.getSource()?.packet?.source;
  if(!session)return 'DISCONNECTED';
  if(session.expires_at&&Date.parse(session.expires_at)<=Date.now())return 'EXPIRED';
  if(!source)return 'NO_SOURCE';
  if(!session.source_bindings?.some(([id,hash])=>id===source.source_document_id&&hash===source.content_sha256))return 'WRONG_SOURCE';
  return session.permissions?.includes('correct')&&session.permissions?.includes('oversee')?'CONNECTED':'READ_ONLY';
 }
 syncReviewState(){
  const access=this.accessState(),packet=this.getSource()?.packet,signature=this.packetSignature(packet);
  if(access!=='CONNECTED'||this.sourceSignature!==signature){el('groundedAnalysisResult')?.replaceChildren();if(el('scheduleAIConsent'))el('scheduleAIConsent').checked=false;}
  if(access!=='CONNECTED'){
   if(this.check){this.remember();clearTimeout(this.draftTimer);this.generation++;this.check=null;this.dirty=false;if(this.sourceURL)URL.revokeObjectURL(this.sourceURL);el('scheduleOriginal')?.replaceChildren();el('scheduleCheckBody')?.replaceChildren();}
   const messages={DISCONNECTED:'Editing access is not connected. Reconnect the saved review to open its private checklist.',EXPIRED:'Editing access expired. Your encrypted draft is retained; reconnect this source.',NO_SOURCE:'Editing access is connected. Open the matching timetable first.',WRONG_SOURCE:'Editing access is connected to a different source. Select its matching original or request access for this timetable.',READ_ONLY:'Saved review access is connected but read-only. Whole-source checking requires the source owner’s review permissions.'};this.status(messages[access]);return;
  }
  if(this.check){
   const sameCapture=this.check.source.content_sha256===packet.source.content_sha256&&this.check.source.source_document_id===packet.source.source_document_id&&this.actor===this.review.session.actor_reference;
   if(!sameCapture){this.remember();clearTimeout(this.draftTimer);this.generation++;this.check=null;this.dirty=false;el('scheduleCheckBody')?.replaceChildren();el('scheduleOriginal')?.replaceChildren();this.status('Review access is connected to a new source. Its saved checklist can be opened separately.');}
   else if(this.sourceSignature!==signature){this.remember();this.stale=true;this.status('Review access is connected, but class revisions or source evidence changed. Your displayed draft is retained. It cannot be submitted against these newer revisions.');return;}
   else return;
  }
  this.status('Saved reviews are connected for this timetable. Open its saved checklist or start a new check.');
  if(el('view-analysis')?.classList.contains('active')&&(!el('analysisResearch')||el('analysisResearch').open)&&!this.review.busy)this.activate();
 }
 hydrateScope(scope){
  const values={analysisStart:scope.start,analysisEnd:scope.end,analysisZone:scope.timezone,analysisScope:scope.timetable_id,analysisCohort:scope.cohort_id||''};
  for(const [id,value] of Object.entries(values))if(el(id)&&!el(id).value)el(id).value=value;
 }
 async activate(){
  if(this.accessState()!=='CONNECTED'){this.syncReviewState();return;}
  if(this.check||this.activation||this.review.busy)return;
  const sequence=this.editSequence,session=this.review.session,signature=this.packetSignature(this.getSource()?.packet);const task=this.getSnapshot()?.tasks?.[0]?.task_id;
  if(!task){this.status('Review access is connected. Load the current saved class snapshot before opening its checklist.');return;}
  this.activation=true;
  try{
   if(this.review.session.source_check_recovery_version===1){
    const catalog=await this.request('GET','for-task/'+task+'/available');
    if(this.editSequence!==sequence||this.review.session!==session||this.packetSignature(this.getSource()?.packet)!==signature)return;
    const options=catalog.checks||[],withDraft=options.filter(c=>c.recovery_drafts>0);
    if(!options.length){this.status(catalog.stale_recovery_checks?'Your encrypted draft is retained for earlier class revisions. Review the updated classes before creating a new source check.':'Review access is connected. No saved source check exists for these current revisions; start one when ready.');return;}
    const remembered=sessionStorage.getItem(this.key());
    const chosen=options.find(c=>c.check_id===remembered&&c.recovery_drafts>0)||(withDraft.length===1?withDraft[0]:options.length===1?options[0]:null);
    if(!chosen){this.showChoices(options);return;}
    await this.loadExisting(chosen.check_id,sequence,signature,session);
    if(options.length>1)this.showChoices(options,true);
   }else{
    const id=sessionStorage.getItem(this.key());
    await this.loadExisting(id||'for-task/'+task,sequence,signature,session);
   }
  }catch(error){if(error.status===404)this.status('Review access is connected. The running host could not locate the checklist; restart it once for the recovery update. Existing drafts remain stored.');else this.connectionError(error);}
  finally{this.activation=false;}
 }
 async loadExisting(id,sequence=this.editSequence,signature=this.packetSignature(this.getSource()?.packet),session=this.review.session){
  const result=await this.request('GET',id);if(this.editSequence!==sequence||this.check||this.review.session!==session||this.packetSignature(this.getSource()?.packet)!==signature||this.accessState()!=='CONNECTED')return;
  const source=this.getSource()?.packet?.source;if(result.source.content_sha256!==source.content_sha256||result.source.source_document_id!==source.source_document_id)throw Error('The loaded checklist belongs to a different source. It was not displayed.');
  this.check=result;sessionStorage.setItem(this.key(),result.check_id);this.hydrateScope(result.scope);this.render();await this.offerRecovery();
 }
 showChoices(options,within=false){
  const target=within?this.recoveryBox:el('scheduleCheckBody');if(!within)target.replaceChildren();
  const choices=node('details');choices.open=!within;choices.append(node('summary','Stored checklists for this exact source'));
  for(const option of options){const button=node('button',`${option.recovery_rows?option.recovery_rows+' draft entries to recover':'Saved checklist'} · ${option.scope.start} to ${option.scope.end}`);button.type='button';button.className='button secondary';button.onclick=()=>this.action(async()=>{if(!await this.mayReplace())return;const result=await this.request('GET',option.check_id);this.check=result;sessionStorage.setItem(this.key(),result.check_id);this.hydrateScope(result.scope);this.render();await this.offerRecovery();});choices.append(button);}target.append(choices);
  this.status('Saved reviews are connected. Choose the stored checklist whose draft you want to recover; nothing was overwritten.');
 }
 connectionError(error){
  if(error.code==='UNAUTHORIZED_REVIEW'){this.review.forget?.();this.status('The host rejected an expired or revoked review session. Your stored draft is retained; reconnect this source.');}
  else if(error.code==='SCHEDULE_CHECK_STALE_SOURCE_OR_REVISION')this.status('The source check uses earlier revisions. Its draft is retained; do not overwrite it with a new check.');
  else this.status('Review access is connected, but the checklist request failed'+(error.status?' (HTTP '+error.status+')':'')+'. '+(error.message||'Try reopening after checking the host.'));
 }
 status(text){el('scheduleCheckStatus').textContent=text;}
 async action(fn){const generation=this.generation;try{await fn();}catch(error){if(generation!==this.generation)return;if(error.status===403||error.status===409||error.status===404){this.connectionError(error);return;}this.status(error.status===403?'Reconnect saved reviews or request the required independent reviewer access.':error.status===409?'This check is incomplete, stale, or lacks source/recurrence authority. Reopen Review Classes, resolve missing details, and start a current check.':error.message || 'The check could not be saved. Your previous history remains unchanged.');}}
 async request(method,path,body){if(!this.review.session)throw Error('Reconnect saved reviews first.');const generation=this.generation;const result=await this.review.request(method,'/api/review/schedule-checks/'+path,body);if(generation!==this.generation)throw Error('Source or authorization changed.');return result;}
 key(){const s=this.getSource().packet?.source;return s?'stair-schedule-check:'+s.content_sha256:null;}
 scope(){const p=this.getSource().packet,val=id=>el(id).value.trim();return {scope_id:'student-source-check',timetable_id:val('analysisScope')||p.source.timetable_id||'local:'+p.source.source_document_id,start:val('analysisStart'),end:val('analysisEnd'),timezone:val('analysisZone'),cohort_id:val('analysisCohort')||null};}
 async start(){
  if(!await this.mayReplace())return;
  if(!this.review.session)throw Error('Reconnect saved reviews first, then return here.');
  const snapshot=this.getSnapshot();let task=snapshot?.tasks?.[0]?.task_id;
  if(!task){
   if(!this.review.permitted('queue'))throw Error('This source requires an owner with timetable-review access.');
   try{const queued=await this.review.request('POST','/api/review/ingest',STAIRUpload.reviewIngestBody(this.getSource().result));task=queued.task_ids[0];}
   catch(error){if(error.code!=='RECONSTRUCTION_ALREADY_QUEUED')throw error;await this.review.restoreSavedSource();task=this.getSnapshot()?.tasks?.[0]?.task_id;}
   if(!task)throw Error('More than one saved reading may match. Reconnect the intended saved review in Review Classes first.');
   const current=await this.review.request('GET','/api/review/tasks/'+task+'/snapshot');if(this.review.onSnapshot(current)===false)throw Error('The source changed. Reopen the current upload.');
  }
  this.check=await this.request('POST','start',{task_id:task,scope:this.scope()});
  sessionStorage.setItem(this.key(),this.check.check_id);this.render();await this.offerRecovery();
 }
 async restore(){
  if(!await this.mayReplace())return;
  const id=sessionStorage.getItem(this.key()),task=this.getSnapshot()?.tasks?.[0]?.task_id;
  if(!id&&!task)throw Error('Reconnect the current saved review first.');
  const sequence=this.editSequence;const result=await this.request('GET',id||'for-task/'+task);if(this.editSequence!==sequence){this.status('You edited the form while Reopen was loading. Those edits were kept; reopen again only if you want to replace them.');return;}this.check=result;this.render();await this.offerRecovery();
 }
 async source(){
  const file=this.getUpload()?.file;if(!file)throw Error('Reopen the exact original upload to inspect all pages.');
  if(this.sourceURL)URL.revokeObjectURL(this.sourceURL);this.sourceURL=URL.createObjectURL(file);
  const view=el('scheduleOriginal');view.replaceChildren();
  const link=node('a','Open the entire original in another tab');link.href=this.sourceURL;link.target='_blank';link.rel='noopener noreferrer';view.append(link);
  if(file.type==='application/pdf'){
   if(!this.check)throw Error('Start or reopen the source check first, then view the original pages.');
   const pages=node('select');pages.setAttribute('aria-label','Original PDF page');
   for(let p=1;p<=this.check.manifest.page_count;p++){const o=node('option','Original page '+p);o.value=p;pages.append(o);}
   const image=node('img');image.alt='Original PDF page — no OCR text replacement';view.append(pages,image);
   const load=async()=>{
    const generation=this.generation,id=this.check.check_id;const page=Number(pages.value);pages.disabled=true;
    try{
     const result=await this.review.request('POST','/api/review/schedule-checks/'+id+'/source-page/'+page,file);
     if(generation!==this.generation||this.check?.check_id!==id)return;
     if(result.content_sha256!==this.getSource().packet.source.content_sha256||result.page!==page||typeof result.png_base64!=='string'||result.png_base64.length>12*1024*1024)throw Error('Original source page could not be confirmed.');
     image.src='data:image/png;base64,'+result.png_base64;
    }finally{pages.disabled=false;}
   };
   pages.addEventListener('change',()=>this.action(load));await load();
  }else if(file.type.startsWith('image/')){const image=node('img');image.src=this.sourceURL;image.alt='Original uploaded timetable';view.append(image);}
  else {const raw=node('pre');raw.textContent=await file.text();view.append(raw);}
 }

 render(){
  const c=this.check,out=el('scheduleCheckBody');this.stale=false;this.sourceSignature=this.packetSignature(this.getSource()?.packet);this.hydrateScope(c.scope);out.replaceChildren();this.actor=this.review.session?.actor_reference;this.dirty=false;this.recoveryStatus='';
  this.notice=node('p');this.notice.id='sourceCheckDraftStatus';this.notice.setAttribute('role','status');this.errors=node('div');this.errors.id='sourceCheckErrors';this.errors.setAttribute('role','alert');this.recoveryBox=node('div');out.append(this.notice,this.errors,this.recoveryBox);
  this.status(c.status==='DRAFT'?'Compare every class and every page with the original.':c.status==='INCOMPLETE'?'Check saved. Missing or uncertain sessions still need attention.':c.status.startsWith('INDEPENDENTLY_CHECKED')?'Independently checked record saved. Live independent authorization is required for study windows.':'Your check is saved. A separately authenticated independent verifier must check the source; self-confirmation does not unlock study windows.');
  out.append(node('p',`This source check covers ${c.scope.start} to ${c.scope.end} · ${c.scope.timezone} · Class group: ${c.scope.cohort_id||'Unknown'}`));
  const pages=node('fieldset');this.pageBoxes=pages;pages.append(node('legend','Pages inspected in the original'));
  for(let i=1;i<=c.manifest.page_count;i++){const label=node('label'),box=node('input');box.type='checkbox';box.value=i;box.className='schedule-page';box.checked=c.primary?.checked_pages.includes(i)||false;label.append(box,node('span',' Page '+i+' '));pages.append(label);}out.append(pages);
  const rows=node('div');out.append(rows);this.rows=rows;
  this.captureExtraDraft=null;this.restoreExtraDraft=null;g.STAIRTemporalScope?.render(out,this);this.extraBaseline=JSON.stringify(this.captureExtraDraft?.()||null);
  const invite=node('details');invite.append(node('summary','Invite an independent reviewer'),node('p','After saving a complete source check, ask the owner to share this exact check in the local host console. The reviewer uses a separate Chrome Guest window and their own Windows account password. No password is entered into STAIR web forms.'),node('code','invite-check '+c.check_id+' "<original timetable file>"'));out.append(invite);
  const saved=c.primary?.rows;
  if(saved)for(const row of saved)this.row(row);
  else for(const e of c.events)this.row({candidate_id:e.candidate_id,decision:'UNCERTAIN',page:1,source_session:'',evidence_ids:[],note:''});
  const add=node('button','Add a missed or uncertain session');add.type='button';add.className='button secondary';add.onclick=()=>{this.row({decision:'OMITTED',page:1,source_session:'',evidence_ids:[],note:''});this.edited();};out.append(add);
  if(c.synthetic)out.append(node('p','Synthetic test timetable — this checklist is not genuine independent verification.'));
  const source=node('input'),scope=node('input');this.sourceBox=source;this.scopeBox=scope;source.type=scope.type='checkbox';source.checked=!!c.primary?.source_confirmed;scope.checked=!!c.primary?.scope_confirmed;
  const l1=node('label'),l2=node('label');l1.append(source,node('span',' I inspected the entire original, including blank areas and all pages.'));l2.append(scope,node('span',' I checked the stated dates, class group, recurrence and exceptions against the source.'));out.append(l1,l2);
  const note=node('textarea');this.noteBox=note;note.placeholder='Describe what you checked and anything still uncertain';note.value=c.primary?.note||'';out.append(note);
  const save=node('button','Save my source check');save.id='saveSourceCheck';save.className='button primary';save.type='button';save.onclick=()=>this.save();out.append(save);
  if(c.primary){const id=node('code',c.check_id),copy=node('button','Copy Check ID');id.id='savedSourceCheckId';copy.id='copySourceCheckId';copy.type='button';copy.className='button secondary';copy.onclick=()=>this.action(async()=>{await navigator.clipboard.writeText(c.check_id);copy.textContent='Check ID copied';});out.append(node('p','Saved Check ID'),id,copy);}
  this.updateDraftStatus();
  if(c.independent_access){
   const confirm=node('input');confirm.type='checkbox';const label=node('label');label.append(confirm,node('span',' As the independently authorized verifier, I personally inspected the full original and reconciled every session, date, recurrence, exception, timezone and scope.'));out.append(label);
   const verify=node('button','Record independent source verification');verify.className='button secondary';verify.onclick=()=>this.action(async()=>{if(!confirm.checked)throw Error('Inspect the entire original and explicitly confirm your independent check.');this.check=await this.request('POST',c.check_id+'/verify',{expected_version:c.version,source_confirmed:true,inventory_confirmed:true,temporal_context_confirmed:true,note:note.value});this.render();});out.append(verify);
  }
  const advanced=node('details');advanced.append(node('summary','Advanced Details'),node('pre',JSON.stringify({status:c.status,blockers:c.blockers,audit:c.audit},null,2)));out.append(advanced);
 }
 row(value){
  const c=this.check,event=(c.context_events||c.events).find(e=>e.candidate_id===value.candidate_id),content=event?.revision.content,box=node('fieldset');box.append(node('legend',content?`${content.course_code||content.course_name||'Class'} · ${content.date||'Date unconfirmed'} · ${content.start_time||'?'}–${content.end_time||'?'}`:'Additional source session'));
  const select=node('select');for(const [id,label] of [['INCLUDE','Included and matches the source'],['OMITTED','Missing from extracted classes'],['UNCERTAIN','Uncertain — needs checking'],['NON_SESSION','Not a class — explain why']]){const o=node('option',label);o.value=id;o.selected=id===value.decision;select.append(o);}box.append(select);box.dataset.candidateId=value.candidate_id||'';
  if(content)box.append(node('p',`${content.session_type||'Type unconfirmed'} · Venue: ${content.location||'Not confirmed'} · Lecturer: ${content.lecturer||'Not confirmed'} · Class group: ${content.cohort_id||'Not confirmed'}`),node('p',`Teaching weeks: ${content.teaching_weeks==null?'Unknown':content.teaching_weeks.join(', ')||'None explicitly recorded'} · Excluded weeks: ${content.excluded_weeks==null?'Unknown':content.excluded_weeks.join(', ')||'None explicitly recorded'} · Recurrence: ${content.recurrence?.frequency||'Not supplied; no repetition inferred'}`));
  const label=node('input');label.placeholder='Where on the original? e.g. Tuesday, second class';label.value=value.source_session;box.append(label);
  const page=node('input');page.type='number';page.min=1;page.max=c.manifest.page_count;page.value=value.page;page.setAttribute('aria-label','Original page');box.append(page);
  const refs=node('select');refs.multiple=true;refs.setAttribute('aria-label','Original class evidence');const packet=this.getSource().packet;
  const original=c.events.find(e=>e.candidate_id===value.candidate_id);const owned=new Set((original?.revision.observations||[]).flatMap(o=>o.evidence_ids));
  for(const s of packet.evidence.filter(s=>owned.has(s.evidence_id))){const option=node('option',`Page ${s.page||1}: ${s.raw_text.slice(0,140)}`);option.value=s.evidence_id;option.selected=value.evidence_ids.includes(s.evidence_id);refs.append(option);}box.append(node('p','Select the original passage(s) you personally inspected for this class. A location or note alone does not select source evidence.'),refs);
  const note=node('input');note.placeholder='Missing details, uncertainty, or reason this is not a class';note.value=value.note;box.append(note);
  box.fields={decision:select,source_session:label,page,evidence_ids:refs,note};
  box.readDraft=()=>({decision:select.value,candidate_id:value.candidate_id||null,page:page.value,source_session:label.value,evidence_ids:[...refs.selectedOptions].map(o=>o.value),note:note.value});
  box.read=()=>({decision:select.value,candidate_id:value.candidate_id||null,page:Number(page.value),source_session:label.value,evidence_ids:[...refs.selectedOptions].map(o=>o.value),note:note.value});this.rows.append(box);if(!value.candidate_id){const remove=node('button','Remove extra draft row');remove.type='button';remove.onclick=()=>{box.remove();this.edited();};box.append(remove);}
 }
 captureForm(){if(!this.check||!this.rows)return null;return {rows:[...this.rows.children].map(row=>row.readDraft()),checked_pages:[...this.pageBoxes.querySelectorAll('input:checked')].map(x=>Number(x.value)),source_confirmed:this.sourceBox.checked,scope_confirmed:this.scopeBox.checked,note:this.noteBox.value,extra:this.captureExtraDraft?.()||null};}
 remember(){if(!this.check||!this.dirty)return;const form=this.captureForm();if(form)this.memory.set(this.check.check_id,{form,actor:this.actor,version:this.check.version,source:this.check.source_revision_digest});}
 updateDraftStatus(){if(!this.notice||!this.check)return;this.notice.textContent=this.dirty?'Unsaved draft'+(this.recoveryStatus?' — '+this.recoveryStatus:' — not a saved source check or verification'):this.check.current_complete_verification?'Independently verified source check':this.check.primary?'Saved source check — '+(this.check.status==='DRAFT'?'context requires rechecking':'not independently verified'):'Unsaved draft — no source check has been saved';}
 edited(){el('groundedAnalysisResult')?.replaceChildren();if(el('scheduleAIConsent'))el('scheduleAIConsent').checked=false;if(!this.check)return;this.dirty=true;this.editSequence++;this.remember();this.updateDraftStatus();clearTimeout(this.draftTimer);this.draftTimer=setTimeout(()=>this.flushDraft(),500);}
 clientId(){const key='stair-source-draft-client:'+this.check.check_id;if(!this.clientIDs.has(key)){let id;try{id=sessionStorage.getItem(key);}catch{}id=id||crypto.randomUUID();this.clientIDs.set(key,id);try{sessionStorage.setItem(key,id);}catch{}}return this.clientIDs.get(key);}
 async flushDraft(){
  clearTimeout(this.draftTimer);if(!this.check||!this.dirty||this.saving)return true;if(this.stale)return false;
  const check=this.check,form=this.captureForm(),client=this.clientId(),sequence=this.editSequence;this.remember();
  if(this.draftFlight){await this.draftFlight;return this.flushDraft();}
  this.draftFlight=(async()=>{try{await this.request('POST',check.check_id+'/draft',{expected_version:check.version,client_id:client,form});if(this.check===check){this.recoveryStatus='encrypted recovery copy stored on this computer; not submitted for verification';this.updateDraftStatus();}return true;}catch{if(this.check===check){this.recoveryStatus='recovery copy could not be saved; keep this tab open and reconnect if needed';this.updateDraftStatus();}return false;}})();
  const ok=await this.draftFlight;this.draftFlight=null;if(ok&&this.check===check&&this.editSequence!==sequence&&!this.saving)return this.flushDraft();return ok;
 }
 async mayReplace(){if(this.saving||this.preparing){this.status('Save is still in progress. Your entries are retained; wait for its result.');return false;}if(!this.dirty)return true;const backedUp=await this.flushDraft();return g.confirm('You have UNSAVED source-check edits. '+(backedUp?'An encrypted recovery copy was stored. ':'A recovery copy could not be confirmed; Cancel is safest. ')+'Replace the displayed edits with the saved check? Choose Cancel to keep working.');}
 applyDraft(form){
  this.rows.replaceChildren();for(const row of form.rows)this.row(row);
  for(const box of this.pageBoxes.querySelectorAll('input'))box.checked=form.checked_pages.includes(Number(box.value));
  this.sourceBox.checked=form.source_confirmed;this.scopeBox.checked=form.scope_confirmed;this.noteBox.value=form.note;
  if(form.extra)this.restoreExtraDraft?.(form.extra);this.edited();
 }
 async offerRecovery(){
  const check=this.check;try{const response=await this.request('GET',check.check_id+'/draft');if(this.check!==check)return;
   const local=this.memory.get(check.check_id),memory=local&&local.actor===this.actor&&local.version===check.version&&local.source===check.source_revision_digest?local.form:null;
   const draft=memory||response.drafts?.[0]?.form;if(!draft)return;
   this.recoveryBox.replaceChildren();this.recoveryBox.append(node('p','An unsubmitted recovery draft is available for this exact source and saved version. It is not verification.'));
   const recover=node('button','Recover my unsaved entries');recover.id='recoverSourceDraft';recover.type='button';recover.className='button secondary';recover.onclick=()=>this.action(async()=>{if(this.dirty&&!await this.mayReplace())return;this.applyDraft(draft);this.recoveryBox.replaceChildren();this.status('Your typed entries were recovered as an UNSAVED draft. Check the fields, then explicitly save your source check.');});this.recoveryBox.append(recover);
  }catch{if(this.check===check)this.status('Saved check loaded. Recovery could not be checked; existing encrypted history was not changed.');}
 }
 showErrors(error){
  this.errors?.replaceChildren();let first=null;
  const names={source_session:'original source location',evidence_ids:'original class evidence',page:'original page',note:'source-check note',decision:'review status',candidate_id:'class link',source_confirmed:'whole-source confirmation',scope_confirmed:'scope confirmation'};
  for(const item of error.fieldErrors||[]){const path=item.path||[],row=path[0]==='rows'?this.rows.children[path[1]]:null,field=path.at(-1),control=row?.fields?.[field]||(field==='note'?this.noteBox:field==='source_confirmed'?this.sourceBox:field==='scope_confirmed'?this.scopeBox:null);if(control){control.setAttribute('aria-invalid','true');first=first||control;}this.errors.append(node('p',(row?'Class '+(path[1]+1)+' — ':'')+(names[field]||'Form field')+': '+item.message));}
  if(first)first.focus();
  this.status('Save failed. Your edits are unchanged. '+(error.fieldErrors?.length?'Fix the highlighted fields and save again.':error.message||'Reconnect if necessary, then retry.'));this.updateDraftStatus();
 }
 async save(){
  if(this.stale){this.status('Current class revisions changed. Your draft is retained; it cannot be saved against a stale check.');return;}if(this.saving||this.preparing)return;this.preparing=true;if(el('saveSourceCheck'))el('saveSourceCheck').disabled=true;clearTimeout(this.draftTimer);this.dirty=true;this.remember();await this.flushDraft();
  const check=this.check;if(!check){this.preparing=false;return;}this.preparing=false;const form=this.captureForm(),sequence=this.editSequence;this.saving=true;el('saveSourceCheck').disabled=true;
  const body={expected_version:check.version,rows:form.rows.map(row=>({...row,page:Number(row.page)})),checked_pages:form.checked_pages,source_confirmed:form.source_confirmed,scope_confirmed:form.scope_confirmed,note:form.note};
  try{const result=await this.request('POST',check.check_id+'/primary',body);const newer=this.editSequence!==sequence||JSON.stringify(this.captureExtraDraft?.()||null)!==this.extraBaseline?this.captureForm():null;this.check=result;this.memory.delete(check.check_id);this.render();if(newer){this.applyDraft(newer);this.status('The submitted version was saved. Your newer edits are retained as an unsaved draft.');}else this.status('Source check saved on this computer. This is not independent verification.');}
  catch(error){this.remember();this.showErrors(error);}
  finally{this.saving=false;if(el('saveSourceCheck'))el('saveSourceCheck').disabled=false;if(this.dirty)this.flushDraft();}
 }
 async grounded(){
  if(this.dirty)throw Error('Save or resolve your source-check edits first.');
  if(!this.check)throw Error('Start or reopen a whole-timetable check first.');
  const start=el('studyStart').value,end=el('studyEnd').value;
  if(!start||!end)throw Error('Enter your availability start and end first.');
  if(!el('scheduleAIConsent').checked)throw Error('Read and check the provider consent before requesting AI suggestions.');
  const control=el('scheduleGrounded'),sequence=this.editSequence;control.disabled=true;
  el('groundedAnalysisResult').textContent='Checking the complete timetable and preparing suggestions…';
  try{
   const result=await this.request('POST',this.check.check_id+'/grounded',{availability:[{start,end}],allow_provider:true});
   if(this.dirty||sequence!==this.editSequence){el('groundedAnalysisResult').textContent='The checklist changed. Save it and request fresh suggestions.';return;}
   STAIRAnalysis.renderGrounded(result,el('groundedAnalysisResult'));
  }finally{control.disabled=false;el('scheduleAIConsent').checked=false;}
 }
 async analyze(){if(this.dirty)throw Error('Save or resolve your unsaved source-check edits before analyzing the checked schedule. Draft edits are not part of the saved verification.');if(!this.check)throw Error('Start or reopen your source check first.');const start=el('studyStart').value,end=el('studyEnd').value;if(!!start!==!!end)throw Error('Supply both availability dates, or leave both empty.');const result=await this.request('POST',this.check.check_id+'/analyze',{availability:start?[{start,end}]:[]});STAIRAnalysis.renderStudent(result,el('verifiedAnalysisResult')||el('analysisResult'));}
}
g.STAIRScheduleCheck={Controller};
})(typeof window!=='undefined'?window:globalThis);
