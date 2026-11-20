/* Explicit source readings only. Unknown values never become defaults. */
(function(g){'use strict';
const node=(tag,text)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;return e;};
function input(parent,label,type='text',value=''){const wrapper=node('label',label),e=node('input');e.type=type;e.value=value||'';wrapper.append(e);parent.append(wrapper);return e;}
function area(parent,label,value=''){const wrapper=node('label',label),e=node('textarea');e.value=value;wrapper.append(e);parent.append(wrapper);return e;}
function weeks(value){if(!value.trim())return null;if(value.trim().toLowerCase()==='none')return [];const result=[];for(const part of value.split(',')){const match=/^\s*(\d+)(?:\s*-\s*(\d+))?\s*$/.exec(part);if(!match)throw Error('Use week numbers such as 1, 2, 4-6; leave unknown blank.');const a=Number(match[1]),b=Number(match[2]||a);if(a<1||b<a||b-a>99)throw Error('Check the week range.');for(let n=a;n<=b;n++)result.push(n);}return [...new Set(result)];}
function reading(parent,title,packet,saved){
 const box=node('details');box.append(node('summary',title));parent.append(box);
 const page=input(box,'Original page','number',saved?.page||1);page.min=1;
 const refs=node('select');refs.multiple=true;refs.setAttribute('aria-label','Source passages for '+title);
 for(const span of packet.evidence){const o=node('option',`Page ${span.page||1}: ${span.raw_text.slice(0,140)}`);o.value=span.evidence_id;o.selected=saved?.evidence_ids?.includes(span.evidence_id)||false;refs.append(o);}box.append(refs);
 const raw=area(box,'What the original says (include the relevant dates, weeks or timezone)',saved?.raw_reading||'');
 const note=area(box,'What this passage establishes',saved?.note||'');const confirmed=input(box,'I personally inspected this passage in the original','checkbox');confirmed.checked=!!saved?.source_confirmed;
 return ()=>({page:Number(page.value),evidence_ids:[...refs.selectedOptions].map(o=>o.value),raw_reading:raw.value,note:note.value,source_confirmed:confirmed.checked});
}
function render(container,controller){
 const c=controller.check,packet=controller.getSource().packet,old=c.temporal?.submission;
 const details=node('details');details.append(node('summary','Confirm timetable dates, weeks and class group'));container.append(details);
 details.append(node('p','Enter only what the original confirms. Leaving a field blank keeps it unknown or retains its existing source value. Saving changes invalidates the previous whole-source check and independent verification. These assertions are separate from automatic extraction and saved class corrections.'));
 const start=input(details,'Analysis starts','date',c.scope.start),end=input(details,'Analysis ends','date',c.scope.end);
 const timezone=input(details,'Source timezone (leave blank if unknown)','text',old?.timezone),cohort=input(details,'Class group supported by the source','text',old?.cohort_id);
 const termStart=input(details,'Source semester begins (optional)','date',old?.term?.start),termEnd=input(details,'Source semester ends (optional)','date',old?.term?.end);
 const mainReading=reading(details,'Source reading for dates, timezone and class group',packet,old?.reading);
 const mapping=node('div');details.append(node('h4','Teaching-week dates'),node('p','Add only explicitly checked week mappings. No semester start or missing week dates are inferred.'),mapping);const rows=[];
 function addWeek(saved){const row=node('fieldset');mapping.append(row);const number=input(row,'Teaching week','number',saved?.week),a=input(row,'Week begins','date',saved?.start),b=input(row,'Week ends','date',saved?.end),read=reading(row,'Source reading for this week',packet,saved?.reading);const remove=node('button','Remove week');remove.type='button';remove.onclick=()=>{row.remove();rows.splice(rows.indexOf(readRow),1);if(!controller.restoringDraft)controller.edited?.();};row.append(remove);const readRow=()=>({week:Number(number.value),start:a.value,end:b.value,reading:read()});rows.push(readRow);}
 for(const week of old?.weeks||[])addWeek(week);const add=node('button','Add teaching week');add.type='button';add.className='button secondary';add.onclick=()=>{addWeek();controller.edited?.();};details.append(add);
 const events=[];
 for(const event of c.events){
  const content=event.revision.content,saved=old?.events?.find(x=>x.candidate_id===event.candidate_id),box=node('details');box.append(node('summary',(content.course_code||content.course_name||'Class')+' — recurrence and exceptions'));details.append(box);
  const rule=node('select');rule.setAttribute('aria-label','Source recurrence for '+(content.course_code||'class'));
  for(const [value,label] of [['','Keep existing / unknown'],['ONCE','One dated session — only if source confirms'],['WEEKLY','Every week within the confirmed source term']]){const o=node('option',label);o.value=value;o.selected=saved?.recurrence?.frequency===value;rule.append(o);}box.append(rule);
  const teaching=input(box,'Teaching weeks (e.g. 1, 3-5; unknown = blank)','text',saved?.teaching_weeks?.join(',')??''),excluded=input(box,'Excluded weeks (use none only if explicitly shown)','text',saved?.excluded_weeks?.join(',')??'');
  const holiday=input(box,'Dates this class does not meet (YYYY-MM-DD, comma separated; source confirmation required)','text',saved?.recurrence?.excluded_dates?.join(',')||''),section=input(box,'Section, if shown','text',saved?.section);
  const read=reading(box,'Source reading for this class context',packet,saved?.reading);
  events.push(()=>{if(!rule.value&&!teaching.value&&!excluded.value&&!holiday.value&&!section.value)return null;const recurrence=rule.value?{...(content.recurrence||{}),frequency:rule.value,source_rule:content.recurrence?.source_rule||(rule.value==='WEEKLY'?'weekly':'once'),...(rule.value==='WEEKLY'?{interval:1,term_start:termStart.value||null,term_end:termEnd.value||null}:{}),...(holiday.value.trim()?{excluded_dates:holiday.value.split(',').map(v=>v.trim())}:{})}:null;if(holiday.value&&!recurrence)throw Error('A source-backed recurrence rule is needed before adding class exceptions.');return {candidate_id:event.candidate_id,recurrence,teaching_weeks:weeks(teaching.value),excluded_weeks:weeks(excluded.value),section:section.value.trim()||null,reading:read()};});
 }
 controller.captureExtraDraft=()=>({temporal_weeks:rows.length,controls:[...details.querySelectorAll('input,select,textarea')].map(e=>({value:e.value,checked:!!e.checked,selected:e.tagName==='SELECT'?[...e.selectedOptions].map(o=>o.value):[]}))});
 controller.restoreExtraDraft=d=>{controller.restoringDraft=true;while(rows.length<d.temporal_weeks)addWeek();while(rows.length>d.temporal_weeks)mapping.lastElementChild.querySelector('button').click();const controls=[...details.querySelectorAll('input,select,textarea')];d.controls.forEach((v,i)=>{const e=controls[i];if(!e)return;e.value=v.value;if(e.type==='checkbox')e.checked=v.checked;if(e.tagName==='SELECT')for(const option of e.options)option.selected=v.selected.includes(option.value);});controller.restoringDraft=false;};
 const save=node('button','Save source-supported context');save.type='button';save.className='button primary';save.onclick=()=>controller.action(async()=>{
  const body={expected_version:c.version,scope:{...c.scope,start:start.value,end:end.value,timezone:timezone.value.trim()||c.scope.timezone,cohort_id:cohort.value.trim()||c.scope.cohort_id},timezone:timezone.value.trim()||null,cohort_id:cohort.value.trim()||null,term:termStart.value||termEnd.value?{start:termStart.value,end:termEnd.value}:null,weeks:rows.map(read=>read()),events:events.map(read=>read()).filter(Boolean),reading:mainReading()};
  const pending=controller.dirty?controller.captureForm():null;controller.check=await controller.request('POST',c.check_id+'/temporal',body);controller.render();if(pending)controller.applyDraft({...pending,source_confirmed:false,scope_confirmed:false,extra:null});controller.status('Source context saved separately from extraction. Recheck and save the whole-source inventory before independent verification. Unknown or unsupported details remain blocked.');
 });details.append(save);
}
g.STAIRTemporalScope={render,weeks};
})(typeof window!=='undefined'?window:globalThis);
