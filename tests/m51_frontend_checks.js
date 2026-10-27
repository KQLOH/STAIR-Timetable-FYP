/* Dependency-free client checks. All fixtures and actors are synthetic. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const caseName = process.argv[3];
const root = path.resolve(__dirname, '..');
class Element {
    constructor(tag) { this.tagName=tag;this.children=[];this.dataset={};this.style={};this.attributes={};this.listeners={};this.value='';this.disabled=false;this.textContent='';this.className='';
        this.classList={toggle:(name,on)=>{this.lastToggle=[name,on];},add:()=>{},remove:()=>{},contains:()=>false}; }
    append(...items) { for (const item of items) { item.parentElement=this;this.children.push(item); } }
    appendChild(item) { this.append(item); }
    replaceChildren(...items) { this.children=[];this.append(...items); }
    addEventListener(name,listener) { this.listeners[name]=listener; }
    setAttribute(name,value) { this.attributes[name]=value; }
    querySelectorAll(selector) { return this.children.flatMap(e=>[e,...e.querySelectorAll(selector)]).filter(e=>selector==='*' || selector==='img' && e.tagName==='img' || selector==='.evidence-box' && e.className.includes('evidence-box')); }
    remove() { if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(e=>e!==this); }
    click() { return this.listeners.click?.({target:this}); }
}
const elements=new Map();
global.document={createElement:tag=>new Element(tag),getElementById:id=>elements.get(id)||null,
    querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{}};
global.window=global;global.addEventListener=()=>{};
for(const file of ['upload.js','intranet.js'])vm.runInThisContext(fs.readFileSync(path.join(root,'frontend/js',file),'utf8'),{filename:file});
const U=global.STAIRUpload;
const clone=value=>JSON.parse(JSON.stringify(value));
const response=(value,status=200)=>({ok:status>=200&&status<300,status,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(value),json:async()=>value});
const png=()=>new File([Buffer.from(fixtures.png,'base64')],'synthetic.png',{type:'image/png'});
function replies(call,options,pipeline='B2H-v1') {
    if(call.includes('/api/extract'))return response(fixtures.extractions[pipeline]);
    if(call.endsWith('/api/reconstruct'))return {...response(fixtures.reconstruction),text:async()=>fixtures.reconstruction_json};
    if(call.endsWith('/api/policy/assess'))return response(fixtures.policy);
    if(call.endsWith('/eligibility'))return response(fixtures.eligibility);
    if(call.endsWith('/preview'))return response(fixtures.preview);
    throw Error('Unexpected route '+call);
}
function client(fetcher) { return new U.Controller({fetcher}); }
const cases={
    async raw_upload() {
        const calls=[];const file=png();const c=client(async(url,options)=>{calls.push([url,options]);return replies(url,options);});
        const result=await c.run(file,'B2H-v1');
        assert.equal(calls.length,5);assert.match(calls[0][0],/pipeline=B2H-v1$/);assert.equal(calls[0][1].body,file);
        assert.equal(calls[0][1].headers['X-Filename'],'synthetic.png');assert.equal(calls[0][1].headers['Content-Type'],'image/png');
        assert.equal(calls[0][1].credentials,'omit');assert.equal(result.stages.calendar,'BLOCKED_READ_ONLY');
        assert(calls.every(([url])=>!url.includes('/review/')&&!url.endsWith('/sync')));
        const request=JSON.parse(calls[1][1].body);assert.equal(request.decoder_mode,'constrained');assert.equal(request.profile,'m4-development-v1');
        assert.deepEqual(request.packet,result.extraction);assert.deepEqual(request.context,{});
        const m5=JSON.parse(calls[2][1].body);assert.deepEqual(m5.reconstruction,fixtures.reconstruction);
        const m6=JSON.parse(calls[3][1].body);assert.deepEqual(m6.assessments,[]);assert.equal(result.policy[0].decision,'ABSTAIN');c.reset();
    },
    async exact_envelope() {
        let body;
        const c=client(async(url,options)=>{if(url.endsWith('/api/policy/assess'))body=options.body;return replies(url,options);});
        await c.run(png(),'B2H-v1');assert(body.includes('"reconstruction":'+fixtures.reconstruction_json));
        assert(fixtures.reconstruction_json.includes('20.0'));assert(body.includes('20.0'));c.reset();
    },
    async file_input() {
        const calls=[];const old=global.fetch;global.fetch=async(url,options)=>{calls.push([url,options]);return replies(url,options);};
        try {
            vm.runInThisContext(fs.readFileSync(path.join(root,'frontend/js/app.js'),'utf8'));
            const input=new Element('input');input.files=[png()];elements.set('fileInput',input);
            const pipeline=new Element('select');pipeline.value='B2H-v1';elements.set('extractionPipeline',pipeline);
            setupSourceButtons();input.listeners.change({target:input});
            const deadline=Date.now()+5000;while(uploadController.result?.busy && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,2));
            assert.equal(uploadController.result.busy,false);assert(calls[0][0].includes('/api/extract?pipeline=B2H-v1'));
            assert.equal(calls[0][1].body,input.files[0]);assert.equal(state.source,'upload');assert(state.events.length>0);uploadController.reset();
        } finally {global.fetch=old;}
    },
    async late_response() {
        let resolveFirst;let started;const firstStarted=new Promise(resolve=>started=resolve);
        const updates=[];let first=true;
        const c=new U.Controller({notify:r=>updates.push(r.filename),fetcher:async(url,options)=>{
            if(first){first=false;started();return new Promise(resolve=>resolveFirst=resolve);}return replies(url,options);}});
        const firstRun=c.run(png(),'B1');await firstStarted;
        const second=new File([Buffer.from(fixtures.png,'base64')],'second.png',{type:'image/png'});
        await c.run(second,'B2H-v1');resolveFirst(response(fixtures.extractions.B1));await firstRun;
        assert.equal(c.result.filename,'second.png');assert.equal(updates.at(-1),'second.png');assert.equal(c.result.pipeline,'B2H-v1');c.reset();
    },
    async pipelines() {
        for(const pipeline of U.PIPELINES) {
            const calls=[];const c=client(async(url,options)=>{calls.push(url);return replies(url,options,pipeline);});
            const r=await c.run(png(),pipeline,{reconstruct:false});assert(r.extraction);assert.match(calls[0],new RegExp(`pipeline=${pipeline}$`));
            if(pipeline==='evidence')assert.equal(r.extraction.events.length,0);c.reset();
        }
        const c=client(()=>{throw Error('Must not request');});await c.run(png(),'b2');assert.equal(c.result.stages.extraction,'FAILED');
    },
    async file_validation() {
        await assert.rejects(U.validateFile(new File(['x'],'a.exe')),/Unsupported/);
        await assert.rejects(U.validateFile(new File([],'a.png',{type:'image/png'})),/nonempty/);
        await assert.rejects(U.validateFile(new File(['wrong'],'a.png',{type:'image/png'})),/signature/);
        await assert.rejects(U.validateFile(new File(['%PDF-x'],'a.pdf',{type:'image/jpeg'})),/disagree/);
        await assert.rejects(U.validateFile({name:'a.png',size:U.MAX_BYTES+1}),/20 MiB/);
        assert.equal((await U.validateFile(new File([Buffer.from([255,216,255,0])],'a.JPG',{type:'image/jpeg'}))).mime,'image/jpeg');
        assert.equal((await U.validateFile(new File(['%PDF-1'],'a.pdf',{type:'application/pdf'}))).mime,'application/pdf');
        const f=new File([Buffer.from(fixtures.png,'base64')],'课程.png',{type:'image/png'});assert.equal((await U.validateFile(f)).filename,'__.png');
    },
    async extraction_failure() {
        const c=client(async()=>response({detail:{code:'OCR_UNAVAILABLE',diagnostic_code:'OCR_EXECUTABLE_MISSING',message:'No executable'}},422));
        const r=await c.run(png(),'B1');assert.equal(r.stages.extraction,'FAILED');assert.equal(r.extraction,null);assert.match(r.diagnostics[0],/OCR_EXECUTABLE_MISSING/);c.reset();
    },
    async bad_response() {
        const c=client(async()=>({ok:true,status:200,headers:{get:()=> 'text/html'},text:async()=>'<html>'}));
        await c.run(png(),'evidence');assert.match(c.result.diagnostics[0],/non-JSON/);c.reset();
        const d=client(async()=>response({success:true,pipeline:'evidence',data:{}}));await d.run(png(),'evidence');assert.equal(d.result.stages.extraction,'FAILED');d.reset();
    },
    async no_candidates() {
        for(const pipeline of ['B1','B2']) { const packet=clone(fixtures.extractions[pipeline]);packet.data.events=[];
            packet.data.issues=[{code:pipeline+'_NO_CANDIDATES',evidence_ids:[]}];
            const c=client(async(url,options)=>url.includes('/extract')?response(packet):replies(url,options,pipeline));
            const r=await c.run(png(),pipeline,{reconstruct:false});assert.equal(r.stages.extraction,'NO_CANDIDATES');assert.equal(U.displayedEvents(r).length,0);assert.equal(r.extraction.issues[0].code,pipeline+'_NO_CANDIDATES');c.reset(); }
    },
    async partial_and_alternatives() {
        const r={pipeline:'B2H-v1',extraction:fixtures.extractions['B2H-v1'].data};
        const views=U.displayedEvents(r);assert(views.length>0);assert.equal(views[0].completeCandidate,false);
        assert.equal(views[0].canonical,r.extraction.events[0]);assert.equal(views[0].canonical.calendar.approval_status,'NOT_APPROVED');
    },
    async reconstruction_failure() {
        const c=client(async(url,options)=>url.endsWith('/reconstruct')?response({detail:'Evidence insufficient'},422):replies(url,options));
        const r=await c.run(png(),'B2H-v1');assert.equal(r.stages.reconstruction,'FAILED');assert.equal(r.stages.policy,'NOT_ASSESSED');
        assert(r.extraction.evidence.length);assert.equal(r.calendar.eligibility.policy_status,'BLOCKED');c.reset();
    },
    async incomplete_search() {
        const payload=clone(fixtures.reconstruction);payload.canonical_output.status='SEARCH_INCOMPLETE';
        assert.equal(U.validateReconstruction(payload).status,'SEARCH_INCOMPLETE');
        assert.equal(U.validateReconstruction({canonical_output:null,status:'EXECUTION_ABORTED',scorable:false}),null);
        payload.canonical_output.decoder_mode='unconstrained';assert.throws(()=>U.validateReconstruction(payload),/constrained/);
    },
    async policy_unavailable() {
        const c=client(async(url,options)=>url.endsWith('/api/policy/assess')?response({detail:'Unavailable'},403):replies(url,options));
        const r=await c.run(png(),'B2H-v1');assert.equal(r.policy,null);assert.equal(r.stages.policy,'NOT_ASSESSED');assert.equal(r.stages.calendar,'BLOCKED_READ_ONLY');c.reset();
        const p=clone(fixtures.policy);p.decisions[0].decision='ACCEPT';assert.throws(()=>U.validatePolicy(p,fixtures.reconstruction.canonical_output.packet.events),/ACCEPT/);
    },
    async calendar_safety() {
        const c=client(async(url,options)=>url.endsWith('/eligibility')?response({policy_status:'ELIGIBLE',external_calendar:true,events:[]}):replies(url,options));
        const r=await c.run(png(),'B2H-v1');assert.equal(r.stages.calendar,'BLOCKED_DIAGNOSTICS_UNAVAILABLE');assert.equal(r.calendar,null);c.reset();
        const bad=clone(fixtures.extractions.B1);bad.data.source.route='native_pdf';assert.throws(()=>U.validateExtraction(bad,'B1'),/non-OCR/);
    },
    async cancellation() {
        let requested;let release;
        const started=new Promise(resolve=>release=resolve);
        const c=client((url,options)=>{requested=options;release();return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError'))));});
        const running=c.run(png(),'evidence');await started;c.cancel();await running;
        assert(requested.signal.aborted);assert.equal(c.result.busy,false);assert.match(c.result.status,/CANCELLED/);assert.equal(c.result.extraction,null);c.reset();
    },
    async timeout() {
        const original=global.setTimeout;
        global.setTimeout=callback=>{callback();return 0;};
        try {
            const c=client(async(url,options)=>{assert(options.signal.aborted);throw new DOMException('Timeout','AbortError');});
            const r=await c.run(png(),'B1');assert.equal(r.stages.extraction,'FAILED');assert.match(r.diagnostics[0],/timed out/);c.reset();
        } finally {global.setTimeout=original;}
    },
    async object_urls() {
        const created=[],revoked=[];const make=URL.createObjectURL,drop=URL.revokeObjectURL;
        URL.createObjectURL=()=>{const value='blob:synthetic-'+created.length;created.push(value);return value;};URL.revokeObjectURL=value=>revoked.push(value);
        try { const c=client(async(url,options)=>replies(url,options));await c.run(png(),'B2H-v1');await c.run(png(),'B2H-v1');c.reset();assert.deepEqual(revoked,created); }
        finally { URL.createObjectURL=make;URL.revokeObjectURL=drop; }
    },
    async xss_and_preview() {
        for(const id of ['extractionEvidence','documentPreview','eventList'])elements.set(id,new Element('div'));
        const packet=clone(fixtures.extractions.evidence.data);packet.evidence[0].raw_text='<img src=x onerror=alert(1)>';
        const r={pipeline:'evidence',extraction:packet,stages:{},diagnostics:[],mime:'application/pdf',previewUrl:'blob:synthetic',busy:false};
        U.renderEvidence(r);U.renderSource(r);
        const all=elements.get('extractionEvidence').querySelectorAll('*');assert(all.some(e=>e.tagName==='pre'&&e.textContent.includes('<img')));
        assert(!all.some(e=>e.tagName==='img'||e.innerHTML));
        assert.equal(elements.get('documentPreview').children[0].attributes.sandbox,'');assert.equal(elements.get('documentPreview').children[1].rel,'noopener noreferrer');
        r.previewUrl='blob:synthetic2';r.mime='image/png';U.renderSource(r);
        const frame=elements.get('documentPreview').children[0];const image=frame.children[0];image.naturalWidth=1100;image.naturalHeight=160;U.drawBoxes(frame,image,r);
        assert(frame.children.some(e=>e.className==='evidence-box'));
        const events=U.displayedEvents({pipeline:'B2H-v1',extraction:fixtures.extractions['B2H-v1'].data});events[0].subject='<script>alert(1)</script>';
        U.renderCards(events,{policy:null,stages:{}},elements.get('eventList'));
        assert(elements.get('eventList').querySelectorAll('*').some(e=>e.tagName==='strong'&&e.textContent.includes('<script>')));
        assert(!elements.get('eventList').querySelectorAll('*').some(e=>e.tagName==='script'));
    },
    async provenance_validation() {
        const p=clone(fixtures.extractions.B1.data);p.evidence[0].source_document_id='other';assert.throws(()=>U.validatePacket(p),/provenance/);
        const e=clone(fixtures.extractions.B1.data);e.events[0].revision.observations[0].evidence_ids=['unknown'];assert.throws(()=>U.validatePacket(e),/field state/);
        assert.equal(U.diagnostic(Error('C:\\private\\token.txt')),'[LOCAL_PATH_REDACTED]');
    },
    async structured_compatibility() {
        const data=clone(fixtures.structured);const copy=JSON.stringify(data);assert.equal(STAIRIntranet.structuredPacket(data),data.canonical);assert.equal(JSON.stringify(data),copy);
        const calls=[];const old=global.fetch;global.fetch=async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return response({success:true,data});};
        try { await STAIRIntranet.sessionRequest('http://local','/api/intranet/timetable','synthetic-session');await STAIRIntranet.sessionRequest('http://local','/api/intranet/disconnect','synthetic-session');assert.deepEqual(calls[0][1],{token:'synthetic-session'}); }
        finally { global.fetch=old; }
    },
    async app_labels() {
        vm.runInThisContext(fs.readFileSync(path.join(root,'frontend/js/app.js'),'utf8'));
        const event=normaliseIntranetEvent(fixtures.structured.events[0],0);assert.equal(event.status,'review');assert.equal(event.calendarEligibility,'BLOCKED');assert.equal(event.policyState,'NOT ASSESSED');
        const first=JSON.stringify(fixtures.structured.events[0]);normaliseIntranetEvent(fixtures.structured.events[0],0);assert.equal(JSON.stringify(fixtures.structured.events[0]),first);
        state.source='tarumt_intranet_live';state.events=[{...event,selected:true}];approveSelected();assert.equal(state.events[0].status,'review');
        state.source='upload';state.events=[{...event,selected:true}];deleteSelected();assert.equal(state.events.length,1);
        assert.equal(isStructurallyValid(event),true);
    },
    async live() {
        const calls=[];const c=client(async(url,options)=>{calls.push(url);return fetch(url,options);});c.base=process.argv[4];
        const result=await c.run(png(),'B2H-v1');assert(result.extraction,JSON.stringify(result.diagnostics));assert(result.reconstruction,JSON.stringify(result.diagnostics));
        assert.equal(result.stages.calendar,'BLOCKED_READ_ONLY');assert(result.policy,JSON.stringify(result.diagnostics));assert(result.policy.length>0);assert(result.policy.every(d=>d.decision!=='ACCEPT'));
        assert(result.extraction.evidence.some(e=>e.route==='ocr'&&e.bbox?.coordinate_space==='PIXELS'&&e.raw_text.includes('CS101')));
        const record={data_scope:'SYNTHETIC_ONLY',calls:calls.map(url=>new URL(url).pathname),pipeline:result.pipeline,
            source:result.extraction.source,evidence:result.extraction.evidence,extraction_issues:result.extraction.issues,
            baseline_candidate_count:result.extraction.events.length,p1_candidate_count:result.reconstruction.canonical_output.packet.events.length,
            p1_unresolved_count:result.reconstruction.canonical_output.unresolved_candidates.length,p1_digest:result.reconstruction.canonical_digest,
            p1_search_status:result.stages.reconstruction,m5_decisions:result.policy.map(d=>({candidate_id:d.candidate_id,decision:d.decision,reasons:d.reason_codes,calibrated_probability:d.calibrated_probability})),
            calendar_status:result.stages.calendar,preview_digest:result.calendar.preview.preview.preview_digest,diagnostics:result.diagnostics,external_calendar:false};
        c.reset();return record;
    }
};
(async()=>{assert(cases[caseName],caseName);const detail=await cases[caseName]();console.log(JSON.stringify({case:caseName,status:'passed',detail:detail||null}));})().catch(error=>{console.error(error.stack);process.exitCode=1;});
