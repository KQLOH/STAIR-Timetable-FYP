/* M5.2 synthetic source classification and optional live client verification. */
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../frontend/js/upload.js'),'utf8'));
const U=global.STAIRUpload;
const source={source_document_id:'synthetic',content_sha256:'a'.repeat(64)};
for (const [route,mime,label] of [['ocr','image/png','OCR image upload'],['ocr','image/jpeg','OCR image upload'],
    ['ocr','application/pdf','PDF OCR extraction'],['native_pdf','application/pdf','Native PDF extraction'],
    ['tarumt_html','text/html','TAR UMT structured source'],['ocr','text/html','Unknown source']])
    assert.equal(U.sourceLabel({...source,route,media_type:mime,label:'TAR UMT structured source'}),label);
assert.equal(U.sourceLabel({label:'TAR UMT structured source'}),'Unknown source');
assert.equal(U.sourceLabel(null,{sample:true}),'Synthetic demonstration sample');
assert.equal(U.sourceLabel(null),'Unknown source');
assert.equal(U.originalEvidenceId({reconstruction:{execution_metadata:{capture_aliases:{original:'canonical'}}}},'canonical'),'original');
assert.equal(U.originalEvidenceId(null,'unknown'),'unknown');
// Exercise app dispatch: earlier tests rendered cards directly and missed this boundary.
const controls=new Map([['eventList',{}],['approveSelected',{}],['deleteSelected',{}],['selectAll',{}]]);
global.document={getElementById:id=>controls.get(id)||null,addEventListener:()=>{},querySelectorAll:()=>[]};
global.window=global;global.addEventListener=()=>{};
let dispatched=false;
global.STAIRUpload={...U,renderCards:()=>{dispatched=true;}};
vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../frontend/js/app.js'),'utf8'));
vm.runInThisContext('state.source="upload"; state.upload={}; renderReview();');
assert(dispatched);assert.equal(controls.get('approveSelected').disabled,true);
assert.equal(controls.get('deleteSelected').disabled,true);
global.STAIRUpload=U;
(async()=>{
    if (!process.argv[2]) {console.log(JSON.stringify({status:'passed',source_labels:8}));return;}
    const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));const calls=[];
    const controller=new U.Controller({base:process.argv[3],fetcher:async(url,options)=>{calls.push({path:new URL(url).pathname,body:typeof options.body==='string'?JSON.parse(options.body):null});return fetch(url,options);}});
    const result=await controller.run(new File([Buffer.from(fixture.png,'base64')],'synthetic-layout.png',{type:'image/png'}),'evidence',{layout:true});
    assert(result.reconstruction,JSON.stringify(result.diagnostics));const c=result.reconstruction.canonical_output;
    assert.equal(c.profile_id,'ocr-layout-m52-v1');assert.equal(c.method_id,'P1-layout-m52-v1');
    assert.equal(calls[1].body.profile,'ocr-layout-m52-v1');assert(calls.some(x=>x.path==='/api/layout/assess'));
    assert(result.policy?.every(d=>d.decision==='ABSTAIN'));assert.equal(result.stages.calendar,'BLOCKED_READ_ONLY');
    const views=U.displayedEvents(result);assert(views.length>0);assert(views.every(v=>v.source==='OCR image upload'));
    assert(c.packet.events.some(e=>e.revision.content.course_code));
    const detail={status:'passed',scope:'REAL_SYNTHETIC_OCR_ONLY',source_label:views[0].source,profile:c.profile_id,
        extraction:result.extraction,reconstruction:result.reconstruction,policy:result.policy,calendar:result.calendar,diagnostics:result.diagnostics,
        paths:calls.map(x=>x.path),external_calendar:false};controller.reset();console.log(JSON.stringify(detail));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
