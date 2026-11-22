/* Simple application editing. Authority and validation remain in the review API. */
(function (global) {
    'use strict';
    const FIELDS = {location:'Venue', lecturer:'Lecturer', course_code:'Course code', course_name:'Course name',
        session_type:'Class type', date:'Date', start_time:'Start time', end_time:'End time', section:'Group'};
    const $ = id => global.document.getElementById(id);
    function normalizedReading(field,value) {
        const cleaned=value.trim().replace(/\s+/g,' ');
        if(field==='course_code')return cleaned.replace(/\s+/g,'').toUpperCase();
        if(field==='session_type') {
            const aliases={l:'Lecture',lec:'Lecture',lecture:'Lecture',t:'Tutorial',tut:'Tutorial',tutorial:'Tutorial',p:'Practical',prac:'Practical',practical:'Practical',lab:'Practical',laboratory:'Practical'};
            return aliases[cleaned.toLowerCase().replace(/\.$/,'')] || cleaned;
        }
        return global.STAIRReview.normalizedValue(field,cleaned);
    }
    function proposal(task, field, value, note, span, confirmed) {
        if (!confirmed) throw Error('Confirm that you checked this reading in the original timetable.');
        if (!Object.hasOwn(FIELDS,field) || !value.trim() || !note.trim()) throw Error('Enter the corrected detail and a short reason.');
        if (!span || span.evidence_id.startsWith('human:') || span.kind === 'HUMAN_ASSERTION_NOT_GOLD' || !span.page)
            throw Error('Select an original source reading for this class.');
        const id='human:'+global.crypto.randomUUID().replaceAll('-','');
        return {request_id:global.crypto.randomUUID(),expected_revision_id:task.current_event.revision.revision_id,
            expected_etag:task.etag,reason:note.trim(),
            fields:[{field_name:field,state:'PRESENT',raw_value:value.trim(),
                value:normalizedReading(field,value),evidence_ids:[id],alternatives:[],justification:note.trim()}],
            human_evidence:[{evidence_id:id,raw_text:value.trim(),source_evidence_ids:[span.evidence_id],page:span.page,note:note.trim()}]};
    }
    class Editor {
        constructor({review,getSource,getUpload}) {Object.assign(this,{review,getSource,getUpload});this.saving=false;this.openSequence=0;}
        setup() {
            $('studentField').addEventListener('change',()=>this.changeField());
            $('studentReload').addEventListener('click',()=>this.open(this.candidateId));
            $('studentSave').addEventListener('click',()=>this.save());
            $('studentRecheck').addEventListener('click',async()=>{if(!this.sameSource())return;await this.review.revalidate();this.message(this.review.task?.state==='REVALIDATED'?'Check complete. Review any missing or uncertain class details before planning. Calendar synchronization remains unavailable.':'The check could not be completed. Your saved correction is retained. Refresh the class and try checking it again.');});
            $('studentOriginal').addEventListener('click',()=>{
                const upload=this.getUpload();
                if(upload?.previewUrl && upload.extraction?.source.content_sha256===this.openSource?.content_sha256 && upload.extraction.source.source_document_id===this.openSource?.source_document_id) global.open(upload.previewUrl,'_blank','noopener');
                else this.message('Reselect the matching original file to view it. Your saved corrections are retained.');
            });
            global.document.addEventListener('stair-review-update',()=>this.controls());
        }
        dirty() {return !!($('studentReason').value || $('studentConfirmed').checked || (this.initialValue!==undefined && $('studentValue').value!==this.initialValue));}
        sameSource() {const source=this.getSource().packet?.source;return !!source && (!global.STAIRAccountMode || (!!this.openRunId && this.openRunId===this.review.selectedRunId && this.review.task?.run_id===this.openRunId)) && source.source_document_id===this.review.capture?.source_document_id && source.content_sha256===this.review.capture?.content_sha256;}
        message(message) {$('studentFeedback').textContent=message;}
        controls() {
            $('studentSave').disabled=this.saving || !this.review.canCorrect() || !this.sameSource();
            $('studentRecheck').disabled=this.saving || !this.sameSource() || !this.review.task || !this.review.permitted('revalidate') || !['IN_PROGRESS','REVALIDATION_REQUIRED'].includes(this.review.task.state);
            if(!this.review.session) {
                $('studentValue').value='';$('studentReason').value='';$('studentConfirmed').checked=false;
                $('studentEvidence').replaceChildren();$('studentCrop').replaceChildren();
                this.message(global.STAIRAccountMode ? 'Sign in and upload your timetable to edit. Editing access connects automatically after reading the file.' : 'To save changes, open the local Windows review app, request access to this timetable and confirm the request in its console. Saved corrections stay private on your computer.');
            }
        }
        async open(candidateId) {
            if(this.saving || this.review.busy)return;
            if(this.dirty() && !global.confirm('Discard the unsaved edit and select another class?'))return;
            const sequence=++this.openSequence, source=this.getSource().packet;
            this.candidateId=candidateId;this.openSource=source?.source;this.openRunId=this.review.selectedRunId;this.review.openDialog();
            $('studentFeedback').textContent='Opening class…';$('studentReason').value='';$('studentConfirmed').checked=false;
            $('studentValue').value='';$('studentEvidence').replaceChildren();$('studentCrop').replaceChildren();
            const event=source?.events.find(e=>e.candidate_id===candidateId);
            $('studentTitle').textContent=event?.revision.content.course_code || event?.revision.content.course_name || 'Selected class';
            const fields=$('studentField');fields.replaceChildren();
            for(const [value,label] of Object.entries(FIELDS)){const option=document.createElement('option');option.value=value;option.textContent=label;fields.append(option);}
            if(!this.review.session){this.controls();this.sourceCrop(event,source?.source);return;}
            this.saving=true;this.controls();
            try {
                const binding=source.source;
                if(!this.review.session.source_bindings?.some(([id,hash])=>id===binding.source_document_id && hash===binding.content_sha256))throw Error('Request fresh access for this timetable before editing.');
                let tasks=[],offset=0;
                do {const page=await this.review.request('GET',`/api/review/queue?offset=${offset}&limit=50`);tasks.push(...page.tasks);offset=page.next_offset;}while(offset!=null && tasks.length<500);
                const matches=t=>(!global.STAIRAccountMode || (!!this.openRunId && t.run_id===this.openRunId)) && t.current_event?.candidate_id===candidateId && t.current_event.revision.source_document_id===binding.source_document_id;
                if(offset!=null)throw Error('There are too many saved classes to match safely. Use Advanced Details to find this class.');
                let candidates=tasks.filter(matches);
                if(!candidates.length && global.STAIRAccountMode)throw Error('This reading is still connecting. Close Edit class and try again when editing is ready.');
                if(!candidates.length) {
                    await this.review.request('POST','/api/review/ingest',global.STAIRUpload.reviewIngestBody(this.getSource().result));
                    const page=await this.review.request('GET','/api/review/queue?offset=0&limit=50');
                    candidates=page.tasks.filter(matches);
                }
                if(candidates.length!==1)throw Error('This class could not be matched uniquely to saved review work. Use Advanced Details to inspect the queue.');
                if(sequence!==this.openSequence)return;
                await this.review.selectTask(candidates[0].task_id);
                if(this.review.capture?.source_document_id!==binding.source_document_id || this.review.capture?.content_sha256!==binding.content_sha256 || !this.sameSource())throw Error('The saved class belongs to a different source. Changes were not enabled.');
                if(['PENDING_REVIEW','ASSIGNED_REVIEW','ESCALATED'].includes(this.review.task.state))await this.review.transition('claim');
                if(this.review.task?.state==='ASSIGNED_REVIEW')await this.review.transition('start');
                else if(['REVALIDATED','REVALIDATION_REQUIRED'].includes(this.review.task?.state))await this.review.transition('resume');
                this.populate();this.sourceCrop();
                this.message(this.review.canCorrect()?'Choose a detail, check the original and save your correction.':'Editing is unavailable for this class. '+$('reviewWorkflowStatus').textContent);
            } catch(error) {if(error.status===403)this.review.forget();this.message(error.message);}
            finally {this.saving=false;this.controls();}
        }
        async changeField() {
            if(this.saving || this.review.busy){$('studentField').value=this.field;return;}
            if(this.dirty() && !global.confirm('Discard this unsaved edit and choose another detail?')){$('studentField').value=this.field;return;}
            // Selecting a new detail explicitly starts another edit on the
            // checked revision; no confirmation or correction is submitted here.
            if(this.sameSource() && this.review.task?.state==='REVALIDATED')await this.review.transition('resume');
            this.populate();this.controls();
        }
        populate() {
            const task=this.review.task;this.field=$('studentField').value;
            $('studentValue').type=['start_time','end_time'].includes(this.field) && task?.current_event?.revision.content[this.field]!=='24:00'?'time':this.field==='date'?'date':'text';
            $('studentValue').value=task?.current_event?.revision.content[$('studentField').value] || '';
            this.initialValue=$('studentValue').value;
            $('studentConfirmed').checked=false;$('studentReason').value='';
            const event=task?.current_event, obs=event?.revision.observations || [];
            const selected=obs.find(o=>o.field_name===$('studentField').value);
            const ids=new Set([...(selected?.evidence_ids || []),...obs.flatMap(o=>o.evidence_ids)]);
            const spans=this.review.evidence.filter(s=>ids.has(s.evidence_id) && !s.evidence_id.startsWith('human:') && s.kind!=='HUMAN_ASSERTION_NOT_GOLD' && s.page && s.raw_text.trim());
            const options=$('studentEvidence');options.replaceChildren();const blank=document.createElement('option');blank.value='';blank.textContent='Choose the source text you checked';options.append(blank);
            for(const s of spans.slice(0,100)){const option=document.createElement('option');option.value=s.evidence_id;option.textContent=`Page ${s.page}: ${s.raw_text.slice(0,180)}`;options.append(option);}
        }
        sourceCrop(event=this.review.task?.current_event,capture=this.review.capture) {
            const upload=this.getUpload();
            if(!upload || !event)return;
            const original=upload.extraction?.source;
            if(original?.content_sha256!==capture?.content_sha256 || original?.source_document_id!==capture?.source_document_id)return;
            const target=$('studentCrop'),image=document.querySelector('#documentPreview img');target.replaceChildren();
            for(const {page,region} of global.STAIRUpload.eventRegions(event,upload)) {
                const b=region.bbox;
                if(image?.complete && image.naturalWidth===page.width && image.naturalHeight===page.height && page.page===1 && b.coordinate_space==='PIXELS' && [null,undefined,1].includes(page.transform?.source_exif_orientation) && b.x0>=0 && b.y0>=0 && b.x1<=page.width && b.y1<=page.height) {
                    const canvas=document.createElement('canvas');canvas.width=Math.ceil(b.x1-b.x0)*2;canvas.height=Math.ceil(b.y1-b.y0)*2;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Original class crop');canvas.getContext('2d').drawImage(image,b.x0,b.y0,b.x1-b.x0,b.y1-b.y0,0,0,canvas.width,canvas.height);target.append(canvas);
                }
            }
        }
        async save() {
            if(this.saving || this.review.busy || !this.review.canCorrect() || !this.sameSource())return;
            this.saving=true;this.controls();let confirmed=false,submitted=false;
            try {
                const span=this.review.evidence.find(s=>s.evidence_id===$('studentEvidence').value);
                const body=proposal(this.review.task,$('studentField').value,$('studentValue').value,$('studentReason').value,span,$('studentConfirmed').checked);
                const id=this.review.task.task_id,source=this.review.capture;
                this.review.onInvalidate(source);
                submitted=true;const task=await this.review.request('POST',`/api/review/tasks/${id}/corrections`,body);
                confirmed=true;this.review.task=task;this.review.renderTask();this.initialValue=$('studentValue').value;
                $('studentConfirmed').checked=false;$('studentReason').value='';
                await this.review.refreshSnapshot();
                await this.review.revalidate();
                if(this.review.task?.state!=='REVALIDATED')throw Error('The saved class still needs its follow-up check.');
                this.message(global.STAIRAccountMode ? 'Correction saved and checked. Open Calendar to confirm the updated dates and preview your changes before synchronizing.' : 'Correction saved on this computer. Check the updated calendar preview. Calendar sync remains unavailable; review does not approve external writes.');
            } catch(error) {
                if(submitted && !error.status && !confirmed)this.review.uncertain=true;
                if(error.status===403)this.review.forget();
                if(error.retained || error.status===409) {
                    await this.review.selectTask(this.review.task?.task_id);
                    $('studentConfirmed').checked=false;
                }
                this.message(confirmed?'Your correction was saved, but its follow-up check is incomplete. Use Check saved class.':submitted?error.message+' Your edit has not been confirmed saved. Refresh the class before retrying; do not repeat an uncertain submission.':error.message);
            } finally {this.saving=false;this.controls();}
        }
    }
    global.STAIRStudentReview={Editor,proposal};
})(globalThis);
