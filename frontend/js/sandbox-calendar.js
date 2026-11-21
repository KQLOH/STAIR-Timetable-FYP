/* Saved account sandbox calendar. Rendering never creates approval or writes events. */
(function (global) {
    'use strict';
    const DAY=86400000, PX_PER_MINUTE=0.9;
    const zonedFormatters=new Map();
    const weekdays=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    function validDate(key) {
        return typeof key==='string' && /^\d{4}-\d{2}-\d{2}$/.test(key) &&
            Number.isFinite(Date.parse(key+'T12:00:00Z')) && new Date(key+'T12:00:00Z').toISOString().slice(0,10)===key;
    }
    function shiftDate(key, days) {
        if(!validDate(key))return null;
        const date=new Date(Date.parse(key+'T12:00:00Z')+days*DAY), result=date.toISOString().slice(0,10);
        return validDate(result)?result:null;
    }
    function monday(key) {
        if(!validDate(key))return null;
        return shiftDate(key,-((new Date(key+'T12:00:00Z').getUTCDay()+6)%7));
    }
    function shiftMonth(key, amount) {
        if(!validDate(key))return null;
        const date=new Date(key+'T12:00:00Z'), day=date.getUTCDate();
        date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()+amount);
        const next=new Date(date);next.setUTCMonth(next.getUTCMonth()+1);next.setUTCDate(0);
        date.setUTCDate(Math.min(day,next.getUTCDate()));
        const result=date.toISOString().slice(0,10);return validDate(result)?result:null;
    }
    function range(anchor, view) {
        if(!validDate(anchor))throw Error('Choose a valid calendar date.');
        const start=view==='month'?monday(anchor.slice(0,7)+'-01'):monday(anchor);
        return Array.from({length:view==='month'?42:7},(_,i)=>shiftDate(start,i));
    }
    function zonedParts(value, zone) {
        const date=new Date(value);
        if(!Number.isFinite(date.getTime()))throw Error('Invalid event time.');
        if(!zonedFormatters.has(zone))zonedFormatters.set(zone,new Intl.DateTimeFormat('en-GB',{timeZone:zone,
            year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}));
        const parts=Object.fromEntries(zonedFormatters.get(zone).formatToParts(date).map(p=>[p.type,p.value]));
        return {date:parts.year.padStart(4,'0')+'-'+parts.month+'-'+parts.day,
                minute:Number(parts.hour)*60+Number(parts.minute),time:parts.hour+':'+parts.minute};
    }
    function today(zone, now=Date.now()) {return zonedParts(now,zone).date;}
    function normalizeEntry(key, body, metadata={}, state='saved', before=null, zone='Asia/Kuala_Lumpur') {
        if(!body || typeof body.summary!=='string' || typeof body.start?.dateTime!=='string' ||
            typeof body.end?.dateTime!=='string' || !/(Z|[+-]\d\d:\d\d)$/.test(body.start.dateTime) ||
            !/(Z|[+-]\d\d:\d\d)$/.test(body.end.dateTime))throw Error('Unsupported saved event.');
        const start=Date.parse(body.start.dateTime),end=Date.parse(body.end.dateTime);
        if(!Number.isFinite(start) || !Number.isFinite(end) || end<=start || end-start>DAY)throw Error('Unsupported saved event duration.');
        return {key,body,metadata,state,before,start,end,startLocal:zonedParts(start,zone),endLocal:zonedParts(end,zone)};
    }
    function project(entries, metadata, preview, zone, now=Date.now()/1000) {
        const rows=new Map(), invalid=[];
        for(const [key,body] of Object.entries(entries || {})) {
            try {rows.set(key,normalizeEntry(key,body,metadata?.[key] || {},'saved',null,zone));}
            catch {invalid.push(key);}
        }
        if(preview?.target==='local' && preview.expires>now && !preview.outcome) {
            for(const op of preview.operations || []) {
                if(!['CREATE','UPDATE'].includes(op.action))continue;
                try {
                    rows.set(op.key,normalizeEntry(op.key,op.body,{task_id:op.task_id,revision_id:op.revision_id},
                        preview.approved?'approved-preview':'preview',entries?.[op.key] || null,zone));
                }catch {invalid.push(op.key);}
            }
        }
        return {events:[...rows.values()].sort((a,b)=>a.start-b.start || a.key.localeCompare(b.key)),invalid};
    }
    function segments(events, dates, zone) {
        const days=new Map(dates.map(date=>[date,[]]));
        for(const event of events) {
            const first=zonedParts(event.start,zone),last=zonedParts(event.end-1,zone);
            for(const date of dates) {
                if(date<first.date || date>last.date)continue;
                const end=zonedParts(event.end,zone);
                const startMinute=date===first.date?first.minute:0;
                const endMinute=date===end.date?end.minute:1440;
                days.get(date).push({event,date,startMinute,endMinute:Math.max(startMinute+1,endMinute)});
            }
        }
        return days;
    }
    function columns(segments) {
        const sorted=[...segments].sort((a,b)=>a.startMinute-b.startMinute || a.endMinute-b.endMinute);
        let group=[],limit=-1;const output=[];
        function flush() {
            const ends=[];
            for(const item of group) {
                let lane=ends.findIndex(end=>end<=item.startMinute);if(lane<0)lane=ends.length;
                ends[lane]=item.endMinute;item.lane=lane;
            }
            output.push(...group.map(item=>({...item,lanes:ends.length})));group=[];
        }
        for(const item of sorted) {
            if(group.length && item.startMinute>=limit){flush();limit=-1;}
            group.push({...item});limit=Math.max(limit,item.endMinute);
        }
        flush();return output;
    }
    const dateText=key=>new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeZone:'UTC'}).format(new Date(key+'T12:00:00Z'));
    const shortTime=(value,zone)=>new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:zone}).format(new Date(value));
    function colour(title) {let hash=0;for(const ch of title.split('·')[0])hash=(hash*31+ch.charCodeAt(0))>>>0;return hash%6;}
    function node(tag,text,cls) {
        const result=document.createElement(tag);if(text!=null)result.textContent=text;if(cls)result.className=cls;return result;
    }
    function button(text,callback,cls='button secondary') {
        const result=node('button',text,cls);result.type='button';result.addEventListener('click',callback);return result;
    }
    class Calendar {
        constructor(root,{onAdd=()=>{},onRefresh=async()=>{},onReview=async()=>{},onPreviewExpired=()=>{},savedOnly=false}={}) {
            Object.assign(this,{root,onAdd,onRefresh,onReview,onPreviewExpired,savedOnly});
            this.entries={};this.metadata={};this.preview=null;this.zone='Asia/Kuala_Lumpur';
            this.anchor=today(this.zone);this.view='week';this.query='';this.showPreview=true;this.navigated=false;this.initialized=false;
            root.classList.add('sc-calendar');
            root.innerHTML='<header class="sc-title"><div><p class="eyebrow">YOUR PRIVATE TEST CALENDAR</p><h2>Sandbox calendar</h2><p>View courses saved inside STAIR. Nothing here is sent to Google Calendar.</p></div><div class="student-actions" data-slot="actions"></div></header>'+
                '<div class="sc-toolbar"><div class="student-actions" data-slot="navigation"></div><h3 data-slot="range" aria-live="polite"></h3><div class="sc-view-buttons" role="group" aria-label="Calendar view" data-slot="views"></div></div>'+
                '<div class="sc-filters"><label>Go to date<input type="date" data-slot="date"></label><label>Display timezone<select data-slot="zone" aria-label="Calendar display timezone"><option value="Asia/Kuala_Lumpur">Malaysia time (UTC+08:00)</option><option value="Asia/Singapore">Singapore time (UTC+08:00)</option><option value="UTC">UTC</option></select></label><label>Find a class<input type="search" data-slot="search" placeholder="Course or venue"></label></div>'+
                '<div class="sc-legend"><span class="sc-saved-key">Saved in sandbox</span><span class="sc-preview-key">Preview — not saved</span><label><input type="checkbox" data-slot="preview-toggle" checked> Show pending preview</label></div>'+
                '<p class="sc-summary" data-slot="summary" role="status"></p><p class="sc-notice" data-slot="notice" role="status" hidden></p>'+
                '<div data-slot="empty" class="sc-empty" hidden></div><div data-slot="board"></div>'+
                '<p class="sc-footer">Only your saved sandbox courses are shown. Empty days do not prove that your timetable is complete.</p>';
            this.el=name=>root.querySelector('[data-slot="'+name+'"]');
            if(savedOnly) {
                root.querySelector('.sc-preview-key').hidden=true;
                this.el('preview-toggle').closest('label').hidden=true;
            }
            this.el('actions').append(button(savedOnly?'Review & output':'Add or update classes',onAdd,'button primary'),button('Refresh calendar',()=>this.refresh()));
            this.prev=button('Previous',()=>this.move(-1));this.next=button('Next',()=>this.move(1));
            this.el('navigation').append(this.prev,button('Today',()=>{this.anchor=today(this.zone);this.navigated=true;this.render();}),this.next,
                button('First saved class',()=>this.firstClass()));
            this.weekButton=button('Week',()=>this.setView('week'));this.monthButton=button('Month',()=>this.setView('month'));
            this.el('views').append(this.weekButton,this.monthButton);
            this.el('date').addEventListener('change',()=>{if(validDate(this.el('date').value)){this.anchor=this.el('date').value;this.navigated=true;this.render();}});
            this.el('zone').addEventListener('change',()=>{this.zone=this.el('zone').value;this.render();});
            this.el('search').addEventListener('input',()=>{this.query=this.el('search').value.trim().toLowerCase();this.render();});
            this.el('preview-toggle').addEventListener('change',()=>{this.showPreview=this.el('preview-toggle').checked;this.render();});
            this.dialog=node('dialog',null,'sc-dialog');this.dialog.setAttribute('aria-label','Sandbox calendar details');
            this.root.append(this.dialog);this.render();
        }
        update(entries,metadata={}) {
            this.entries=entries || {};this.metadata=metadata || {};
            if(!this.initialized && Object.keys(this.entries).length) {
                const zones=new Set(Object.values(this.entries).map(e=>e.start?.timeZone).filter(Boolean));
                if(zones.size===1) {
                    const zone=[...zones][0];
                    try {today(zone);this.zone=zone;
                        if(![...this.el('zone').options].some(o=>o.value===zone)){const option=node('option',zone);option.value=zone;this.el('zone').append(option);}
                        this.el('zone').value=zone;
                    }catch { /* Invalid display zones are reported by event validation. */ }
                }
                if(!this.navigated)this.firstClass(false);
                this.initialized=true;
            }
            this.render();
        }
        setPreview(preview) {
            clearTimeout(this.expiryTimer);this.preview=!this.savedOnly && preview?.target==='local'?preview:null;
            if(this.preview) {
                const id=this.preview.id;
                this.expiryTimer=setTimeout(()=>{if(this.preview?.id===id){this.preview=null;this.render();this.onPreviewExpired();}},Math.max(0,(this.preview.expires-Date.now()/1000)*1000));
            }
            this.render();
        }
        reset() {
            clearTimeout(this.expiryTimer);this.preview=null;this.entries={};this.metadata={};this.query='';this.el('search').value='';
            this.initialized=false;this.navigated=false;this.anchor=today(this.zone);this.dialog.close();this.dialog.replaceChildren();this.render();
        }
        setView(view) {this.view=view;this.render();}
        move(amount) {
            const next=this.view==='month'?shiftMonth(this.anchor,amount):shiftDate(this.anchor,amount*7);
            if(next){this.anchor=next;this.navigated=true;this.render();}
        }
        firstClass(render=true) {
            const {events}=project(this.entries,this.metadata,null,this.zone);
            if(events.length)this.anchor=events[0].startLocal.date;
            if(render){this.navigated=true;this.render();}
        }
        jumpToPreview() {
            const event=this.events?.find(e=>e.state!=='saved');
            if(event){this.anchor=event.startLocal.date;this.navigated=true;this.render();}
        }
        async refresh() {
            try {await this.onRefresh();}
            catch(error){this.el('notice').hidden=false;this.el('notice').textContent=error.message;}
        }
        render() {
            const projection=project(this.entries,this.metadata,!this.savedOnly && this.showPreview?this.preview:null,this.zone);
            this.events=projection.events;
            const events=this.events.filter(e=>(e.body.summary+' '+(e.body.location || '')).toLowerCase().includes(this.query));
            const dates=range(this.anchor,this.view), days=segments(events,dates,this.zone);
            this.el('date').value=this.anchor;
            this.weekButton.setAttribute('aria-pressed',String(this.view==='week'));this.monthButton.setAttribute('aria-pressed',String(this.view==='month'));
            this.prev.setAttribute('aria-label','Previous '+this.view);this.next.setAttribute('aria-label','Next '+this.view);
            this.el('range').textContent=this.view==='month'?new Intl.DateTimeFormat('en-GB',{month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(this.anchor+'T12:00:00Z')):dateText(dates[0])+' – '+dateText(dates[6]);
            const visible=new Set([...days.values()].flat().filter(s=>this.view!=='month' || s.date.slice(0,7)===this.anchor.slice(0,7)).map(s=>s.event.key));
            const count=Object.keys(this.entries).length,pending=this.events.filter(e=>e.state!=='saved').length;
            this.el('summary').textContent=count+' saved occurrence(s) · '+visible.size+' shown'+(pending?' · '+pending+' pending preview change(s)':'')+' · '+this.zone;
            this.el('notice').hidden=!projection.invalid.length;
            this.el('notice').textContent=projection.invalid.length+' event(s) could not be displayed. Their stored data has not been changed. Refresh or inspect sync history.';
            this.el('empty').hidden=count>0 || pending>0;
            this.el('empty').replaceChildren(node('h3',this.savedOnly?'No saved classes yet':'Your sandbox calendar is ready'),node('p',this.savedOnly?'Finish Review & output and apply the approved changes. Successfully saved classes will appear here.':'Add your timetable, check the preview and apply approved changes. Saved courses will appear here.'),button(this.savedOnly?'Go to Review & output':'Add classes',this.onAdd,'button primary'));
            this.el('board').replaceChildren(this.view==='month'?this.month(days,dates):this.week(days,dates));
            if(this.view==='week') {
                const scroll=this.el('board').firstChild;
                const first=[...days.values()].flat().sort((a,b)=>a.startMinute-b.startMinute)[0]?.startMinute;
                scroll.scrollTop=Math.max(0,((first ?? 480)-60)*PX_PER_MINUTE);
            }
        }
        eventButton(segment,compact=false) {
            const event=segment.event,pending=event.state!=='saved';
            const result=button('',()=>this.showEvent(event),'sc-event sc-colour-'+colour(event.body.summary)+(pending?' sc-pending':''));
            const time=shortTime(event.start,this.zone)+'–'+shortTime(event.end,this.zone);
            result.append(node('strong',event.body.summary),node('span',time));
            if(!compact && event.body.location)result.append(node('span',event.body.location));
            const label=pending?(event.state==='approved-preview'?'Approved preview · not saved':'Preview · not saved'):
                event.metadata.current_revision_id && event.metadata.current_revision_id!==event.metadata.revision_id?'Saved · source changed':'Saved';
            if(!compact || pending)result.append(node('small',label));
            result.setAttribute('aria-label',event.body.summary+', '+dateText(segment.date)+', '+time+', '+label);
            result.title=event.body.summary+' · '+time+' · '+(event.body.location || 'Venue not supplied')+' · '+label;
            return result;
        }
        week(days,dates) {
            const scroll=node('div',null,'sc-week-scroll');scroll.setAttribute('aria-label','Weekly sandbox calendar; scroll for other hours or days');scroll.tabIndex=0;
            const surface=node('div',null,'sc-week-surface'),head=node('div',null,'sc-week-head'),body=node('div',null,'sc-week-body');
            head.append(node('div','Time','sc-time-heading'));
            for(let i=0;i<7;i++)head.append(button(weekdays[i]+' '+dates[i].slice(8),()=>this.showDay(dates[i],days.get(dates[i])),'sc-day-heading'+(dates[i]===today(this.zone)?' sc-today':'')));
            const axis=node('div',null,'sc-time-axis');
            for(let hour=0;hour<24;hour++){const tick=node('span',String(hour).padStart(2,'0')+':00');tick.style.top=(hour*60*PX_PER_MINUTE)+'px';axis.append(tick);}
            body.append(axis);
            for(const date of dates) {
                const day=node('div',null,'sc-time-day'+(date===today(this.zone)?' sc-today-column':''));day.setAttribute('aria-label',dateText(date));
                for(const segment of columns(days.get(date))) {
                    const event=this.eventButton(segment);event.style.top=(segment.startMinute*PX_PER_MINUTE)+'px';
                    event.style.height=Math.max(28,(segment.endMinute-segment.startMinute)*PX_PER_MINUTE)+'px';
                    event.style.left='calc('+segment.lane*100/segment.lanes+'% + 3px)';event.style.width='calc('+100/segment.lanes+'% - 6px)';
                    day.append(event);
                }
                body.append(day);
            }
            surface.append(head,body);scroll.append(surface);return scroll;
        }
        month(days,dates) {
            const wrapper=node('div',null,'sc-month-scroll'),grid=node('div',null,'sc-month-grid');grid.setAttribute('aria-label','Monthly sandbox calendar');
            for(const name of weekdays)grid.append(node('div',name,'sc-month-heading'));
            for(const date of dates) {
                const cell=node('div',null,'sc-month-cell'+(date.slice(0,7)!==this.anchor.slice(0,7)?' sc-other-month':''));
                const dateButton=button(String(Number(date.slice(8))),()=>this.showDay(date,days.get(date)),'sc-month-date'+(date===today(this.zone)?' sc-today':''));
                dateButton.setAttribute('aria-label','Show classes on '+dateText(date));cell.append(dateButton);
                const segments=days.get(date);
                for(const segment of segments.slice(0,3))cell.append(this.eventButton(segment,true));
                if(segments.length>3)cell.append(button('+'+(segments.length-3)+' more',()=>this.showDay(date,segments),'sc-more'));
                grid.append(cell);
            }
            wrapper.append(grid);return wrapper;
        }
        openDialog(title) {
            this.dialog.replaceChildren();const heading=node('header');heading.append(node('h3',title),button('Close',()=>this.dialog.close()));
            this.dialog.append(heading);if(!this.dialog.open)this.dialog.showModal();
        }
        showDay(date,segments) {
            this.openDialog('Classes on '+dateText(date));
            this.dialog.append(button('View this week',()=>{this.anchor=date;this.view='week';this.navigated=true;this.dialog.close();this.render();}));
            if(!segments.length)this.dialog.append(node('p','No matching saved or preview courses on this date.'));
            for(const segment of segments)this.dialog.append(this.eventButton(segment));
        }
        showEvent(event) {
            this.openDialog(event.body.summary);
            const pending=event.state!=='saved',label=pending?'Preview only — this version is not saved':'Saved in your STAIR sandbox';
            this.dialog.append(node('p',label,pending?'sc-preview-label':'sc-saved-label'));
            const facts=node('dl');
            const full=value=>new Intl.DateTimeFormat('en-GB',{dateStyle:'full',timeStyle:'short',timeZone:this.zone}).format(new Date(value));
            for(const [name,value] of [['Starts',full(event.start)],['Ends',full(event.end)],['Display timezone',this.zone],
                ['Venue',event.body.location || 'Not supplied'],['Destination','STAIR sandbox on this computer']])facts.append(node('dt',name),node('dd',value));
            if(event.metadata.saved_at)facts.append(node('dt','Last applied'),node('dd',new Date(event.metadata.saved_at*1000).toLocaleString()));
            if(event.metadata.calendar_policy)facts.append(node('dt','Checks used when saved'),node('dd',event.metadata.calendar_policy==='registered-accept'?'Registered ACCEPT and source-supported dates':'Personal confirmation; not research ACCEPT'));
            this.dialog.append(facts);
            if(event.before)this.dialog.append(node('p','Saved version before this preview: '+event.before.start.dateTime+' · '+(event.before.location || 'Venue not supplied')));
            if(event.metadata.current_revision_id && event.metadata.current_revision_id!==event.metadata.revision_id)this.dialog.append(node('p','The source class has changed since this event was saved. Review it, create a new preview and approve the update.','sc-notice'));
            if(event.metadata.task_id) {
                const review=button('Review source class',async()=>{
                    review.disabled=true;
                    try {await this.onReview(event.metadata);this.dialog.close();}
                    catch(error){this.dialog.append(node('p',error.message,'sc-notice'));review.disabled=false;}
                });this.dialog.append(review);
            } else this.dialog.append(node('p','This older saved event has no retained source link. Open its original timetable in Review classes.'));
            this.dialog.append(node('p','Changes are made through Review classes, then preview and approval. This calendar does not delete events or move them by dragging.','sc-footer'));
        }
    }
    const api={Calendar,validDate,shiftDate,monday,shiftMonth,range,zonedParts,normalizeEntry,project,segments,columns};
    if(typeof module!=='undefined' && module.exports)module.exports=api;
    global.STAIRSavedCalendar=api;
})(globalThis);
