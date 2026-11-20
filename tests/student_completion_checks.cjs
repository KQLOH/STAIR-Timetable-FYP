const assert=require('node:assert/strict');
require('../frontend/js/review.js');require('../frontend/js/student-review.js');require('../frontend/js/calendar-checks.js');
const task={etag:3,current_event:{revision:{revision_id:'synthetic-revision'}}};
const span={evidence_id:'original:synthetic',page:1,raw_text:'ZOO8'};
assert.throws(()=>STAIRStudentReview.proposal(task,'location','Z008','Checked invented source',span,false),/Confirm/);
assert.throws(()=>STAIRStudentReview.proposal(task,'location','Z008','',span,true),/reason/);
assert.throws(()=>STAIRStudentReview.proposal(task,'location','Z008','Checked',{...span,evidence_id:'human:previous'},true),/original/);
assert.throws(()=>STAIRStudentReview.proposal(task,'recurrence','weekly','Checked',span,true),/corrected/);
const body=STAIRStudentReview.proposal(task,'location','Z008','Checked invented source',span,true);
assert.equal(body.expected_etag,3);assert.equal(body.expected_revision_id,'synthetic-revision');
assert.equal(body.fields[0].evidence_ids[0],body.human_evidence[0].evidence_id);
assert.deepEqual(body.human_evidence[0].source_evidence_ids,['original:synthetic']);assert.equal(body.fields[0].value,'Z008');
const event=(id,start,end,extra={})=>({id,subject:'Invented Science',date:'2033-02-07',start,end,type:'Lecture',venue:'X1',...extra});
let checks=STAIRCalendarChecks.inspect([event('a','09:00','10:00'),event('b','10:00','11:00')]);assert.equal(checks.conflicts,0);
checks=STAIRCalendarChecks.inspect([event('a','09:00','11:00'),event('b','10:00','12:00')]);assert.equal(checks.conflicts,1);assert(checks.warnings.get('a').includes('Overlaps another class'));
checks=STAIRCalendarChecks.inspect([event('a','09:00','11:00'),event('b','09:00','11:00')]);assert.equal(checks.duplicates,1);assert.equal(checks.conflicts,0);
checks=STAIRCalendarChecks.inspect([event('a','29:00','31:00'),event('b','12:00','11:00',{date:'Unknown'})]);assert.equal(checks.conflicts,0);assert(checks.warnings.get('a').includes('Check start and end times'));assert(checks.warnings.get('b').includes('Date not confirmed'));
assert(checks.warnings.get('b').includes('Repeat schedule not confirmed'));
console.log('PASS explicit human-source payload, stale binding, calendar conflict/duplicate/uncertainty checks');

checks=STAIRCalendarChecks.inspect([event('night','23:00','01:00',{canonical:{revision:{content:{end_day_offset:1}}}}),event('morning','00:30','01:30',{date:'2033-02-08'})]);assert.equal(checks.conflicts,1);assert(!checks.warnings.get('night').includes('Check start and end times'));
checks=STAIRCalendarChecks.inspect([event('invalid','09:00','10:00',{date:'2033-02-30'})]);assert(checks.warnings.get('invalid').includes('Date not confirmed'));
checks=STAIRCalendarChecks.inspect([event('midnight','23:00','24:00')]);assert(!checks.warnings.get('midnight').includes('Check start and end times'));

require('../frontend/js/upload.js');
const cellSpan={evidence_id:'synthetic-cell',page:1,locator:'cell:row:1;columns:1:1',bbox:{x0:10,y0:20,x1:200,y1:100,coordinate_space:'PIXELS'}};
const ruledEvent={revision:{observations:[{field_name:'course_code',evidence_ids:['synthetic-cell']}]}};
const ruledResult={layoutEvidence:{profile:'ocr-ruled-grid-s2a-v1',pages:[{page:1,width:300,height:200,words:[]}]},reconstruction:{canonical_output:{packet:{evidence:[cellSpan]}}}};
const regions=STAIRUpload.eventRegions(ruledEvent,ruledResult);assert.equal(regions.length,1);assert.equal(regions[0].region.source_cell,true);assert.deepEqual(regions[0].region.bbox,cellSpan.bbox);
const absent={...ruledResult,reconstruction:{canonical_output:{packet:{evidence:[]}}}};assert.deepEqual(STAIRUpload.eventRegions(ruledEvent,absent),[]);

(async()=>{
const previous={busy:false,status:'Finished',stages:{calendar:'BLOCKED_READ_ONLY'},extraction:{source:{source_document_id:'synthetic-kept'}}};
const client=new STAIRUpload.Controller({fetcher:async()=>({ok:false,status:422,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({detail:'Unusable image'})})});
client.result=previous;client.objectUrl='blob:synthetic-original';
const failed=await client.run(new File([new Uint8Array([137,80,78,71,13,10,26,10])],'damaged.png',{type:'image/png'}),'evidence');
assert.equal(failed,previous);assert.equal(client.result,previous);assert.equal(client.objectUrl,'blob:synthetic-original');assert(previous.replacementError.includes('Unusable image'));
})().catch(e=>{console.error(e);process.exit(1)});

assert(STAIRUpload.friendlyFailure(new Error('AUTO_MIXED_LAYOUTS_REQUIRE_SEPARATE_UPLOADS')).includes('separately'));
assert(STAIRUpload.friendlyFailure(new Error('AUTO_PAGE_CAP')).includes('four'));
const fields=new Map();global.document={getElementById:id=>{if(!fields.has(id))fields.set(id,{value:'',checked:false,disabled:false,replaceChildren(){},textContent:''});return fields.get(id);}};
const oldSource={source_document_id:'synthetic-old',content_sha256:'a'.repeat(64)};
const sourceReview={capture:oldSource,session:{},task:{state:'IN_PROGRESS'},permitted:()=>true,canCorrect:()=>true};
const editor=new STAIRStudentReview.Editor({review:sourceReview,getSource:()=>({packet:{source:{...oldSource,content_sha256:'b'.repeat(64)}}}),getUpload:()=>null});
editor.controls();assert.equal(fields.get('studentSave').disabled,true);assert.equal(fields.get('studentRecheck').disabled,true);

const codeCase=STAIRStudentReview.proposal(task,'course_code','xy 1234','Checked invented heading',span,true);assert.equal(codeCase.fields[0].value,'XY1234');assert.equal(codeCase.fields[0].raw_value,'xy 1234');assert.equal(codeCase.human_evidence[0].raw_text,'xy 1234');
const typeCase=STAIRStudentReview.proposal(task,'session_type','lec','Checked invented type',span,true);assert.equal(typeCase.fields[0].value,'Lecture');assert.equal(typeCase.fields[0].raw_value,'lec');
const noGuess=STAIRStudentReview.proposal(task,'location','ZOO8','Checked invented room',span,true);assert.equal(noGuess.fields[0].value,'ZOO8');

assert.equal(STAIRUpload.friendlyTime('24:00'),'12:00 AM (next day)');

(async()=>{
let resumed=0,populated=0;
const c={capture:oldSource,session:{},task:{state:'REVALIDATED'},busy:false,canCorrect:()=>true,permitted:()=>true,async transition(action){assert.equal(action,'resume');resumed++;this.task.state='IN_PROGRESS';}};
const same=new STAIRStudentReview.Editor({review:c,getSource:()=>({packet:{source:oldSource}}),getUpload:()=>null});same.initialValue=document.getElementById('studentValue').value;same.populate=()=>populated++;document.getElementById('studentReason').value='';document.getElementById('studentConfirmed').checked=false;
await same.changeField();assert.equal(resumed,1);assert.equal(populated,1);assert.equal(fields.get('studentSave').disabled,false);
})().catch(e=>{console.error(e);process.exit(1)});
