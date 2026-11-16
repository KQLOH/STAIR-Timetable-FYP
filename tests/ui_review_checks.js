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
const cases={
 counts(){const r=fixture(),views=U.displayedEvents(r);assert.equal(views.length,6);assert.equal(views.filter(v=>v.completeCandidate).length,4);assert.equal(views.filter(v=>U.verified(v.canonical)).length,0);},
 distinct(){const r=fixture(),es=r.reconstruction.canonical_output.packet.events;es.push(es[0]);const views=U.displayedEvents(r);assert.equal(views.length,6);es[1].revision.content={...es[0].revision.content};assert.equal(U.displayedEvents(r).length,6);},
 filters(){const views=U.displayedEvents(fixture());assert.equal(U.filteredEvents(views,'review').length,2);assert.equal(U.filteredEvents(views,'accepted').length,4);assert.equal(U.filteredEvents(views,'all','synthetic lecturer').length,6);assert.equal(U.filteredEvents(views,'all','R204').length,4);assert.equal(U.filteredEvents(views,'all','absent').length,0);},
 sorting(){const views=U.displayedEvents(fixture()).reverse();assert.deepEqual(U.filteredEvents(views).map(v=>v.id),Array.from({length:6},(_,i)=>'synthetic-'+i));assert.equal(U.friendlyTime('13:30'),'1:30 PM');assert.equal(U.friendlyTime(null),'Time not confirmed');assert.equal(U.friendlyDate({}),'Day and date not confirmed');},
 evidence_hidden(){const r=fixture(),container=new Element('div');U.renderCards(U.displayedEvents(r),r,container);assert(!visible(container).includes('s2b-word-'));assert(visible(container).includes('View OCR details'));assert(visible(container).includes('Not yet verified'));assert(container.querySelectorAll().some(e=>e.tagName==='button'&&e.textContent==='Copy full ID'));assert(container.querySelectorAll().filter(e=>e.tagName==='details').every(e=>!e.open));},
 safe_text(){const r=fixture();r.reconstruction.canonical_output.packet.events[0].revision.content.course_code='<img src=x onerror=alert(1)>';const c=new Element('div');U.renderCards(U.displayedEvents(r),r,c);assert(visible(c).includes('<img'));assert(!c.querySelectorAll().some(e=>e.tagName==='img'));assert(!c.querySelectorAll().some(e=>['Approve','Verify','Sync'].includes(e.textContent)));},
 region_binding(){const r=fixture(),event=r.reconstruction.canonical_output.packet.events[0];r.layoutEvidence={pages:[{page:1,regions:[{region_id:'own'},{region_id:'other'}],words:[{region_id:'own',evidence_id:event.revision.observations[0].evidence_ids[0]},{region_id:'other',evidence_id:'unrelated'}]}]};assert.deepEqual(U.eventRegions(event,r).map(x=>x.region.region_id),['own']);const p=new Element('section');nodes.set('sourceInspection',p);U.inspectEvent(event,{...r,layoutEvidence:null},null);assert(visible(p).includes('unavailable'));assert.equal(p.hidden,false);},
 verification(){const r=fixture(),event=r.reconstruction.canonical_output.packet.events[0];assert(!U.verified(event));event.human_verification.status='DISPUTED';assert(!U.verified(event));event.human_verification.status='INDEPENDENTLY_VERIFIED';assert(U.verified(event));}
};
const which=process.argv[2];if(!cases[which])throw Error('Unknown test');cases[which]();console.log(JSON.stringify({case:which,passed:true}));
