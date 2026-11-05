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

function rendered(result,events=[]){const c=new Element('div');U.renderCards(events,result,c);return {c,text:visible(c)};}
let r=fixture();r.reconstruction.canonical_output.packet.events=[];
let {c,text}=rendered(r);assert(text.includes('No classes were detected in this image.'));assert(!text.includes('Clear your search'));assert(c.querySelectorAll().some(x=>x.textContent==='Retry'));
r.busy=true;assert(rendered(r).text.includes('Reading your timetable'));r.busy=false;
r.stages.extraction='FAILED';assert(rendered(r).text.includes('couldn’t read'));delete r.stages.extraction;
r.stages.reconstruction='FAILED';assert(rendered(r).text.includes('couldn’t read'));
r=fixture();assert(rendered(r).text.includes('Clear your search'));
const views=U.displayedEvents(r);assert(rendered(r,views).text.includes('Needs review'));assert(!rendered(r,views).text.includes('No classes were detected'));
console.log('RT3 empty state distinctions passed');
