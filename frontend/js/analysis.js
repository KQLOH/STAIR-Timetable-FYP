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
    class Controller {
        constructor({base, getSource}) { this.base=base; this.getSource=getSource; this.generation=0; this.key=null; }
        invalidate(key, force = false) {
            if (this.key===key && !force) return;
            this.key=key; this.generation++; this.abort?.abort();
            const out=document.getElementById("analysisResult");
            if(out) {out.replaceChildren();line(out,"NOT ASSESSED — choose an explicit scope and analyze the current canonical source.");}
        }
        async run() {
            const out=document.getElementById("analysisResult");
            const source=this.getSource(); const packet=packetFrom(source.upload,source.canonical);
            this.abort?.abort();const abort=this.abort=new AbortController();const generation=++this.generation;
            out.replaceChildren();
            if (!packet || source.sample) {line(out,"NOT ASSESSED — the display demo has no canonical evidence packet. Import a source for development diagnostics.");return;}
            const val=id=>document.getElementById(id).value.trim();
            if (!["analysisStart","analysisEnd","analysisZone","analysisScope"].every(id=>val(id))) {line(out,"INSUFFICIENT EVIDENCE — explicit horizon, timezone and timetable scope are required.");return;}
            const body={event_set_id:`application:${packet.source.source_document_id}`,profile:"m7-development-v1",packet,
                scope:{scope_id:"application-observed-scope",timetable_id:val("analysisScope"),start:val("analysisStart"),end:val("analysisEnd"),timezone:val("analysisZone"),cohort_id:val("analysisCohort")||null},
                declared_kind:"UNVERIFIED_CANDIDATES",context:{}};
            line(out,"Analyzing observed candidates…");
            const timer=setTimeout(()=>abort.abort(),30000);
            try {
                const response=await fetch(`${this.base}/api/analysis/v1/analyze`,{method:"POST",credentials:"omit",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:abort.signal});
                if (!response.ok) throw new Error(`Analysis rejected (HTTP ${response.status}). Check explicit scope and canonical evidence.`);
                const result=await response.json();
                if(generation!==this.generation)return;
                if(result.version!=="m7-development-v1" || !Array.isArray(result.facts) || !result.completeness)throw new Error("Unsupported analysis response.");
                render(result,out);
            } catch(error) {
                if(generation===this.generation){out.replaceChildren();line(out,error.name==="AbortError"?"NOT ASSESSED — analysis cancelled or timed out.":error.message);}
            } finally {clearTimeout(timer);}
        }
    }
    global.STAIRAnalysis={packetFrom,render,Controller};
})(typeof window!=="undefined"?window:globalThis);
