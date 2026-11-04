const assert=require('node:assert/strict');
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.listeners={};this.value='';this.checked=false;this.textContent='';}
 append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this.listeners[k]=f;}
}
const elements=new Map(['reviewPublicCandidate','reviewCandidateChoices','inspectCanonical'].map(id=>[id,new Element(id==='reviewPublicCandidate'?'select':'div')]));
global.document={getElementById:id=>elements.get(id),createElement:tag=>new Element(tag)};
require('../frontend/js/upload.js');
require('../frontend/js/review.js');
const events=[8,10].map((hour,index)=>({candidate_id:'invented-'+index,revision:{revision_id:'rev-'+index,version:index+1,content:{course_code:'QA1701',session_type:'Lecture',date:'2033-02-07',start_time:hour+':00',end_time:(hour+2)+':00',location:'HX C'},observations:[]}}));
const controller=new STAIRReview.Controller({base:'http://synthetic.invalid',getSource:()=>({packet:{source:{source_document_id:'synthetic',content_sha256:'a'.repeat(64)},events}}),openDialog:()=>{},closeDialog:()=>{}});
controller.updateSource();const rows=elements.get('reviewCandidateChoices').children;
assert.equal(rows.length,2);assert(rows.every(r=>r.tagName==='label'&&r.children[0].type==='radio'));
assert(rows.every(r=>r.children[1].children.length===3));
assert(rows[1].children[1].children[2].textContent.includes('Revision 2'));
const radio=rows[1].children[0];radio.checked=true;radio.listeners.change();
assert.equal(elements.get('reviewPublicCandidate').value,'invented-1');
let selected;controller.renderEvent=event=>selected=event;controller.openDialog=()=>{};controller.inspectPublic();
assert.equal(selected.candidate_id,'invented-1');assert.equal(controller.transport,null);
console.log('Separated accessible candidate selection passed');

const event={revision:{observations:[{field_name:'course_code',evidence_ids:['own-code']},{field_name:'location',evidence_ids:['own-venue','other-spacing-witness']}]}};
const result={layoutEvidence:{pages:[{regions:[{region_id:'own'},{region_id:'other'}],words:[{evidence_id:'own-code',region_id:'own'},{evidence_id:'own-venue',region_id:'own'},{evidence_id:'other-spacing-witness',region_id:'other'}]}]}};
assert.deepEqual(STAIRUpload.eventRegions(event,result).map(r=>r.region.region_id),['own']);
console.log('Cross-block normalization evidence does not change source ownership');
