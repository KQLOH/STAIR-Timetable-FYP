/* Deterministic calendar projection tests; no source datasets or external service. */
const assert=require('node:assert/strict');
const C=require('../frontend/js/sandbox-calendar.js');
let checks=0;
function test(name,run){run();checks++;process.stdout.write('PASS '+name+'\n');}
const body=(title,start,end,zone='Asia/Kuala_Lumpur')=>({summary:title,location:'Invented room',start:{dateTime:start,timeZone:zone},end:{dateTime:end,timeZone:zone}});
const monday=body('TEST101 · Lecture','2026-11-23T09:00:00+08:00','2026-11-23T11:00:00+08:00');
test('valid leap dates and rejection of normalized invalid dates',()=>{
 assert.equal(C.validDate('2024-02-29'),true);assert.equal(C.validDate('2026-02-29'),false);
 assert.equal(C.validDate('2026-02-30'),false);assert.equal(C.validDate('not a date'),false);
});
test('Monday week boundary across years',()=>{
 assert.equal(C.monday('2027-01-03'),'2026-12-28');assert.equal(C.monday('2026-11-23'),'2026-11-23');
});
test('month navigation clamps month-end and handles leap years',()=>{
 assert.equal(C.shiftMonth('2024-01-31',1),'2024-02-29');assert.equal(C.shiftMonth('2026-01-31',1),'2026-02-28');
 assert.equal(C.shiftMonth('2026-12-31',1),'2027-01-31');assert.equal(C.shiftMonth('2026-03-31',-1),'2026-02-28');
});
test('date navigation is independent of DST and host timezone',()=>{
 assert.equal(C.shiftDate('2026-03-28',1),'2026-03-29');assert.equal(C.shiftDate('2026-12-31',1),'2027-01-01');
});
test('month has 42 Monday-first dates including adjacent months',()=>{
 const dates=C.range('2026-11-23','month');assert.equal(dates.length,42);assert.equal(dates[0],'2026-10-26');assert.equal(dates[41],'2026-12-06');
 assert.equal(C.range('2026-11-23','week').length,7);
});
test('calendar display zone changes an occurrence date without changing stored time',()=>{
 const entry=C.normalizeEntry('a',body('TEST', '2026-11-23T00:30:00+08:00','2026-11-23T01:30:00+08:00'),{},'saved',null,'UTC');
 assert.equal(entry.startLocal.date,'2026-11-22');assert.equal(entry.startLocal.time,'16:30');assert.equal(entry.body.start.dateTime,'2026-11-23T00:30:00+08:00');
});
test('overnight course is split over two visible dates',()=>{
 const overnight=C.normalizeEntry('a',body('NIGHT','2026-11-23T23:00:00+08:00','2026-11-24T01:00:00+08:00'));
 const days=C.segments([overnight],['2026-11-23','2026-11-24'],'Asia/Kuala_Lumpur');
 assert.equal(days.get('2026-11-23')[0].startMinute,1380);assert.equal(days.get('2026-11-23')[0].endMinute,1440);
 assert.equal(days.get('2026-11-24')[0].startMinute,0);assert.equal(days.get('2026-11-24')[0].endMinute,60);
});
test('midnight end does not invent an extra occurrence on next day',()=>{
 const event=C.normalizeEntry('a',body('NIGHT','2026-11-23T23:00:00+08:00','2026-11-24T00:00:00+08:00'));
 const days=C.segments([event],['2026-11-23','2026-11-24'],'Asia/Kuala_Lumpur');
 assert.equal(days.get('2026-11-23').length,1);assert.equal(days.get('2026-11-24').length,0);
});
test('overlapping courses use separate lanes, touching courses do not',()=>{
 const layout=C.columns([{startMinute:540,endMinute:660},{startMinute:600,endMinute:720},{startMinute:720,endMinute:780}]);
 assert.equal(layout[0].lanes,2);assert.equal(layout[1].lanes,2);assert.notEqual(layout[0].lane,layout[1].lane);assert.equal(layout[2].lanes,1);
});
test('separate overlap groups each have their own lane count',()=>{
 const layout=C.columns([{startMinute:60,endMinute:180},{startMinute:90,endMinute:120},{startMinute:150,endMinute:240},{startMinute:300,endMinute:360}]);
 assert.equal(layout[0].lanes,2);assert.equal(layout[2].lanes,2);assert.equal(layout[3].lanes,1);
});
test('pending update overlays saved entry without changing saved data',()=>{
 const entries={a:monday},before=JSON.stringify(entries),updated={...monday,location:'Invented new room'};
 const preview={target:'local',expires:2000,approved:false,operations:[{key:'a',body:updated,action:'UPDATE',task_id:'synthetic-task',revision_id:'new'}]};
 const projected=C.project(entries,{},preview,'Asia/Kuala_Lumpur',1000);
 assert.equal(projected.events.length,1);assert.equal(projected.events[0].state,'preview');assert.equal(projected.events[0].before.location,'Invented room');
 assert.equal(JSON.stringify(entries),before);
});
test('approved preview remains not saved',()=>{
 const preview={target:'local',expires:2000,approved:true,operations:[{key:'a',body:monday,action:'CREATE'}]};
 assert.equal(C.project({}, {},preview,'Asia/Kuala_Lumpur',1000).events[0].state,'approved-preview');
});
test('expired preview is excluded without removing saved entries',()=>{
 const preview={target:'local',expires:999,operations:[{key:'b',body:monday,action:'CREATE'}]};
 assert.deepEqual(C.project({a:monday},{},preview,'Asia/Kuala_Lumpur',1000).events.map(e=>e.key),['a']);
});
test('Google previews never appear as local sandbox entries',()=>{
 const preview={target:'google:invented-test',expires:2000,operations:[{key:'b',body:monday,action:'CREATE'}]};
 assert.equal(C.project({}, {},preview,'Asia/Kuala_Lumpur',1000).events.length,0);
});
test('no-op preview preserves saved status',()=>{
 const preview={target:'local',expires:2000,operations:[{key:'a',body:monday,action:'NO_OP'}]};
 assert.equal(C.project({a:monday},{},preview,'Asia/Kuala_Lumpur',1000).events[0].state,'saved');
});
test('legacy saved events remain visible without fabricated source links',()=>{
 const event=C.project({a:monday},{},null,'Asia/Kuala_Lumpur').events[0];assert.deepEqual(event.metadata,{});assert.equal(event.state,'saved');
});
test('malformed events are reported rather than silently altering valid entries',()=>{
 const result=C.project({a:monday,b:{summary:'broken'}},{},null,'Asia/Kuala_Lumpur');assert.equal(result.events.length,1);assert.deepEqual(result.invalid,['b']);
});
test('inverted or offset-free times are not represented as saved courses',()=>{
 assert.throws(()=>C.normalizeEntry('x',body('bad','2026-11-23T11:00:00+08:00','2026-11-23T09:00:00+08:00')));
 assert.throws(()=>C.normalizeEntry('x',body('bad','2026-11-23T09:00:00','2026-11-23T11:00:00')));
});
test('source revision metadata is retained independently of saved body',()=>{
 const event=C.project({a:monday},{a:{revision_id:'saved-version',current_revision_id:'new-version'}},null,'Asia/Kuala_Lumpur').events[0];
 assert.equal(event.metadata.revision_id,'saved-version');assert.equal(event.metadata.current_revision_id,'new-version');assert.equal(event.body,monday);
});
test('saved-only dashboard calendar rejects pending and approved preview overlays',()=>{
 const calendar=Object.create(C.Calendar.prototype);
 Object.assign(calendar,{savedOnly:true,render(){},entries:{a:monday}});
 for(const approved of [false,true]) {
  calendar.setPreview({id:'invented',target:'local',approved,expires:Date.now()/1000+600,
   operations:[{key:'b',body:monday,action:'CREATE'}]});
  assert.equal(calendar.preview,null);
  assert.equal(calendar.expiryTimer,undefined);
  assert.deepEqual(Object.keys(calendar.entries),['a']);
 }
});
process.stdout.write(JSON.stringify({passed:checks,failed:0,purpose:'SYNTHETIC_CALENDAR_RENDERING_NOT_RESEARCH_EVALUATION'})+'\n');
