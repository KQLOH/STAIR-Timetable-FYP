/* M7 development diagnostics. No completeness minting, storage or calendar calls. */
(function (global) {
    "use strict";
    function packetFrom(upload, canonical) {
        const output = upload?.reconstruction?.canonical_output;
        if (output?.packet) return {...output.packet, events:[...output.packet.events, ...(output.unresolved_candidates || [])]};
        return upload ? upload.extraction || null : canonical || null;
    }
    function line(parent, text, tag="p") {
        const node = document.createElement(tag); node.textContent = text; parent.append(node); return node;
    }
    function render(result, container) {
        container.replaceChildren();
        line(container, `${result.completeness.label} · DEVELOPMENT / DEMO`, "h3");
        line(container, "Candidate arithmetic does not verify source correctness. Registered C1/P1-A and calendar writes remain blocked.");
        for (const type of ["observed_candidate_count", "normalized_session_count", "observed_scheduled_hours", "hours_by_day", "hours_by_course", "hours_by_session_type", "confirmed_conflicts"]) {
            const f = result.facts.find(item=>item.type===type);
            if (!f) continue;
            const value = type==="confirmed_conflicts" ? `${f.value.length} supported pairs` : JSON.stringify(f.value);
            line(container, `${type.replaceAll("_"," ")}: ${f.status} — ${value}`);
            const details = document.createElement("details");
            line(details, "Fact evidence and source revisions", "summary");
            line(details, JSON.stringify({fact_id:f.fact_id,event_refs:f.event_refs,reason_codes:f.reason_codes},null,2), "pre");
            container.append(details);
        }
        line(container, `Completeness blockers: ${result.completeness.reason_codes.join(", ") || "none within attested scope"}`);
        line(container, `Recommendations: ${result.recommendation_status}`);
        for (const r of result.recommendations) line(container, `${r.type}: ${r.explanation} ${r.window ? JSON.stringify(r.window) : ""}`);
        line(container, `Registered P1-A: ${result.registered_p1a_status}. ${result.registered_blockers.join(", ")}`);
    }
    function renderStudent(result,container) {
        container.replaceChildren();
        const complete=result.completeness.trusted_complete, facts=result.student_facts;
        line(container,complete?"Whole schedule independently checked":"Your observed classes", "h3");
        line(container,complete?"Verified only for the checked source, dates and class group. This does not approve calendar changes.":"This timetable may have missing classes. Blank spaces do not establish free time. Complete the source check before requesting study windows.");
        if(result.completeness.synthetic)line(container,"Synthetic test schedule — demonstration only. This is not a genuinely verified student timetable.");
        if(facts) {
            const w=result.verified_workload;
            line(container,complete?`${w.class_contact_hours} scheduled class hours in the checked date range`:`${facts.class_count} readable class slots · ${facts.printed_class_hours ?? "Unknown"} printed class hours`);
            if(!complete)line(container,"Printed durations only; recurrence and elapsed hours are not inferred.");
            const hours=complete?w.contact_hours_by_day:facts.hours_by_day;
            const list=document.createElement("ul");for(const [day,h] of Object.entries(hours))line(list,`${day}: ${h} class hours`,"li");container.append(list);
            const busiest=complete?w.busiest_days:facts.busiest_observed_days, lightest=complete?w.lightest_days:facts.lightest_observed_active_days;
            line(container,`Busiest ${complete?"days":"observed days"}: ${busiest.join(", ") || "Not known"}`);
            line(container,`Lightest ${complete?"days":"observed active days"}: ${lightest.join(", ") || "Not known"}`);
            line(container,`${facts.possible_overlaps.length} possible overlapping pair(s) · ${facts.possible_duplicates.length} possible duplicate(s). Inspect these against the original.`);
            const occupied=document.createElement("details");line(occupied,"Occupied class slots","summary");
            for(const row of facts.occupied_local_slots)line(occupied,`${row.day} · ${row.start}–${row.end} · ${row.course}`);container.append(occupied);
            if(complete)for(const [week,h] of Object.entries(w.contact_hours_by_calendar_week))line(container,`Week beginning ${week}: ${h} class hours`);
            if(complete){
                const occupied=result.facts.find(f=>f.type==='occupancy_intervals');
                const conflicts=result.facts.find(f=>f.type==='confirmed_conflicts');
                line(container,`Occupied time (${result.scope.timezone})`, 'h4');
                for(const v of occupied?.value||[])line(container,`${v.start} – ${v.end}`);
                line(container,`${conflicts?.value?.length||0} confirmed overlap(s)`, 'h4');
                const courses=new Map(facts.occupied_local_slots.map(row=>[row.candidate_id,row.course]));
                for(const v of conflicts?.value||[])line(container,`${[...new Set(v.event_refs.map(ref=>courses.get(ref.candidate_id)||'Class'))].join(' / ')} · ${v.start} – ${v.end}`);
            }

        }
        const windows=result.facts.find(f=>f.type==="verified_available_windows");
        if(complete && windows?.status==="COMPUTED") {
            line(container,windows.value.length?"Available windows within your stated availability":"No feasible window within your stated availability","h4");
            for(const v of windows.value)line(container,`${v.start} – ${v.end}`);
            for(const rec of result.recommendations.filter(r=>r.window))line(container,`Study/project option: ${rec.window.start} – ${rec.window.end}`);
            line(container,"Study options use a fixed 60-minute duration and a 15-minute class buffer; they are deterministic suggestions, not AI advice.");
        } else line(container,"Study windows are unavailable until independent whole-schedule verification and explicit personal availability are supplied.");
        const advanced=document.createElement("details");line(advanced,"Advanced Details","summary");const body=document.createElement("div");render(result,body);advanced.append(body);container.append(advanced);
    }
    function renderObserved(result,container) {
        container.replaceChildren();
        const f=result.student_facts;
        line(container,'Your observed workload','h3');
        line(container,'Based on detected classes and any saved corrections you have connected. These are printed class times, not a confirmed complete schedule.');
        const metrics=document.createElement('div');metrics.className='workload-summary';
        for(const [value,label] of [[f.class_count,'classes counted'],[f.printed_class_hours==null?'—':Number(f.printed_class_hours.toFixed(2)),'total observed class hours']]){
            const card=document.createElement('div');line(card,String(value),'strong');line(card,label,'span');metrics.append(card);
        }container.append(metrics);
        if(f.human_corrected_class_count)line(container,`${f.human_corrected_class_count} human-corrected class(es) included. Human correction does not establish independent verification.`);
        if(!f.class_count)line(container,'No readable class times could be counted. Review the detected classes and correct missing day or time details.');
        if(Object.keys(f.hours_by_day).length){
            const table=document.createElement('table');table.className='workload-table';const head=document.createElement('tr');line(head,'Day','th');line(head,'Observed class hours','th');table.append(head);
            for(const [day,hours] of Object.entries(f.hours_by_day)){const row=document.createElement('tr');line(row,day,'td');line(row,String(Number(hours.toFixed(2))),'td');table.append(row);}container.append(table);
            line(container,`Busiest observed: ${f.busiest_observed_days.join(', ')}`);
            line(container,`Lightest observed active days: ${f.lightest_observed_active_days.join(', ')}. Unlisted days are not confirmed free.`);
        }
        if(f.excluded_unresolved_candidates.length)line(container,`${f.excluded_unresolved_candidates.length} class(es) need a readable identity, day and time before they can be counted. Review these against the original.`);
        if(f.possible_duplicates.length)line(container,`${f.possible_duplicates.length} exact duplicate reading(s) counted once. Check whether these represent different real classes.`);
        if(f.uncertain_details_class_count)line(container,`${f.uncertain_details_class_count} class(es) have uncertain details. Their supported day and times are counted; check the remaining details in Review Classes.`);
        if(f.undated_class_count)line(container,`${f.undated_class_count} class(es) have a weekday but no confirmed date. Their printed slots are counted once; no teaching weeks were inferred.`);
        if(result.source_issues)line(container,'Some parts of the source could not be read reliably. Check the original for missing classes.');
        const occupied=document.createElement('details');line(occupied,'Occupied class times','summary');
        for(const row of f.occupied_local_slots)line(occupied,`${row.day} · ${row.start}–${row.end}${row.end_day_offset?' (ends next day)':''} · ${row.course}${row.location?' · '+row.location:''}${row.lecturer?' · '+row.lecturer:''}`);container.append(occupied);
        line(container,'Possible timetable conflicts','h4');
        if(!f.possible_overlaps.length)line(container,'No overlaps found among comparable detected class times. This does not rule out missing classes.');
        for(const row of f.possible_overlaps)line(container,`${row.day}: ${row.classes.join(' / ')} overlap. Check the original timetable.`);
        line(container,'Study and project time','h4');
        line(container,'Open spaces are not confirmed free time. Study windows need a complete checked schedule, supported dates and recurrence, and your availability. The optional Advanced / Research Verification section provides this stricter workflow; it is not needed for workload summaries.');
        const advanced=document.createElement('details');line(advanced,'Advanced Details','summary');line(advanced,JSON.stringify(result,null,2),'pre');container.append(advanced);
    }
    class Controller {
        constructor({base, getSource, studentMode=false}) { this.studentMode=studentMode; this.base=base; this.getSource=getSource; this.generation=0; this.key=null; }
        invalidate(key, force = false) {
            if (this.key===key && !force) return;
            this.key=key; this.generation++; this.abort?.abort();
            const out=document.getElementById("analysisResult");
            if(out) {out.replaceChildren();line(out,this.studentMode?"Choose Analyze my classes to summarize the current timetable.":"NOT ASSESSED — choose an explicit scope and analyze the current canonical source.");}
        }
        async observed(packet,out,generation,abort) {
            const val=id=>document.getElementById(id)?.value.trim()||'';
            const start=val('analysisStart'),end=val('analysisEnd');
            if(start&&end&&end<start){line(out,'The end date must be on or after the start date. Clear the optional date filter to count all detected classes.');return;}
            line(out,'Adding up your detected class times…');
            const timer=setTimeout(()=>abort.abort(),30000);
            try{
                const response=await fetch(`${this.base}/api/analysis/v1/observed`,{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({packet,...(start?{start}:{}),...(end?{end}:{})}),signal:abort.signal});
                if(!response.ok)throw Error(response.status===422?'The class details or date filter could not be read. Review your timetable and try again.':`Workload analysis is unavailable (HTTP ${response.status}). Your timetable and saved reviews are unchanged.`);
                const result=await response.json();if(generation!==this.generation)return;
                if(result.version!=='fr7-observed-v1'||result.trusted_complete!==false||result.calendar_authorized!==false||!result.student_facts)throw Error('The workload response could not be confirmed. Your timetable is unchanged.');
                const f=result.student_facts;
                for(const [id,value] of [['analysisStart',f.detected_date_range?.start],['analysisEnd',f.detected_date_range?.end],['analysisZone',f.source_timezone]]){const field=document.getElementById(id);if(field&&!field.value&&value)field.value=value;}
                renderObserved(result,out);
            }catch(error){if(generation===this.generation){out.replaceChildren();line(out,error.name==='AbortError'?'Analysis timed out. Your timetable is unchanged; try again.':error.message);}}
            finally{clearTimeout(timer);}
        }
        async run() {
            const out=document.getElementById("analysisResult");
            const source=this.getSource(); const packet=packetFrom(source.upload,source.canonical);
            this.abort?.abort();const abort=this.abort=new AbortController();const generation=++this.generation;
            out.replaceChildren();
            if (!packet || source.sample) {line(out,"Upload a timetable or paste its text first. The sample display has no source-backed class details to analyze.");return;}
            if(this.studentMode)return this.observed(packet,out,generation,abort);
            const val=id=>document.getElementById(id).value.trim();
            if(this.studentMode && !val("analysisScope"))document.getElementById("analysisScope").value=packet.source.timetable_id || `local:${packet.source.source_document_id}`;
            if (!["analysisStart","analysisEnd","analysisZone","analysisScope"].every(id=>val(id))) {line(out,"INSUFFICIENT EVIDENCE — explicit horizon, timezone and timetable scope are required.");return;}
            const body={event_set_id:`application:${packet.source.source_document_id}`,profile:this.studentMode?"m7-student-v1":"m7-development-v1",packet,
                scope:{scope_id:"application-observed-scope",timetable_id:val("analysisScope"),start:val("analysisStart"),end:val("analysisEnd"),timezone:val("analysisZone"),cohort_id:val("analysisCohort")||null},
                declared_kind:"UNVERIFIED_CANDIDATES",context:{}};
            line(out,"Analyzing observed candidates…");
            const timer=setTimeout(()=>abort.abort(),30000);
            try {
                const response=await fetch(`${this.base}/api/analysis/v1/${this.studentMode?"student":"analyze"}`,{method:"POST",credentials:"omit",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:abort.signal});
                if (!response.ok) throw new Error(`Analysis rejected (HTTP ${response.status}). Check explicit scope and canonical evidence.`);
                const result=await response.json();
                if(generation!==this.generation)return;
                if(result.version!=="m7-development-v1" || !Array.isArray(result.facts) || !result.completeness)throw new Error("Unsupported analysis response.");
                this.studentMode?renderStudent(result,out):render(result,out);
            } catch(error) {
                if(generation===this.generation){out.replaceChildren();line(out,error.name==="AbortError"?"NOT ASSESSED — analysis cancelled or timed out.":error.message);}
            } finally {clearTimeout(timer);}
        }
    }
    function renderGrounded(result,container) {
        container.replaceChildren();line(container,'Checked study suggestions','h3');
        const state=result.grounded;
        if(state.status!=='VALIDATED'){
            const reasons={INDEPENDENT_COMPLETE_SCHEDULE_REQUIRED:'An independent reviewer must check the entire timetable first.',
                EXPLICIT_VALID_AVAILABILITY_REQUIRED:'Choose valid availability within the checked timetable range.',
                APPROVED_PROVIDER_CONFIGURATION_REQUIRED:'AI analysis is not configured or approved on this computer. Local facts remain available.',
                EXPLICIT_PROVIDER_CONSENT_REQUIRED:'Your permission is required before sending the minimized summary.',
                COMPLETENESS_REVOKED_DURING_GENERATION:'The timetable verification expired or changed. Check it again.',
                SOURCE_CHANGED_DURING_GENERATION:'The timetable changed while suggestions were being prepared.'};
            line(container,reasons[state.reason]||'The provider response could not be validated. No AI suggestions are shown. Local facts remain available.');
            if(result.completeness.trusted_complete){const total=result.facts.find(f=>f.type==='total_scope_workload_hours');if(total?.status==='COMPUTED')line(container,'Checked class hours: '+total.value);}
            return;
        }
        if(state.output.no_window)line(container,'No suitable study window was found within your declared availability.');
        for(const r of state.output.recommendations)line(container,r.start+' – '+r.end+' · '+r.explanation);
        for(const f of state.output.claims)line(container,f.type.replaceAll('_',' ')+': '+JSON.stringify(f.value));
        line(container,'Optional suggestions based on the checked timetable. This is operational output, not a completed research evaluation.');
    }
    global.STAIRAnalysis={packetFrom,render,renderStudent,renderObserved,renderGrounded,Controller};
})(typeof window!=="undefined"?window:globalThis);
