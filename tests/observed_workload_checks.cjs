/* Invented packets only; no review identity or completeness grants. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node{constructor(){this.children=[];this.textContent='';this.value='';}append(...nodes){this.children.push(...nodes)}replaceChildren(){this.children=[];this.textContent='';}}
const ids=Object.fromEntries(['analysisResult','analysisStart','analysisEnd','analysisZone','analysisScope','analysisCohort'].map(id=>[id,new Node()]));
global.document={getElementById:id=>ids[id],createElement:()=>new Node()};
vm.runInThisContext(fs.readFileSync('frontend/js/analysis.js','utf8'));
const text=n=>n.textContent+' '+n.children.map(text).join(' ');
const result={version:'fr7-observed-v1',trusted_complete:false,calendar_authorized:false,source_issues:0,student_facts:{class_count:2,printed_class_hours:3,hours_by_day:{Monday:3},busiest_observed_days:['Monday'],lightest_observed_active_days:['Monday'],excluded_unresolved_candidates:[],possible_duplicates:[],possible_overlaps:[],undated_class_count:2,human_corrected_class_count:0,occupied_local_slots:[{day:'Monday',start:'09:00',end:'10:00',course:'SYN101'}],detected_date_range:null,source_timezone:null}};
let calls=[],source={canonical:{source:{source_document_id:'synthetic-source'},events:[]}};
global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>result}};
const c=new STAIRAnalysis.Controller({base:'http://synthetic',studentMode:true,getSource:()=>source});
(async()=>{
 const test=process.argv[2];
 if(test==='unknown_scope'){await c.run();assert.equal(calls.length,1);const body=JSON.parse(calls[0].options.body);assert.deepEqual(Object.keys(body),['packet']);assert.equal(calls[0].options.credentials,'omit');assert(calls[0].url.endsWith('/observed'));assert(text(ids.analysisResult).includes('Your observed workload'));assert(!text(ids.analysisResult).includes('Reconnect'));}
 if(test==='prefill_without_invention'){result.student_facts.detected_date_range={start:'2033-02-07',end:'2033-02-08'};result.student_facts.source_timezone='Etc/UTC';await c.run();assert.equal(ids.analysisStart.value,'2033-02-07');assert.equal(ids.analysisZone.value,'Etc/UTC');ids.analysisStart.value='2033-02-01';await c.run();assert.equal(ids.analysisStart.value,'2033-02-01');}
 if(test==='invalid_dates_keep_timetable'){ids.analysisStart.value='2033-03-01';ids.analysisEnd.value='2033-02-01';const before=source.canonical;await c.run();assert.equal(calls.length,0);assert.equal(source.canonical,before);assert(text(ids.analysisResult).includes('end date'));}
 if(test==='current_saved_revision'){source={canonical:{source:{source_document_id:'synthetic'},events:[{candidate_id:'SYN-CURRENT',revision:{version:2,content:{location:'SYN-UPDATED'}}}]}};await c.run();assert.equal(JSON.parse(calls[0].options.body).packet.events[0].revision.content.location,'SYN-UPDATED');assert(!JSON.parse(calls[0].options.body).attestation_id);}
 if(test==='late_reply_ignored'){let resolve;global.fetch=()=>new Promise(r=>resolve=r);const work=c.run();c.invalidate('new-source');resolve({ok:true,json:async()=>result});await work;assert(!text(ids.analysisResult).includes('Your observed workload'));}
 if(test==='research_optional'){const html=fs.readFileSync('frontend/index.html','utf8');const begin=html.indexOf('id="analysisResearch"');assert(begin>html.indexOf('id="analysisResult"'));assert(!html.slice(begin,html.indexOf('>',begin)).includes('open'));assert(begin<html.indexOf('id="scheduleStart"'));await c.run();assert(text(ids.analysisResult).includes('not confirmed free time'));assert(!result.trusted_complete);}
 console.log('Observed student frontend passed: '+test);
})().catch(error=>{console.error(error);process.exitCode=1});
