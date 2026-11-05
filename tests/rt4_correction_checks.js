/* Invented UI records only; no private source images or extracted content. */
const assert=require('node:assert/strict');
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.attributes={};this.listeners={};this.textContent='';this.className='';this.open=false;this.hidden=false;this.classList={toggle(){}};}
 append(...items){for(const x of items){x.parentElement=this;this.children.push(x);}}
 replaceChildren(...items){this.children=[];this.append(...items);}
 addEventListener(n,f){this.listeners[n]=f;}
 setAttribute(n,v){this.attributes[n]=v;}
 querySelectorAll(){return this.children.flatMap(x=>[x,...x.querySelectorAll()]);}
 focus(){this.focused=true;}scrollIntoView(){}
}
const nodes=new Map();
global.document={createElement:t=>new Element(t),getElementById:id=>nodes.get(id),querySelector:()=>null,querySelectorAll:()=>[]};
require('../frontend/js/upload.js');const U=STAIRUpload;
function fixture(){
 const events=Array.from({length:6},(_,i)=>({candidate_id:'synthetic-'+i,revision:{revision_id:'revision-'+i,method_id:'synthetic-test',
 content:{course_code:i<2?'AB1201':'CD2302',session_type:i<2?'Lecture':'Practical',day_of_week:i<2?1:3,date:i<2?'2033-02-07':'2033-02-09',
 start_time:`${String(8+i).padStart(2,'0')}:00`,end_time:`${String(9+i).padStart(2,'0')}:00`,location:i<2?null:'R204',section:null,lecturer:'Synthetic Lecturer'},
 observations:[{field_name:'course_code',state:'PRESENT',evidence_ids:['s2b-word-'+i+'x'.repeat(80)]}]},human_verification:{status:'NOT_VERIFIED'},calendar:{approval_status:'NOT_APPROVED',sync_status:'NOT_SUBMITTED'}}));
 return {pipeline:'evidence',stages:{},extraction:{events:[],evidence:[],source:{}},reconstruction:{canonical_output:{profile_id:'synthetic-ui-only',packet:{events,evidence:[]},unresolved_candidates:[]}},policy:[]};
}
function visible(node){return node.hidden?'':node.textContent+' '+(node.tagName==='details'&&!node.open?node.children.filter(x=>x.tagName==='summary'):node.children).map(visible).join(' ');}

const r=fixture(),event=r.reconstruction.canonical_output.packet.events[0];
event.revision.observations[0].evidence_ids.push('format-witness');event.revision.observations[0].transformations=['rt4-owner-region:own'];
r.layoutEvidence={pages:[{page:1,regions:[{region_id:'own'},{region_id:'witness'}],words:[{region_id:'own',evidence_id:event.revision.observations[0].evidence_ids[0]},{region_id:'witness',evidence_id:'format-witness'}]}]};
const correction={field:'location',original:'X0O1J',suggested:'X001J',status:'AMBIGUOUS',rule:'insufficient-or-competing-source-support',candidates:[]};
r.reconstruction.canonical_output.layout={regions:[{region_id:'own',ocr_corrections:[correction]}]};
assert.deepEqual(U.eventRegions(event,r).map(x=>x.region.region_id),['own']);
let c=new Element('div');U.renderCards(U.displayedEvents(r),r,c);
assert(visible(c).includes('Possible OCR error — please check'));assert(!visible(c).includes('Original OCR:'));assert(!visible(c).includes('format-witness'));
c.querySelectorAll().filter(x=>x.tagName==='details').forEach(x=>x.open=true);
assert(visible(c).includes('Original OCR: X0O1J'));assert(visible(c).includes('Suggested: X001J'));assert(visible(c).includes('Reason:'));
correction.status='SUPPORTED';correction.rule='independent-format-numeric-position';event.revision.content.location='X001J';
c=new Element('div');U.renderCards(U.displayedEvents(r),r,c);assert(visible(c).includes('Venue: X001J'));assert(!visible(c).includes('Possible OCR error'));
assert(!U.verified(event));assert.equal(event.calendar.approval_status,'NOT_APPROVED');
event.revision.observations[0].transformations=[];event.revision.observations[0].evidence_ids.push('own-derived');
r.reconstruction.canonical_output.packet.evidence.push({evidence_id:'own-derived',locator:'region:own'});
assert.deepEqual(U.eventRegions(event,r).map(x=>x.region.region_id),['own']);
console.log('RT4 readable correction provenance and own-region binding passed');
