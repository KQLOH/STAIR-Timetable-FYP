/* Account-host enhancement. Credentials stay in HttpOnly cookies and JS memory. */
(() => {
    'use strict';
    window.STAIRAccountMode = true;
    document.body.classList.add('student-account-host');
    const nativeFetch = window.fetch.bind(window);
    const $ = id => document.getElementById(id);
    const node = (tag, text, cls) => {
        const item = document.createElement(tag);
        if (text != null) item.textContent = text;
        if (cls) item.className = cls;
        return item;
    };
    const button = (text, run, cls='secondary') => {
        const item=node('button',text,'button '+cls);item.type='button';item.addEventListener('click',()=>action(run,item));return item;
    };
    let csrf='', user=null, workspace=null, connectedKey='', connecting=false, pendingSource=false;
    let currentPlan=null, selected=new Set(), comparison=null, generation=0, lastSignature='', lastCaptureKey='', planGeneration=0;
    let batchDefaultsKey='', batchProblems=new Map(), dateOverrides=new Map();
    let sandboxCalendar=null, activeRunId=null, connectedResult=null;
    const className = task => {
        const c=task.current_event?.revision.content;
        return c ? [c.course_code,c.course_name,c.session_type].filter(Boolean).join(' · ') : 'Unread class';
    };
    const dateLabel = value => new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeZone:'UTC'}).format(new Date(value+'T12:00:00Z'));
    const slotLabel = body => {
        const format=new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeStyle:'short',timeZone:body.start.timeZone});
        return format.format(new Date(body.start.dateTime))+' – '+format.format(new Date(body.end.dateTime));
    };
    const sourceKey = source => source ? source.source_document_id+':'+source.content_sha256 : '';
    const status = (text,error=false) => {
        $('accountWorkflowStatus').textContent=text;
        $('accountWorkflowStatus').className='student-status'+(error?' error':'');
        if(error && typeof showToast==='function')showToast('Action needs attention',text,true);
    };
    function clearSession() {
        csrf='';user=null;workspace=null;connectedKey='';selected.clear();currentPlan=null;comparison=null;generation++;
        batchDefaultsKey='';batchProblems.clear();dateOverrides.clear();
        activeRunId=null;connectedResult=null;reviewController.selectedRunId=null;
        sandboxCalendar?.reset();
        reviewController?.forget();
        if(typeof uploadController!=='undefined')uploadController.cancel({notify:false});
        clearUploadView();state.events=[];state.source=null;state.canonical=null;state.intranet=null;
        state.reviewSnapshot=null;state.reviewStale=false;state.meta=null;state.connectionToken=null;state.studentName=null;
        // Account-bound drafts must not remain visible after the session ends.
        for(const id of ['copiedTimetableText','fileInput','studentBatchNote','studentBatchExcluded',
            'studentBatchStart','studentBatchEnd','studentBatchCustomTimezone','analysisStart','analysisEnd',
            'analysisZone','analysisScope','analysisCohort','studyStart','studyEnd']) {
            if($(id))$(id).value='';
        }
        if($('studentBatchTimezone'))$('studentBatchTimezone').value='';
        if($('studentBatchFrequency'))$('studentBatchFrequency').value='ONCE';
        if($('studentBatchConfirmed'))$('studentBatchConfirmed').checked=false;
        for(const input of $('studentCompletenessChecks')?.querySelectorAll('input') || [])input.checked=false;
        $('documentPreview')?.replaceChildren();
        for(const id of ['accountSavedSources','studentScheduleClasses','studentCalendarHistory','studentPlan','studentComparison'])$(id)?.replaceChildren();
        renderEverything();
        document.querySelector('.app-shell').inert=true;
        if(!$('studentLogin').open)$('studentLogin').showModal();
        $('studentUsername').focus();
    }
    window.fetch = async (input, options={}) => {
        const target=new URL(typeof input==='string'?input:input.url,location.href);
        if(target.origin===location.origin && target.pathname.startsWith('/api/')) {
            const requestGeneration=generation;
            const authenticationRequest=['/api/student/login','/api/student/register'].includes(target.pathname);
            const headers=new Headers(options.headers || (input instanceof Request?input.headers:undefined));
            const method=(options.method || (input instanceof Request?input.method:'GET')).toUpperCase();
            if(method!=='GET' && csrf)headers.set('X-STAIR-CSRF',csrf);
            const response=await nativeFetch(input,{...options,headers,credentials:'same-origin',cache:'no-store',redirect:'error'});
            if(requestGeneration!==generation && !authenticationRequest) {
                const cancelled=new Error('Your timetable or sign-in changed. Reopen the current timetable and try again.');
                cancelled.name='AbortError';throw cancelled;
            }
            if(response.status===401 && user && !authenticationRequest) {
                if(target.pathname.startsWith('/api/intranet/')) {
                    // An institutional expiry is not an expiry of the STAIR account.
                    let accountStillValid=false;
                    try {
                        const check=await nativeFetch(location.origin+'/api/student/session',{credentials:'same-origin',cache:'no-store',redirect:'error'});
                        if(check.ok){const current=await check.json();accountStillValid=!!current.user?.id && current.user.id===user?.id && current.csrf===csrf;}
                    }catch(_){}
                    if(requestGeneration!==generation){const cancelled=new Error('Your sign-in changed.');cancelled.name='AbortError';throw cancelled;}
                    if(accountStillValid)return response;
                }
                clearSession();$('studentLoginMessage').textContent='Your session ended. Sign in again to continue.';
            }
            return response;
        }
        return nativeFetch(input,options);
    };
    // The existing upload controller captures its fetcher during construction.
    if(typeof uploadController!=='undefined')uploadController.fetcher=window.fetch.bind(window);
    async function api(path, body, options={}) {
        const response=await fetch(path,{...options,method:body===undefined?'GET':'POST',
            headers:body===undefined?options.headers:{'Content-Type':'application/json',...options.headers},
            ...(body===undefined?{}:{body:typeof body==='string'?body:JSON.stringify(body)})});
        let result;try{result=await response.json();}catch{throw Error('The server did not return a valid response. Reload and check your saved results.');}
        if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Check the form fields and try again.');
        return result;
    }
    async function action(run, control) {
        if(control?.disabled)return;
        if(control)control.disabled=true;
        try {await run();}catch(error){status(error.message,true);}
        finally {if(control)control.disabled=control.dataset.keepDisabled==='true';}
    }
    function download(value,name) {
        const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
        const link=node('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function makeGate() {
        const dialog=node('dialog',null,'student-auth');dialog.id='studentLogin';dialog.setAttribute('aria-labelledby','studentLoginTitle');
        dialog.innerHTML='<p class="eyebrow">STAIR TIMETABLE</p><h2 id="studentLoginTitle">Your timetable, your space.</h2>'+
            '<p>Sign in to check your classes, save corrections and plan your calendar.</p>'+
            '<form id="studentLoginForm"><label>Username<input id="studentUsername" autocomplete="username" minlength="3" maxlength="40" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{2,39}" required placeholder="Choose a username"></label>'+
            '<label>Password<input id="studentPassword" type="password" autocomplete="current-password" minlength="10" maxlength="128" required placeholder="At least 10 characters"></label>'+
            '<p id="studentLoginMessage" role="status" class="student-auth-error"></p><button id="studentSignIn" class="button primary" type="submit">Sign in</button>'+
            '<button id="studentCreateAccount" class="button secondary" type="button">Create an account</button></form>'+
            '<p class="student-auth-note">This account is for STAIR on this computer. Intranet and Google sign-in are separate. Use a different password from your university account.</p>';
        dialog.addEventListener('cancel',event=>event.preventDefault());document.body.append(dialog);
        const submit=async create=>{
            if(!$('studentLoginForm').reportValidity())return;
            $('studentSignIn').disabled=true;$('studentCreateAccount').disabled=true;$('studentLoginMessage').textContent='Signing you in…';
            try {
                const data=await api('/api/student/'+(create?'register':'login'),{username:$('studentUsername').value,password:$('studentPassword').value});
                generation++;csrf=data.csrf;user=data.user;$('studentPassword').value='';await ready();
            }catch(error){$('studentLoginMessage').textContent=error.message;}
            finally{$('studentSignIn').disabled=false;$('studentCreateAccount').disabled=false;}
        };
        $('studentLoginForm').addEventListener('submit',event=>{event.preventDefault();submit(false);});
        $('studentCreateAccount').addEventListener('click',()=>submit(true));
        document.querySelector('.app-shell').inert=true;dialog.showModal();
    }
    function makePanels() {
        const account=node('div',null,'student-account-bar');account.append(node('strong','Signed out'));
        const name=node('strong');name.id='studentAccountName';account.replaceChildren(name,
            button('Sign out',async()=>{await api('/api/student/logout',{});location.reload();}));
        document.querySelector('.sidebar-footer').prepend(account);
        const review=node('section',null,'student-workflow');review.id='accountReviewPanel';
        review.innerHTML='<h3>Check your classes</h3><p>Compare each class with your original timetable. Use <strong>Edit class</strong> to correct a detail. Your changes are saved to your account.</p>'+
            '<p id="accountEditingStatus" role="status">Upload a timetable to begin.</p><div id="accountSavedSources" class="student-saved-list"></div>';
        $('view-review').prepend(review);
        const reconnect=button('Reconnect editing',async()=>{
            const result=await api('/api/student/review/connect',{});
            if(!result.connected)throw Error('Upload a timetable before enabling editing.');
            if(!reviewController.transport)attachReviewTransport();await reviewController.refreshQueue();
            await reviewController.restoreSavedSource();await refresh();
            $('accountEditingStatus').textContent='Editing reconnected for your saved timetables.';
        });review.append(reconnect);
        const routingDetails=node('details',null,'student-technical');routingDetails.append(node('summary','How the system checks classes'));
        if($('sourceRoutingSummary'))routingDetails.append($('sourceRoutingSummary'));review.append(routingDetails);
        if($('reconnectSavedReviews'))$('reconnectSavedReviews').textContent='Refresh saved reviews';
        for(const notice of $('view-review').querySelectorAll('p.review-notice:not([id])'))notice.textContent='Check your classes against the original timetable, save corrections, then continue to Review & output.';
        const panel=node('section',null,'student-workflow');panel.id='studentCalendarWorkflow';
        panel.innerHTML='<h3>Add your classes to your calendar</h3><p>Select your classes, choose the date settings once, then check the preview.</p>'+
            '<p id="studentCalendarPolicy" class="review-notice" role="status"></p>'+
            '<ol class="student-steps"><li>Choose your classes</li><li>Set dates once</li><li>Preview and approve</li></ol>'+
            '<div id="accountWorkflowStatus" class="student-status" role="status" aria-live="polite">Upload a timetable to begin.</div>'+
            '<div id="studentSelectionControls" class="student-actions"><button id="studentSelectAll" class="button secondary" type="button" disabled>Select all</button><button id="studentClearSelection" class="button secondary" type="button" disabled>Clear selection</button><span id="studentSelectionCount" role="status" aria-live="polite">0 classes selected</span></div>'+
            '<div id="studentScheduleClasses" class="student-class-list"></div>'+
            '<div id="studentBatchSettings"></div><div id="studentBatchProblems" role="status" aria-live="polite"></div>'+
            '<label class="student-target">Calendar destination<select id="studentCalendarTarget"><option value="local">STAIR local test calendar (this computer)</option><option value="google" disabled>Google test calendar — setup required</option></select></label>'+
            '<p id="studentCalendarDestinationHint"></p>'+
            '<p id="studentGoogleStatus"></p><div id="studentGoogleActions" class="student-actions"></div>'+
            '<div id="studentPreviewActions" class="student-actions"></div><div id="studentPlan"></div>'+
            '<details id="studentSavedCalendar" class="student-technical"><summary>My local calendar and sync history</summary><div id="studentCalendarHistory"></div></details>';
        // Review/output and the saved calendar are separate destinations.
        $('view-calendar').prepend(panel);
        const calendarView=node('section',null,'view');calendarView.id='view-saved-calendar';
        calendarView.setAttribute('aria-labelledby','pageTitle');$('view-workspace').parentElement.append(calendarView);
        const calendarRoot=node('section');calendarRoot.id='savedSandboxCalendar';calendarView.append(calendarRoot);
        const openCalendar=()=>{showView('saved-calendar');sandboxCalendar?.render();};
        const calendarNav=button('Calendar',openCalendar,'nav-item');calendarNav.dataset.view='saved-calendar';
        const calendarIcon=document.createElementNS('http://www.w3.org/2000/svg','svg');
        const calendarUse=document.createElementNS('http://www.w3.org/2000/svg','use');calendarUse.setAttribute('href','#i-calendar');calendarIcon.append(calendarUse);
        calendarNav.replaceChildren(calendarIcon,node('span','Calendar'));
        document.querySelector('.nav-item[data-view="calendar"]').after(calendarNav);
        const dashboardActions=node('div',null,'student-actions');
        const upload=$('workspaceUpload');upload.replaceWith(dashboardActions);
        dashboardActions.append(upload,button('Calendar',openCalendar,'button secondary large'));
        sandboxCalendar=new STAIRSavedCalendar.Calendar(calendarRoot,{
            savedOnly:true,
            onAdd:()=>{showView('calendar');$('studentSelectionControls').scrollIntoView({block:'start'});},
            onRefresh:refresh,
            onReview:async metadata=>{await openSavedTimetable(metadata.task_id);showView('review');}
        });
        $('studentSelectAll').addEventListener('click',()=>{
            const tasks=activeTasks();if(!tasks.length || tasks.length>100)return;
            selected=new Set(tasks.map(task=>task.task_id));
            invalidatePlan('All classes in this timetable are selected. Uncheck any classes that are not yours.');renderTasks();
        });
        $('studentClearSelection').addEventListener('click',()=>{
            selected.clear();invalidatePlan('Selection cleared. Choose the classes you want to add.');renderTasks();
        });
        $('studentCalendarTarget').addEventListener('change',()=>{updateDestinationHint();invalidatePlan('Destination changed. Preview the selected calendar again.');});
        updateDestinationHint();
        $('studentPreviewActions').append(button('Preview selected classes',preview,'primary'),button('Refresh saved state',refresh));
        const verification=node('section',null,'student-workflow');verification.id='studentVerification';
        verification.innerHTML='<h3>Check the whole timetable</h3><p>Before relying on a gap between classes, check that your original timetable is complete. A personal checklist does not establish independent verification.</p>'+
            '<div id="studentCompletenessChecks"></div><div id="studentCompletenessActions" class="student-actions"></div>'+
            '<p id="studentCompletenessResult" role="status"></p>'+
            '<details class="student-technical"><summary>JSON comparison and research readiness</summary>'+
            '<p>Export the original predictions and saved revisions separately. Import a personally checked reference to find differences. This tool does not perform formal held-out evaluation.</p>'+
            '<div id="studentJsonActions" class="student-actions"></div><label>Reference JSON (maximum 2 MiB)<input id="studentReferenceFile" type="file" accept=".json,application/json"></label>'+
            '<p id="studentResearchStatus"></p><pre id="studentComparison" tabindex="0"></pre></details>';
        $('view-analysis').prepend(verification);
        for(const [id,label] of [['original_pages_checked','I checked every page of the original timetable.'],['groups_checked','I selected the correct cohort, section and class groups.'],['no_missing_classes','I checked for missing and extra classes.'],['exceptions_checked','I checked teaching dates, repeat boundaries and exceptions.']]) {
            const l=node('label',null,'student-check'),input=node('input');input.type='checkbox';input.id='complete_'+id;l.append(input,node('span',label));$('studentCompletenessChecks').append(l);
        }
        $('studentCompletenessActions').append(button('Save personal checklist',saveCompleteness));
        $('studentJsonActions').append(button('Export results JSON',exportJson),button('Download blank reference template',exportTemplate),button('Compare reference JSON',compareJson),button('Download comparison',()=>{
            if(!comparison)throw Error('Compare a reference file first.');download(comparison,'stair-personal-comparison.json');
        }));
        const summary=document.querySelector('.sidebar-footer .system-status small');if(summary)summary.textContent='Your private timetable workspace';
        document.querySelectorAll('[data-view="calendar"] span').forEach(n=>n.textContent='Review & output');
        $('reviewToCalendar').textContent='Continue to output';
        const welcome=$('view-workspace').querySelector('.welcome-row p:not(.eyebrow)');
        if(welcome)welcome.textContent='Upload and review your timetable, or open Calendar to view your saved classes.';
        const limitations=$('view-calendar').querySelector('.calendar-limitations');if(limitations)limitations.hidden=true;
        const sync=$('syncButton');if(sync)sync.closest('.sync-panel').hidden=true;
        const sandbox=$('sandboxWorkflowTitle');if(sandbox)sandbox.closest('details')?.setAttribute('hidden','');
        for(const notice of $('view-calendar').querySelectorAll('p.review-notice:not([id])'))notice.hidden=true;
        const sourceTitle=$('view-calendar').querySelector('.calendar-intro h2');if(sourceTitle)sourceTitle.textContent='Source timetable preview';
        makeBatchSettings();
    }
    function makeBatchSettings() {
        $('studentBatchSettings').innerHTML='<form id="studentBatchForm" class="student-schedule-form student-batch-form">'+
            '<h4>Date settings for selected classes</h4><label>Add these classes<select id="studentBatchFrequency" aria-label="Add these classes">'+
            '<option value="ONCE">Once, on the dates shown in my timetable</option><option value="WEEKLY">Every week, until a date I choose</option></select></label>'+
            '<p id="studentBatchHint">Dates already read from your timetable are used automatically. Only missing dates need your input.</p>'+
            '<div id="studentBatchRange" class="student-date-grid" hidden><label>Start repeating from<input id="studentBatchStart" type="date"></label><label>Stop repeating after<input id="studentBatchEnd" type="date"></label></div>'+
            '<label>Timezone for all selected classes<select id="studentBatchTimezone" aria-label="Timezone for all selected classes" required><option value="">Choose your timezone</option>'+
            '<option value="Asia/Kuala_Lumpur">Malaysia time (UTC+08:00)</option><option value="Asia/Singapore">Singapore time (UTC+08:00)</option><option value="Asia/Hong_Kong">Hong Kong time (UTC+08:00)</option><option value="UTC">UTC</option><option value="custom">Other timezone</option></select></label>'+
            '<label id="studentBatchCustomLabel" hidden>Other timezone<input id="studentBatchCustomTimezone" placeholder="For example: Europe/London" maxlength="80"></label>'+
            '<details class="student-technical"><summary>Optional notes and dates with no class</summary>'+
            '<label id="studentBatchExcludedLabel" hidden>Dates with no class (optional)<input id="studentBatchExcluded" placeholder="YYYY-MM-DD, separated by commas"><small>Applies to matching class dates within your repeat range.</small></label>'+
            '<label>Note (optional)<textarea id="studentBatchNote" rows="2" maxlength="1000" placeholder="Leave blank if not needed"></textarea></label></details>'+
            '<label class="student-check"><input id="studentBatchConfirmed" type="checkbox" required><span>These are my classes, and I checked the dates and times against my timetable.</span></label>'+
            '<small>Nothing is synchronized until you approve the changes in the preview.</small></form>';
        $('studentBatchForm').addEventListener('submit',event=>{event.preventDefault();action(preview);});
        for(const input of $('studentBatchForm').querySelectorAll('input,select,textarea')) {
            if(input.id==='studentBatchConfirmed')continue;
            input.addEventListener('input',()=>{batchFields();invalidatePlan('Date settings changed. Check the selection and preview again.');});
        }
        batchFields();
    }
    function batchFields() {
        const weekly=$('studentBatchFrequency').value==='WEEKLY';
        $('studentBatchRange').hidden=!weekly;$('studentBatchExcludedLabel').hidden=!weekly;
        $('studentBatchStart').required=weekly;$('studentBatchEnd').required=weekly;
        const custom=$('studentBatchTimezone').value==='custom';
        $('studentBatchCustomLabel').hidden=!custom;$('studentBatchCustomTimezone').required=custom;
        $('studentBatchHint').textContent=weekly?'Each class repeats on its own weekday within this range. Choose an end date supported by your semester schedule.':'Dates already read from your timetable are used automatically. Only missing dates need your input.';
    }
    function batchDefaults(tasks) {
        const key=sourceKey(reviewSource().packet?.source);
        if(!tasks.length || batchDefaultsKey===key)return;
        batchDefaultsKey=key;
        const dates=tasks.map(t=>t.current_event.revision.content.date).filter(Boolean).sort();
        $('studentBatchFrequency').value='ONCE';$('studentBatchStart').value=dates[0] || '';$('studentBatchEnd').value='';
        const zones=new Set(tasks.map(t=>t.current_event.revision.content.source_timezone || workspace.schedules[t.task_id]?.timezone).filter(Boolean));
        const zone=zones.size===1?[...zones][0]:'';
        const known=[...$('studentBatchTimezone').options].some(o=>o.value===zone);
        $('studentBatchTimezone').value=known?zone:'custom';$('studentBatchCustomTimezone').value=known?'':zone;
        $('studentBatchNote').value='';$('studentBatchExcluded').value='';$('studentBatchConfirmed').checked=false;batchFields();
    }
    function activeTasks() {
        const capture=reviewSource().packet?.source;
        if(!capture || !activeRunId)return [];
        const identities=new Set((reviewSource().packet?.events || []).map(STAIRUpload.eventIdentity).filter(Boolean));
        const runs=new Map();
        for(const task of workspace?.tasks || []) {
            if(task.current_event?.revision.source_document_id!==capture.source_document_id || task.source_sha256!==capture.content_sha256)continue;
            if(!runs.has(task.run_id))runs.set(task.run_id,[]);
            runs.get(task.run_id).push(task);
        }
        const matching=[...runs.entries()].filter(([id,tasks])=>(!activeRunId || id===activeRunId) && tasks.length===identities.size && tasks.every(t=>identities.has(STAIRUpload.eventIdentity(t.current_event)))).map(([,tasks])=>tasks);
        // Never combine separate saved readings, including partially matching runs.
        return matching.length===1?matching[0]:[];
    }
    async function ready() {
        const expected=generation;
        const info=await api('/api/student/session');if(expected!==generation)return;csrf=info.csrf;user=info.user;
        $('studentAccountName').textContent=user.username;document.querySelector('.app-shell').inert=false;$('studentLogin').close();
        $('studentLoginMessage').textContent='';
        attachReviewTransport();
        await refresh();if(expected!==generation)return;renderGoogle(info.google);
        const strict=info.calendar_policy==='registered-accept';
        $('studentCalendarPolicy').textContent=strict
            ? (info.research_accept_available?'Strict output: only a current registered ACCEPT with source-supported dates can be saved. Preview and explicit approval are still required.':'Strict output is waiting for approved validation evidence. Checking a class yourself cannot unlock it; existing saved classes remain available in Calendar.')
            : 'Personal sandbox: these outputs are based on your checks. They are not research ACCEPT results.';
        if(strict){$('studentCalendarTarget').value='local';$('studentCalendarTarget').disabled=true;for(const control of $('studentGoogleActions').querySelectorAll('button'))control.disabled=true;updateDestinationHint();}
        $('studentResearchStatus').textContent=info.research_accept_available ? 'Approved operating-point configuration loaded. Each ACCEPT still needs a current revision-bound assessment.' : 'Automatic ACCEPT is waiting for approved independent validation and operating-point registration. Personal checks and calendar approval remain available separately.';
        const googleResult=new URLSearchParams(location.search).get('google');
        if(googleResult){status(googleResult==='connected'?'Google test calendar connected. Preview your changes before approving synchronization.':'Google authorization was cancelled or could not be verified. Connect again.',googleResult!=='connected');history.replaceState(null,'',location.pathname);}
        window.STAIRAccount.sourceChanged();
    }
    function attachReviewTransport() {
        reviewController.attachTrustedHostTransport((url,options={})=>{
            const target=new URL(url,location.href);
            if(target.origin!==location.origin || !target.pathname.startsWith('/api/review/'))throw Error('Review destination refused.');
            return fetch(target.href,options);
        });
    }
    function renderGoogle(google) {
        $('studentGoogleStatus').textContent=google.message+(google.connected?' Connected to '+google.label+'.':'');
        const option=$('studentCalendarTarget').querySelector('[value="google"]');option.disabled=!google.connected;
        option.textContent=google.connected?google.label:google.configured?'Google test calendar — connect first':'Google test calendar — setup required';
        $('studentGoogleActions').replaceChildren();
        const setup=node('a','Google setup guide');setup.href='/google-calendar-setup';setup.target='_blank';setup.rel='noopener noreferrer';setup.className='button secondary';$('studentGoogleActions').append(setup);
        if(google.connected)$('studentGoogleActions').append(button('Disconnect Google',async()=>{await api('/api/student/google/disconnect',{});$('studentCalendarTarget').value='local';invalidatePlan('Google disconnected.');renderGoogle((await api('/api/student/session')).google);}));
        else if(google.configured)$('studentGoogleActions').append(button('Connect Google Calendar',async()=>{
            const result=await api('/api/student/google/connect',{}),target=new URL(result.url);
            if(target.origin!=='https://accounts.google.com')throw Error('The Google sign-in address was not recognized.');location.assign(target.href);
        }));
        if(google.connected){const open=node('a','Open Google Calendar');open.href='https://calendar.google.com/';open.target='_blank';open.rel='noopener noreferrer';open.className='button secondary';$('studentGoogleActions').append(open);}
        updateDestinationHint();
    }
    function updateDestinationHint() {
        $('studentCalendarDestinationHint').textContent=$('studentCalendarTarget').value==='local'
            ? 'Saved inside STAIR on this computer. After applying the approved output, open Calendar from the dashboard to see your saved classes. This does not add events to Google or Windows Calendar.'
            : 'Changes go to the connected Google test calendar only after you approve and apply the preview.';
    }
    function updateSelectionControls(tasks=activeTasks()) {
        const count=tasks.filter(task=>selected.has(task.task_id)).length;
        $('studentSelectionCount').textContent=count+' of '+tasks.length+' classes selected';
        $('studentSelectAll').disabled=!tasks.length || count===tasks.length || tasks.length>100;
        $('studentSelectAll').title=tasks.length>100?'Select up to 100 classes for one preview.':'';
        $('studentClearSelection').disabled=count===0;
    }
    async function refresh() {
        if(!user)return;
        const expected=generation, data=await api('/api/student/workspace');
        if(expected!==generation || !user)return;
        workspace=data;renderTasks();renderSaved();renderHistory();
        sandboxCalendar?.update(workspace.entries,workspace.calendar_metadata || {});
    }
    function renderTasks() {
        const container=$('studentScheduleClasses');container.replaceChildren();const tasks=activeTasks();
        updateSelectionControls(tasks);
        $('studentBatchSettings').hidden=!tasks.length;
        if(!tasks.length){const actions=node('div',null,'student-actions');actions.append(button('Upload timetable',()=>showView('upload')),button('Open saved timetables',()=>showView('review')));container.append(node('p','Upload a timetable or open a saved timetable from Review classes.'),actions);return;}
        batchDefaults(tasks);
        for(const task of tasks) {
            const row=node('div',null,'student-class-row'),input=node('input'),details=node('div'),actions=node('div',null,'student-actions');
            const c=task.current_event.revision.content, saved=workspace.schedules[task.task_id];
            const valid=saved?.revision_id===task.current_event.revision.revision_id;
            input.type='checkbox';input.checked=selected.has(task.task_id);
            input.setAttribute('aria-label','Select '+className(task));
            input.addEventListener('change',()=>{input.checked?selected.add(task.task_id):selected.delete(task.task_id);invalidatePlan('Class selection changed. Create a new preview.');updateSelectionControls();});
            details.append(node('strong',className(task)),node('small',[c.start_time&&c.end_time?c.start_time+'–'+c.end_time:'Time needs correction',c.location].filter(Boolean).join(' · ')));
            const weekdays=['','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
            details.append(node('small',c.date?dateLabel(c.date):weekdays[c.day_of_week] || 'Date and weekday missing'));
            if(!c.date) {
                const label=node('label','Date for this class (only if adding once)'),date=node('input');
                date.type='date';date.value=dateOverrides.get(task.task_id) || (saved?.frequency==='ONCE'?saved.first_date:'') || '';
                date.setAttribute('aria-label','Date for '+className(task));
                date.addEventListener('input',()=>{dateOverrides.set(task.task_id,date.value);invalidatePlan('A class date changed. Preview the selection again.');});
                label.append(date);label.className='student-missing-date';details.append(label);
            }
            if(valid)details.append(node('small','Saved date settings available. You can replace them for this selection.'));
            if(batchProblems.has(task.task_id))details.append(node('p',batchProblems.get(task.task_id),'student-status error'));
            actions.append(button('Edit class',()=>studentEditor.open(task.current_event.candidate_id)));
            row.append(input,details,actions);container.append(row);
        }
    }
    function renderSaved() {
        const list=$('accountSavedSources');list.replaceChildren();const sources=new Map();
        for(const task of workspace.tasks){if(!task.current_event)continue;const id=task.run_id;if(!sources.has(id))sources.set(id,[]);sources.get(id).push(task);}
        let version=0;
        for(const [id,tasks] of sources){version++;const saved=new Date(tasks[0].created_at).toLocaleString('en-GB'),profile=tasks[0].extraction_profile || '',route=tasks[0].source_route,type=profile.includes('s2b')?'Colored':profile.includes('s2a')?'Grid':route==='tarumt_html'?'TAR UMT':route==='native_pdf'?'PDF':route==='text'?'Copied text':'General';list.append(button('Open saved version '+version+' '+type+' · '+tasks.length+(tasks.length===1?' class':' classes')+' · '+saved+(id===activeRunId?' · Current':''),()=>openSavedTimetable(tasks[0].task_id)));}
    }
    async function openSavedTimetable(taskId) {
        if(state.upload?.busy)throw Error('Wait until your current upload finishes.');
        if(studentEditor.dirty() && !window.confirm('Open another saved version and discard the unsaved class edit?'))return;
        const expected=++generation;
        const snapshot=await api('/api/review/tasks/'+encodeURIComponent(taskId)+'/snapshot');
        if(expected!==generation || !user)return;
        STAIRUpload.validatePacket(snapshot.packet);
        if(!snapshot.tasks.length || snapshot.tasks.some(t=>t.run_id!==snapshot.tasks[0].run_id))throw Error('This saved version could not be opened safely. Your current timetable is retained.');
        connectedKey='';clearUploadView();state.reviewSnapshot=null;state.reviewStale=false;state.intranet=null;state.canonical=null;state.events=[];state.source=null;
        if(activeRunId!==snapshot.tasks[0]?.run_id)for(const id of ['analysisStart','analysisEnd','analysisZone'])if($(id))$(id).value='';
        activeRunId=snapshot.tasks[0]?.run_id || null;reviewController.selectedRunId=activeRunId;connectedResult=null;
        selected.clear();batchDefaultsKey='';dateOverrides.clear();
        applyReviewSnapshot(snapshot);connectedKey=sourceKey(snapshot.packet.source);
        renderTasks();renderSaved();
        $('accountEditingStatus').textContent='Historical saved timetable opened. Review and output now use this selected version. Upload the original again to return to a fresh reading.';
    }
    function renderHistory() {
        const container=$('studentCalendarHistory');container.replaceChildren();
        const entries=Object.values(workspace.entries);container.append(node('p',entries.length+' occurrence(s) saved in your private local test calendar.'));
        const events=node('ul',null,'student-plan-list');
        for(const entry of entries.sort((a,b)=>a.start.dateTime.localeCompare(b.start.dateTime)).slice(0,100))events.append(node('li',entry.summary+' · '+slotLabel(entry)+' · '+(entry.location || 'Venue not supplied')));
        container.append(events);
        for(const plan of [...workspace.plans].reverse().slice(0,20)) {
            const line=node('div',null,'student-actions');line.append(node('span',new Date(plan.created*1000).toLocaleString()+' · '+(plan.target==='local'?'Local test calendar':'Google test calendar')+' · '+(plan.outcome?.status || 'Preview only')));
            if(plan.outcome)line.append(button('Check saved result',async()=>{
                const result=await api('/api/student/calendar/check',{plan_id:plan.id});status(result.status==='SUCCEEDED'?'All planned class details match the saved calendar.':'Some planned details do not match. Create a fresh preview; no changes were sent by this check.',result.status!=='SUCCEEDED');
            }));container.append(line);
        }
    }
    function invalidatePlan(message) {
        planGeneration++;
        currentPlan=null;$('studentPlan').replaceChildren();if(message)status(message);
        if($('studentBatchConfirmed'))$('studentBatchConfirmed').checked=false;
        for(const input of $('studentCompletenessChecks').querySelectorAll('input'))input.checked=false;
        $('studentCompletenessResult').textContent='';
    }
    async function preview() {
        const activeIds=new Set(activeTasks().map(t=>t.task_id));
        const taskIds=[...selected].filter(id=>activeIds.has(id));
        if(!taskIds.length)throw Error('Select the classes you want to add first.');
        if(!$('studentBatchForm').reportValidity())return;
        const expected=++planGeneration;
        currentPlan=null;$('studentPlan').replaceChildren();
        batchProblems.clear();$('studentBatchProblems').replaceChildren();
        status('Checking selected classes and reading the target calendar…');
        const tasks=activeTasks(),weekly=$('studentBatchFrequency').value==='WEEKLY';
        const prepared=await api('/api/student/schedules/batch',{
            classes:tasks.filter(t=>taskIds.includes(t.task_id)).map(t=>({task_id:t.task_id,
                revision_id:t.current_event.revision.revision_id,first_date:dateOverrides.get(t.task_id) || (!t.current_event.revision.content.date && workspace.schedules[t.task_id]?.frequency==='ONCE'?workspace.schedules[t.task_id].first_date:null)})),
            frequency:weekly?'WEEKLY':'ONCE',timezone:$('studentBatchTimezone').value==='custom'?$('studentBatchCustomTimezone').value.trim():$('studentBatchTimezone').value,
            starts_on:weekly?$('studentBatchStart').value:null,ends_on:weekly?$('studentBatchEnd').value:null,
            excluded_dates:weekly?$('studentBatchExcluded').value.split(',').map(s=>s.trim()).filter(Boolean):[],
            source_note:$('studentBatchNote').value.trim(),checked_original:$('studentBatchConfirmed').checked
        });
        if(expected!==planGeneration || !user)return;
        batchProblems=new Map(prepared.blocked.map(item=>[item.task_id,item.message]));
        selected=new Set(prepared.ready_task_ids);
        if(prepared.blocked.length) {
            const heading=node('p',prepared.blocked.length+' class(es) need attention and are excluded from this preview.','student-status error'),list=node('ul');
            for(const item of prepared.blocked)list.append(node('li',(tasks.find(t=>t.task_id===item.task_id)?className(tasks.find(t=>t.task_id===item.task_id)):'Class')+': '+item.message));
            $('studentBatchProblems').append(heading,list);
        }
        await refresh();
        if(expected!==planGeneration || !user)return;
        if(!prepared.ready_task_ids.length){status('No classes are ready yet. Check the marked details, select the classes again and preview.',true);return;}
        const received=await api('/api/student/calendar/preview',{task_ids:prepared.ready_task_ids,target:$('studentCalendarTarget').value});
        if(expected!==planGeneration || !user)return;
        currentPlan=received;
        const panel=$('studentPlan');panel.replaceChildren();
        const plan=currentPlan, title=node('h4','Review these calendar changes');
        const summary={CREATE:0,UPDATE:0,NO_OP:0};for(const op of plan.operations)summary[op.action]++;
        panel.append(title,node('p',summary.CREATE+' new · '+summary.UPDATE+' updated · '+summary.NO_OP+' unchanged. Destination: '+(plan.target==='local'?'your local test calendar':'your connected Google test calendar')+'. No events will be deleted.'));
        const list=node('ul',null,'student-plan-list');
        for(const op of plan.operations) {
            const li=node('li');li.append(node('span',({CREATE:'Add class',UPDATE:'Update class',NO_OP:'Keep unchanged'})[op.action],'student-plan-tag'),node('strong',op.body.summary),node('div',slotLabel(op.body)+' · '+op.body.start.timeZone),node('div',op.body.location || 'Venue not supplied'));
            if(op.before)li.append(node('span','Previously: '+slotLabel(op.before)+' · '+(op.before.location || 'No venue'),'plan-before'));
            list.append(li);
        }
        const confirmation=node('label',null,'student-check'),check=node('input');check.type='checkbox';
        confirmation.append(check,node('span','I checked these exact changes and approve applying them to the displayed calendar.'));
        const actions=node('div',null,'student-actions');
        const apply=button('Apply approved changes',async()=>{
            if(currentPlan?.id!==plan.id)throw Error('Your preview changed. Create a fresh preview.');
            apply.disabled=true;apply.dataset.keepDisabled='true';check.disabled=true;approveButton.disabled=true;
            try {
                const result=await api('/api/student/calendar/execute',{plan_id:plan.id});
                status(result.status==='SUCCEEDED'?'Calendar updated. All planned changes were confirmed.':'The server could not confirm every change. Use Check saved result before making a fresh preview.',result.status!=='SUCCEEDED');
                currentPlan=null;await refresh();
                $('studentSavedCalendar').open=true;
                if(result.status==='SUCCEEDED' && plan.target==='local') {
                    const savedActions=node('div',null,'student-actions');
                    savedActions.append(button('Open Calendar',()=>{showView('saved-calendar');sandboxCalendar?.render();},'primary'));
                    panel.append(savedActions);
                }
                if(result.status==='SUCCEEDED' && plan.target.startsWith('google:')) {
                    const open=node('a','View courses in Google Calendar');open.href='https://calendar.google.com/';open.target='_blank';open.rel='noopener noreferrer';open.className='button primary';
                    panel.append(node('p','Saved to your connected Google test calendar. Select that calendar in Google to view the classes; your local STAIR calendar is separate.'),open);
                }
            }catch(error){status('The outcome is uncertain. Refresh saved state and use Check saved result before trying again. '+error.message,true);throw error;}
            finally{apply.disabled=true;}
        },'primary');apply.disabled=true;
        const approveButton=button('Approve this preview',async()=>{
            if(!check.checked || currentPlan?.id!==plan.id)throw Error('Check the confirmation box for this preview.');
            await api('/api/student/calendar/approve',{plan_id:plan.id,confirmed:true});
            if(currentPlan?.id!==plan.id || expected!==planGeneration)return;
            currentPlan.approved=true;
            apply.disabled=false;check.disabled=true;approveButton.hidden=true;status('Preview approved. Apply the approved changes within ten minutes.');
        });
        check.addEventListener('change',()=>{approveButton.disabled=!check.checked;});approveButton.disabled=true;
        actions.append(approveButton,apply);panel.append(list,confirmation,actions);status('Preview ready. Check every change before approving.');
    }
    async function saveCompleteness() {
        const task_ids=activeTasks().map(t=>t.task_id);if(!task_ids.length)throw Error('Open your timetable first.');
        const body={task_ids};for(const id of ['original_pages_checked','groups_checked','no_missing_classes','exceptions_checked'])body[id]=$('complete_'+id).checked;
        const result=await api('/api/student/completeness',body);$('studentCompletenessResult').textContent=result.message;status('Personal completeness checklist saved.');
    }
    function firstTask() {const task=activeTasks()[0];if(!task)throw Error('Upload or open a saved timetable first.');return task;}
    async function exportJson() {download(await api('/api/student/export/'+firstTask().task_id),'stair-timetable-results.json');}
    async function exportTemplate() {const data=await api('/api/student/export/'+firstTask().task_id);download(data.reference_template,'stair-blank-reference.json');status('Blank template downloaded. Fill events with independently checked readings; importing it does not certify independent verification.');}
    async function compareJson() {
        const file=$('studentReferenceFile').files[0];if(!file || file.size>2*1024*1024)throw Error('Choose a reference JSON file smaller than 2 MiB.');
        let reference;try{reference=JSON.parse(await file.text());}catch{throw Error('The selected file is not valid JSON.');}
        comparison=await api('/api/student/compare',{task_id:firstTask().task_id,reference});
        $('studentComparison').textContent=JSON.stringify(comparison,null,2);status('Comparison complete. Original predictions and current revisions are reported separately.');
    }
    async function connectSource() {
        if(!user)return;
        if(connecting){pendingSource=true;return;}
        const source=reviewSource().packet?.source,result=reviewSource().result;
        if(!source || state.upload?.busy || !result?.reconstruction)return;
        const key=sourceKey(source);if(key===connectedKey && result===connectedResult){renderTasks();return;}
        connecting=true;const currentGeneration=generation;
        try {
            $('accountEditingStatus').textContent='Connecting your private editing workspace…';
            if(state.upload?.file) {
                const response=await fetch('/api/student/source',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-STAIR-Source-ID':source.source_document_id,'X-STAIR-Source-SHA256':source.content_sha256},body:state.upload.file});
                if(!response.ok){const error=await response.json();throw Error(error.detail || 'The original file did not match.');}
            }
            if(currentGeneration!==generation || result!==reviewSource().result || key!==sourceKey(reviewSource().packet?.source))return;
            await reviewController.refreshQueue();
            await refresh();
            const resolved=await api('/api/review/connect-run',STAIRUpload.reviewIngestBody(result));
            if(currentGeneration!==generation || result!==reviewSource().result || key!==sourceKey(reviewSource().packet?.source))return;
            activeRunId=resolved.run_id;reviewController.selectedRunId=activeRunId;connectedResult=result;
            connectedKey=key;selected.clear();batchDefaultsKey='';dateOverrides.clear();
            await reviewController.refreshQueue();await reviewController.restoreSavedSource();await refresh();
            if(currentGeneration!==generation || !user)return;
            $('accountEditingStatus').textContent='Editing is ready. Your changes are private to '+user.username+'.';
            status('Timetable connected. Select your classes and choose the date settings once.');
        }catch(error){if(currentGeneration===generation && user){$('accountEditingStatus').textContent='Editing could not connect: '+error.message;status(error.message,true);}}
        finally{connecting=false;if(pendingSource){pendingSource=false;queueMicrotask(connectSource);}}
    }
    window.STAIRAccount={sourceChanged(){
        if(user) {
            const reading=state.upload || state.intranet;
            if(reading && reading!==connectedResult){if(activeRunId)for(const id of ['analysisStart','analysisEnd','analysisZone'])if($(id))$(id).value='';activeRunId=null;reviewController.selectedRunId=null;$('accountEditingStatus').textContent=reading.busy?'Reading this timetable. Editing connects when it is ready.':reading.reconstruction && reviewSource().packet?.events?.length?'Connecting this reading to your saved versions...':'No editable classes in this reading. Try another timetable type or a clearer source. Your saved versions are retained.';}
            const packet=reviewSource().packet;
            const capture=sourceKey(packet?.source);
            const captureChanged=capture!==lastCaptureKey;
            if(captureChanged){for(const id of ['analysisStart','analysisEnd','analysisZone'])if($(id))$(id).value='';selected.clear();batchDefaultsKey='';batchProblems.clear();dateOverrides.clear();$('studentBatchProblems').replaceChildren();lastCaptureKey=capture;comparison=null;$('studentComparison').textContent='';}
            const signature=sourceKey(packet?.source)+':'+(packet?.events || []).map(e=>e.revision.revision_id).join(',');
            if(signature!==lastSignature) {
                lastSignature=signature;invalidatePlan(!packet?'Upload a timetable or open a saved one to begin.':captureChanged?'Timetable opened. Select your classes and choose the date settings once.':'Class details changed. Check the selection and preview again.');
                if(!connecting)action(refresh);
            }
            $('metricConfidence').textContent='Local test calendar';
            const metric=$('metricConfidence').parentElement.querySelector('small');if(metric)metric.textContent='View saved classes in Calendar; prepare changes in Review & output';
            const boundary=$('reviewBoundary');if(boundary)boundary.textContent='Check the original source and save corrections here. Continue to Review & output to confirm dates and approve changes.';
            const warning=$('studentCalendarWarnings');if(warning)warning.textContent='The week view below shows source dates. Confirmed dates and repeat schedules are managed above.';
        }
        if(currentPlan) {
            const currentIds=new Set(activeTasks().map(t=>t.task_id));
            if(state.reviewStale || currentPlan.task_ids.some(id=>!currentIds.has(id)))invalidatePlan('The timetable changed. Confirm dates and preview again.');
        }
        queueMicrotask(connectSource);
    }};
    async function boot() {
        makePanels();makeGate();
        const expected=generation;
        try {await ready();}catch(error) {if(expected===generation){clearSession();if(error.message!=='Please sign in to continue.')$('studentLoginMessage').textContent=error.message;}}
        document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && user)action(async()=>{await api('/api/student/session');});});
    }
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
