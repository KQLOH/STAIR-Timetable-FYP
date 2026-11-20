/* M5.1 browser integration. All data is ephemeral. No review or calendar mutations. */
(function (global) {
    "use strict";
    const MAX_BYTES = 20 * 1024 * 1024;
    const PIPELINES = Object.freeze(["evidence", "B1", "B2", "B2H-v1"]);
    const TYPES = {png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", pdf: "application/pdf", txt: "text/plain"};
    const HASH = /^[a-f0-9]{64}$/;
    // Preserve the backend's JSON number representation inside its digested envelope.
    // JS stringify would turn e.g. 30.0 into 30, changing Python canonical hashes.
    const responseJSON = new WeakMap();
    const DAY = [null, "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
    const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
    function requireValue(condition, message) { if (!condition) throw new Error(message); }
    function diagnostic(error) {
        return String(error?.message || "Processing failed")
            .replace(/(?:[A-Za-z]:[\\/]|\\\\)[^\s"<>]+/g, "[LOCAL_PATH_REDACTED]").slice(0, 1500);
    }

    function readerConfiguration(reader='general', settings={}) {
        // Normal student selections are complete configurations. Hidden legacy
        // toggles cannot accidentally combine a baseline and a layout sidecar.
        if(reader!=='advanced') {
            requireValue(['general','colored','grid'].includes(reader), 'Choose a supported timetable reader.');
            return {pipeline:'evidence',reconstruct:true,layout:reader==='general',
                blockGrid:reader==='colored',ruledGrid:reader==='grid',automatic:false,
                contextCorrection:reader==='colored' && settings.contextCorrection!==false};
        }
        const pipeline=PIPELINES.includes(settings.pipeline)?settings.pipeline:'evidence';
        const application=pipeline==='evidence',blockGrid=application && settings.blockGrid===true;
        const ruledGrid=application && !blockGrid && settings.ruledGrid===true;
        return {pipeline,reconstruct:settings.reconstruct!==false,layout:settings.layout===true,
            blockGrid,ruledGrid,automatic:application && !blockGrid && !ruledGrid && settings.automatic===true,
            contextCorrection:blockGrid && settings.contextCorrection!==false || application && !blockGrid && !ruledGrid && settings.automatic===true && settings.contextCorrection!==false};
    }
    function friendlyFailure(error) {
        const message=String(error?.message || '');
        if(message.includes('AUTO_MIXED_LAYOUTS'))return 'This PDF contains different page layouts. Upload its pages separately.';
        if(message.includes('AUTO_PAGE_CAP'))return 'Upload at most four pages at a time, or split this PDF into smaller files.';
        if(message.includes('OCR_UNAVAILABLE'))return 'Text recognition is unavailable on this computer. Check the OCR setup or use a PDF with selectable text.';
        if(message.includes('INVALID_PDF'))return 'This PDF could not be opened. Try another copy or export it as an image.';
        return 'We couldn’t read this file. Try another copy or a clearer image with the full timetable and headers visible.';
    }
    const MAX_TEXT_BYTES = 128 * 1024, MAX_TEXT_LINES = 2048;
    function validateCopiedText(text) {
        requireValue(typeof text === 'string' && text.trim(), 'Paste some timetable text first.');
        requireValue(new TextEncoder().encode(text).length <= MAX_TEXT_BYTES, 'Use at most 128 KiB of timetable text at a time.');
        requireValue(text.split(/\r\n|\r|\n/).length <= MAX_TEXT_LINES, 'Use at most 2048 lines of timetable text at a time.');
        requireValue(!/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text), 'Remove unsupported control characters from the timetable text.');
        return text;
    }
    function copiedTextFile(text) {
        validateCopiedText(text);
        return new File([text], 'copied-timetable.txt', {type:'text/plain'});
    }
    async function validateFile(file) {
        const extension = String(file?.name || "").split(".").pop().toLowerCase();
        const mime = TYPES[extension];
        requireValue(mime, "Unsupported file: choose PNG, JPG/JPEG, PDF or plain text.");
        requireValue(file.size > 0 && file.size <= MAX_BYTES, "File must be nonempty and at most 20 MiB.");
        requireValue(!file.type || file.type === mime, "File extension and MIME type disagree.");
        if (mime === 'text/plain') {
            requireValue(file.size <= MAX_TEXT_BYTES, 'Use at most 128 KiB of timetable text at a time.');
            let text;
            try { text = new TextDecoder('utf-8', {fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer()); }
            catch { throw Error('Use UTF-8 encoded timetable text.'); }
            validateCopiedText(text);
            return {mime, filename:'copied-timetable.txt', text};
        }
        const bytes = new Uint8Array(await file.slice(0, 8).arrayBuffer());
        const signature = extension === "png" ? [137,80,78,71,13,10,26,10] : extension === "pdf" ? [37,80,68,70,45] : [255,216,255];
        requireValue(signature.every((value, index) => bytes[index] === value), "File signature does not match its extension.");
        // HTTP headers require ASCII. Retain the exact original name in the local UI only.
        const filename = String(file.name).split(/[\\/]/).pop().replace(/[^\x20-\x7e]|["\r\n]/g, "_").slice(-160);
        return {mime, filename};
    }

    function validatePacket(packet) {
        requireValue(record(packet) && packet.schema_version === "m1.0" && record(packet.source), "Invalid M1 evidence packet.");
        requireValue(typeof packet.source.source_document_id === "string" && HASH.test(packet.source.content_sha256), "Missing source identity or hash.");
        requireValue(["ocr", "native_pdf", "text", "tarumt_html", "authority"].includes(packet.source.route), "Unsupported source evidence route.");
        requireValue(Array.isArray(packet.evidence) && Array.isArray(packet.events) && Array.isArray(packet.issues), "Missing packet evidence/events/issues.");
        const ids = new Set();
        for (const span of packet.evidence) {
            requireValue(record(span) && typeof span.evidence_id === "string" && typeof span.raw_text === "string" &&
                span.source_document_id === packet.source.source_document_id && !ids.has(span.evidence_id), "Invalid evidence provenance.");
            ids.add(span.evidence_id);
            if (span.bbox) requireValue(["PIXELS","PDF_POINTS","NORMALIZED"].includes(span.bbox.coordinate_space) &&
                ["x0","y0","x1","y1"].every(k => Number.isFinite(span.bbox[k])) &&
                span.bbox.x1 >= span.bbox.x0 && span.bbox.y1 >= span.bbox.y0, "Invalid evidence coordinates.");
            requireValue(span.confidence == null || Number.isFinite(span.confidence) && span.confidence >= 0 && span.confidence <= 1, "Invalid OCR confidence.");
        }
        const candidates = new Set();
        for (const event of packet.events) {
            requireValue(record(event) && typeof event.candidate_id === "string" && !candidates.has(event.candidate_id) &&
                record(event.revision) && record(event.revision.content) && Array.isArray(event.revision.observations), "Invalid candidate contract.");
            candidates.add(event.candidate_id);
            requireValue(event.revision.source_document_id === packet.source.source_document_id, "Candidate source mismatch.");
            for (const field of event.revision.observations) requireValue(
                ["PRESENT","MISSING","SOURCE_ABSENT","UNRESOLVED","CONFLICTING","NOT_APPLICABLE"].includes(field.state) &&
                Array.isArray(field.evidence_ids) && field.evidence_ids.every(id => ids.has(id)), "Invalid field state or evidence link.");
        }
        return packet;
    }

    function validateExtraction(payload, pipeline) {
        requireValue(payload?.success === true && payload.pipeline === pipeline, "Unexpected extraction response or pipeline.");
        const packet = validatePacket(payload.data);
        requireValue(pipeline === "evidence" || packet.source.route === "ocr" && packet.evidence.every(s => s.route === "ocr"), "Baseline response substituted a non-OCR route.");
        requireValue(pipeline !== "evidence" || packet.events.length === 0, "Evidence-only response unexpectedly contains baseline events.");
        return packet;
    }
    function validateReconstruction(envelope, profile = "m4-development-v1") {
        if (envelope?.canonical_output === null) {
            requireValue(envelope.status === "EXECUTION_ABORTED" && envelope.scorable === false, "Invalid aborted reconstruction.");
            return null;
        }
        const output = envelope?.canonical_output;
        requireValue(["m4-development-v1","ocr-layout-m52-v1","ocr-ruled-grid-s2a-v1","ocr-block-grid-s2b-v1"].includes(profile) && record(output) && HASH.test(envelope.canonical_digest) && output.profile_id === profile &&
            output.decoder_mode === "constrained" && output.method_id === (profile === "ocr-block-grid-s2b-v1" ? "P1-app-s2b-v1" : profile === "ocr-ruled-grid-s2a-v1" ? "P1-app-s2a-v1" : profile === "ocr-layout-m52-v1" ? "P1-layout-m52-v1" : "P1-m4-v1") && output.scorable === false &&
            ["COMPLETE_FINITE_SEARCH","SEARCH_INCOMPLETE"].includes(output.status) &&
            Array.isArray(output.unresolved_candidates) && Array.isArray(output.findings) && Array.isArray(output.components), "Invalid constrained development reconstruction response.");
        validatePacket(output.packet);
        validatePacket({...output.packet, events: [...output.packet.events, ...output.unresolved_candidates]});
        return output;
    }
    function calendarBundle(packet,reconstructionStatus='NOT_PROVIDED',decisions=[]) {
        // Public policy records intentionally omit full findings. Carry only
        // explicitly non-authoritative, revision-bound routing diagnostics.
        const routing=[],registered=[];
        for(const event of packet.events)for(const d of decisions || []){
            if(registeredDecision(event,d)){registered.push(Object.fromEntries(['decision_id','candidate_id','event_id','revision_id','revision_digest','registration_id','registration_signature'].map(k=>[k,d[k]])));continue;}
            if(d?.candidate_id!==event.candidate_id || d.revision_id!==event.revision.revision_id || d.event_id!==event.identity.event_id || !HASH.test(d.revision_digest || '') || !['REVIEW','ABSTAIN'].includes(d.decision) || d.registered_routing_eligible!==false || !['m5-development-v1','m5-source-review-v1'].includes(d.policy_profile))continue;
            routing.push({candidate_id:d.candidate_id,event_id:d.event_id,revision_id:d.revision_id,revision_digest:d.revision_digest,route:d.decision,policy_profile:d.policy_profile,registered_accept_eligible:false});
        }
        return {packet,reconstruction_status:reconstructionStatus,context:{},assessments:[],...(routing.length?{routing_diagnostics:routing}:{}),...(registered.length?{registered_assessment_refs:registered}:{})};
    }
    function calendarBundleFromResult(result) {
        const output=result.reconstruction?.canonical_output;
        const packet=output?{...output.packet,events:[...output.packet.events,...output.unresolved_candidates]}:result.extraction;
        return calendarBundle(packet,(output?.profile_id==='tarumt-structured-review-v1'?'NOT_PROVIDED':output?.status) || (result.stages?.reconstruction==='EXECUTION_ABORTED'?'EXECUTION_ABORTED':'NOT_PROVIDED'),result.policy);
    }
    function registeredDecision(event,decision){
        return decision?.policy_profile==='m5-registered-v1' && ['AUTHENTIC','TEST_ONLY'].includes(decision.registration_mode) &&
            ['registration_id','registration_signature','registration_source_digest','registration_evidence_digest','revision_digest'].every(k=>HASH.test(decision[k] || '')) &&
            ['ACCEPT','REVIEW','ABSTAIN'].includes(decision.decision) && decision.registered_routing_eligible===(decision.decision==='ACCEPT') &&
            decision.candidate_id===event?.candidate_id && decision.event_id===event?.identity?.event_id && decision.revision_id===event?.revision?.revision_id;
    }
    function validatePolicy(payload, events) {
        if(payload?.profile==='m5-registered-v1'){
            requireValue(payload.registered_routing_ready===true && Array.isArray(payload.decisions) && payload.decisions.length>0 &&
                payload.decisions.every(d=>registeredDecision(events.find(e=>e.candidate_id===d.candidate_id),d) && d.calibrated_probability==null),'Invalid registered policy receipt.');
            return payload.decisions;
        }
        requireValue(["m5-development-v1","m5-source-review-v1"].includes(payload?.profile) && payload.registered_routing_ready === false &&
            Array.isArray(payload.decisions) && payload.decisions.length > 0, "Invalid M5 development assessment response.");
        for (const decision of payload.decisions) {
            requireValue(["REVIEW", "ABSTAIN"].includes(decision.decision) && decision.registered_routing_eligible === false &&
                decision.calibrated_probability == null && Array.isArray(decision.reason_codes), "Unexpected policy promotion: development ACCEPT is disabled.");
            if (decision.candidate_id != null) requireValue(events.some(e => e.candidate_id === decision.candidate_id &&
                e.revision.revision_id === decision.revision_id), "Policy refers to an unknown or stale candidate.");
            if(payload.profile==='m5-source-review-v1')requireValue(decision.source_routing?.profile==='m5-source-review-v1' && decision.source_routing.route===decision.decision && decision.source_routing.revision_id===decision.revision_id && decision.source_routing.revision_digest===decision.revision_digest && decision.source_routing.registered_accept_eligible===false && decision.source_routing.calibrated_probability==null,'Invalid or stale source-review assessment.');
        }
        return payload.decisions;
    }

    class Controller {
        constructor({base = "http://127.0.0.1:8000", fetcher = global.fetch.bind(global), notify = () => {}} = {}) {
            this.base = base; this.fetcher = fetcher; this.notify = notify;
            this.sequence = 0; this.abortController = null; this.objectUrl = null; this.result = null;
        }
        releasePreview() { if (this.objectUrl) global.URL.revokeObjectURL(this.objectUrl); this.objectUrl = null; }
        cancel({notify = true} = {}) {
            this.sequence++;
            this.abortController?.abort(); this.abortController = null;
            if (this.result?.busy) { this.result.busy = false; this.result.status = "CANCELLED — no success claimed"; if (notify) this.notify(this.result); }
        }
        reset(options) { this.cancel(options); this.releasePreview(); this.result = null; }
        async request(path, options, signal, limit = 2 * 1024 * 1024) {
            if (typeof options.body === "string") requireValue(new TextEncoder().encode(options.body).length <= limit, "Request exceeds backend JSON limit; original evidence retained.");
            const child = new AbortController();
            const onAbort = () => child.abort();
            signal.addEventListener("abort", onAbort, {once:true});
            if (signal.aborted) child.abort();
            let timedOut = false;
            const timer = setTimeout(() => { timedOut = true; child.abort(); }, 90000);
            try {
                const response = await this.fetcher(this.base + path, {...options, signal:child.signal, credentials:"omit"});
                requireValue(response.headers?.get("content-type")?.includes("application/json"), `Backend returned non-JSON (HTTP ${response.status}).`);
                const text = await response.text();
                requireValue(text.length <= 12 * 1024 * 1024, "Response exceeds browser display limit.");
                let payload;
                try { payload = JSON.parse(text); } catch (_) { throw new Error("Backend returned malformed JSON."); }
                if (!response.ok) {
                    const detail = payload.detail;
                    throw new Error(`HTTP ${response.status}: ${typeof detail === "string" ? detail : [detail?.code, detail?.diagnostic_code, detail?.message].filter(Boolean).join(" · ") || "Request rejected"}`);
                }
                if (record(payload)) responseJSON.set(payload, text);
                return payload;
            } catch (error) { if (timedOut) throw new Error("Request timed out; no success assumed. Retry explicitly."); throw error; }
            finally { clearTimeout(timer); signal.removeEventListener("abort", onAbort); }
        }
        async run(file, pipeline, {reconstruct = true, layout = false, ruledGrid = false, blockGrid = false, contextCorrection = false, automatic = false} = {}) {
            // Reject an invalid replacement before discarding source or review work.
            if (this.result) {
                const previousSequence = this.sequence;
                await validateFile(file);
                if (previousSequence !== this.sequence) return this.result;
            }
            const previousResult=this.result, previousUrl=this.objectUrl;
            const canRestore=previousResult && !previousResult.busy;
            this.cancel();this.objectUrl=null;this.result=null;
            const sequence = this.sequence;
            if(previousResult)delete previousResult.replacementError;
            const controller = new AbortController(); this.abortController = controller;
            const signal = controller.signal;
            const result = this.result = {filename:file.name, size:file.size, busy:true, status:"Validating file…", pipeline,
                extraction:null, reconstruction:null, policy:null, calendar:null, stages:{}, diagnostics:[], file};
            const emit = status => { if (sequence !== this.sequence) return false; result.status = status; this.notify(result); return true; };
            emit(result.status);
            try {
                requireValue(PIPELINES.includes(pipeline), "Unknown case-sensitive pipeline identifier.");
                requireValue(!ruledGrid || pipeline === "evidence", "S2a requires the evidence pipeline; B1/B2/B2H inputs remain unchanged.");
                requireValue(!(ruledGrid && blockGrid), "Choose one opt-in layout profile: S2a or S2b.");
                requireValue(!blockGrid || pipeline === "evidence", "S2b requires the evidence pipeline; B1/B2/B2H inputs remain unchanged.");
                const {mime, filename, text} = await validateFile(file);
                if (mime === 'text/plain') {
                    requireValue(pipeline === 'evidence', 'Copied text uses the application reader, not an OCR baseline.');
                    layout = ruledGrid = blockGrid = contextCorrection = automatic = false;
                    result.sourceText = text;
                }
                if (sequence !== this.sequence) return result;
                this.objectUrl = global.URL.createObjectURL(file); result.previewUrl = this.objectUrl; result.mime = mime;
                emit(`Extracting ${pipeline} evidence…`);
                automatic = automatic && pipeline === 'evidence' && !blockGrid && !ruledGrid;
                let selectedLayoutProfile = blockGrid ? "ocr-block-grid-s2b-v1" : ruledGrid ? "ocr-ruled-grid-s2a-v1" : null;
                const extracted = await this.request(`/api/extract?pipeline=${encodeURIComponent(pipeline)}` + (automatic ? "&layout_profile=auto" : selectedLayoutProfile ? "&layout_profile=" + selectedLayoutProfile : ""), {
                    method:"POST", headers:{"Content-Type":mime, "X-Filename":filename}, body:file}, signal);
                result.extraction = validateExtraction(extracted, pipeline);
                if (automatic) {
                    requireValue(extracted.reader_selection === 'measured-structure-v1' &&
                        [null, 'ocr-block-grid-s2b-v1', 'ocr-ruled-grid-s2a-v1'].includes(extracted.layout_profile), 'Invalid automatic reader selection.');
                    selectedLayoutProfile = extracted.layout_profile;
                    blockGrid = selectedLayoutProfile === 'ocr-block-grid-s2b-v1';
                    ruledGrid = selectedLayoutProfile === 'ocr-ruled-grid-s2a-v1';
                }
                if (selectedLayoutProfile) {
                    requireValue(extracted.layout_profile === selectedLayoutProfile && extracted.layout_evidence?.schema_version === (blockGrid ? "s2b.1" : "s2a.1"), "Missing versioned supplementary layout evidence.");
                    result.layoutEvidence = extracted.layout_evidence;
                    if(blockGrid && contextCorrection)result.layoutEvidence={...result.layoutEvidence,correction_mode:'context-v1'};
                }
                if (sequence !== this.sequence) return result;
                result.stages.extraction = result.extraction.events.length ? "CANDIDATES_RETURNED" : "NO_CANDIDATES";
                emit("Evidence extracted; reconstruction pending…");
                if (reconstruct) {
                    try {
                        result.reconstructionProfile = blockGrid ? "ocr-block-grid-s2b-v1" : ruledGrid ? "ocr-ruled-grid-s2a-v1" : layout && result.extraction.source.route === "ocr" ? "ocr-layout-m52-v1" : "m4-development-v1";
                        if (layout && result.extraction.source.route !== "ocr") result.diagnostics.push("OCR layout adapter not applicable to this source route; original M4 path used.");
                        result.reconstruction = await this.request("/api/reconstruct", {method:"POST", headers:{"Content-Type":"application/json"},
                            body:JSON.stringify({packet:result.extraction, decoder_mode:"constrained", profile:result.reconstructionProfile, context:{},
                                ...(selectedLayoutProfile ? {layout_evidence:result.layoutEvidence} : {})})}, signal, 1024*1024);
                        if (sequence !== this.sequence) return result;
                        const output = validateReconstruction(result.reconstruction,result.reconstructionProfile);
                        result.stages.reconstruction = output?.status || "EXECUTION_ABORTED";
                    } catch (error) {
                        if (signal.aborted || sequence !== this.sequence) return result;
                        result.reconstruction = null; result.stages.reconstruction = "FAILED";
                        result.diagnostics.push("P1: " + diagnostic(error));
                    }
                } else result.stages.reconstruction = "NOT_REQUESTED";
                if (sequence !== this.sequence) return result;
                emit("Assessing development policy and read-only calendar safety…");
                if (result.reconstruction) {
                    try {
                        const response = await this.request(blockGrid ? "/api/layout/s2b/assess" : ruledGrid ? "/api/layout/s2a/assess" : result.reconstructionProfile === "ocr-layout-m52-v1" ? "/api/layout/assess" : "/api/policy/assess", {method:"POST", headers:{"Content-Type":"application/json"},
                            body:blockGrid ? JSON.stringify({source_packet:result.extraction,layout_evidence:result.layoutEvidence,reconstruction:{canonical_digest:result.reconstruction.canonical_digest},policy_profile:'m5-source-review-v1'}) : assessmentBody(result.extraction, result.reconstruction, result.layoutEvidence,'m5-source-review-v1')}, signal);
                        if (sequence !== this.sequence) return result;
                        const output = result.reconstruction.canonical_output;
                        result.policyProfile=response.profile;result.routingReport=response.routing_report;
                        result.policy = validatePolicy(response, output ? [...output.packet.events, ...output.unresolved_candidates] : []);
                        result.stages.policy = "DEVELOPMENT_ASSESSED";
                    } catch (error) {
                        if (signal.aborted || sequence !== this.sequence) return result;
                        result.stages.policy = "NOT_ASSESSED"; result.diagnostics.push("M5: " + diagnostic(error));
                    }
                } else result.stages.policy = "NOT_ASSESSED";
                const output = result.reconstruction?.canonical_output;
                const bundle=calendarBundleFromResult(result);
                try {
                    if(bundle.packet.evidence.length>4096)throw new Error('Calendar diagnostic evidence limit (4096 spans) exceeded; calendar remains blocked.');
                    const eligibility = await this.request("/api/calendar/v1/eligibility", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(bundle)}, signal);
                    if (sequence !== this.sequence) return result;
                    requireValue(eligibility.policy_status === "BLOCKED" && eligibility.external_calendar === false && Array.isArray(eligibility.events) &&
                        eligibility.events.length === bundle.packet.events.length && eligibility.events.every(e => e.status === "BLOCKED" && e.actual_write_eligibility === "BLOCKED" && e.sandbox_write_permitted === false), "Unexpected calendar eligibility response.");
                    result.calendar = {eligibility, preview:null};
                    const preview = await this.request("/api/calendar/v1/preview", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({bundle})}, signal);
                    if (sequence !== this.sequence) return result;
                    requireValue(preview.write_authorized === false && preview.snapshot_trust === "CALLER_SUPPLIED_DIAGNOSTIC_ONLY" &&
                        record(preview.preview) && preview.preview.scope === "DEVELOPMENT_BLOCKED" && preview.preview.external_calendar === false &&
                        HASH.test(preview.preview.preview_digest) && Array.isArray(preview.preview.operations), "Invalid read-only calendar preview.");
                    result.calendar.preview = preview; result.stages.calendar = "BLOCKED_READ_ONLY";
                } catch (error) {
                    if (signal.aborted || sequence !== this.sequence) return result;
                    result.stages.calendar = "BLOCKED_DIAGNOSTICS_UNAVAILABLE"; result.diagnostics.push("M6: " + diagnostic(error));
                }
                emit(result.diagnostics.length ? "Processing finished with limitations — inspect diagnostics" : "Processing finished — candidates are not verified or calendar-approved");
            } catch (error) {
                if (sequence !== this.sequence) return result;
                result.stages.extraction = "FAILED"; result.failureMessage=friendlyFailure(error);result.diagnostics.push(diagnostic(error)); emit("PROCESSING FAILED — no successful timetable claimed");
            } finally {
                if (sequence === this.sequence) {
                    result.busy = false; this.abortController = null;
                    if(canRestore && (result.stages.extraction==='FAILED' || result.status?.includes('CANCELLED'))) {
                        this.releasePreview();this.objectUrl=previousUrl;this.result=previousResult;
                        previousResult.replacementError=result.diagnostics.join(' · ') || 'Replacement cancelled';previousResult.replacementMessage=result.failureMessage;
                        this.notify(previousResult);
                    } else {if(previousUrl)global.URL.revokeObjectURL(previousUrl);this.notify(result);}
                } else if(previousUrl)global.URL.revokeObjectURL(previousUrl);
            }
            return sequence===this.sequence?this.result:result;
        }
    }

    function eventView(event, layer) {
        const content = event.revision.content;
        const required = ["session_type", "start_time", "end_time", "location"];
        const complete = Boolean(content.course_code || content.course_name) && required.every(k => content[k] != null) && (content.date != null || content.day_of_week != null) &&
            !event.revision.observations.some(f => ["UNRESOLVED","CONFLICTING"].includes(f.state));
        return {id:event.candidate_id, subject:content.course_code || content.course_name || "Unresolved course", subjectName:content.course_name || "",
            day:DAY[content.day_of_week], date:content.date || "Unresolved date", start:content.start_time || "?", end:content.end_time || "?",
            venue:content.location || "Unresolved venue", type:content.session_type || "Unresolved type", source:layer,
            lecturer:content.lecturer || "", section:content.section || "", status:"review", completeCandidate:complete, selected:false, canonical:event};
    }
    function boundRouting(event, decision){
        if(registeredDecision(event,decision))return {route:decision.decision,support:'REGISTERED',registrationMode:decision.registration_mode,
            explanations:decision.explanations || [{code:'REGISTERED_'+decision.decision,message:'Frozen policy assessed this current class.',action:'Calendar approval and independent verification remain separate.'}]};
        const routing=decision?.source_routing;
        return routing && ['REVIEW','ABSTAIN'].includes(routing.route) && routing.profile==='m5-source-review-v1' && routing.registered_accept_eligible===false && routing.calibrated_probability==null && decision.candidate_id===event.candidate_id && decision.revision_id===event.revision.revision_id && routing.revision_id===event.revision.revision_id && routing.revision_digest===decision.revision_digest ? routing : null;
    }
    function routingSummary(events,result){
        const views=uniqueEvents(events),counts={ACCEPT:0,REVIEW:0,ABSTAIN:0,NOT_ASSESSED:0,REGISTERED:0,TEST_ONLY:0};
        for(const view of views){const d=view.reviewTask?.decision || result?.policy?.find(d=>d.candidate_id===view.canonical?.candidate_id);const route=boundRouting(view.canonical,d);counts[route?.route || 'NOT_ASSESSED']++;if(route?.support==='REGISTERED')counts.REGISTERED++;if(route?.registrationMode==='TEST_ONLY')counts.TEST_ONLY++;}
        return {total:views.length,review:counts.REVIEW,abstain:counts.ABSTAIN,unassessed:counts.NOT_ASSESSED,accept:counts.ACCEPT,registered:counts.REGISTERED,testOnly:counts.TEST_ONLY,automaticCoverage:views.length?counts.ACCEPT/views.length:null,accuracy:null,unsafeAcceptance:null};
    }
    function renderRoutingSummary(events,result){
        const target=global.document.getElementById('sourceRoutingSummary');if(!target)return;
        const m=routingSummary(events,result),fraction=n=>m.total?`${n} / ${m.total} (${Math.round(100*n/m.total)}%)`:'No candidates';
        if(m.registered){target.textContent=`${m.testOnly?'TEST ONLY · ':''}Policy routing: ACCEPT ${fraction(m.accept)} · REVIEW ${fraction(m.review)} · ABSTAIN ${fraction(m.abstain)} · Not assessed ${m.unassessed}. Calendar approval and independent verification remain separate. These counts are not measured accuracy.`;return;}
        target.textContent=`Source-review routing: REVIEW ${fraction(m.review)} · ABSTAIN ${fraction(m.abstain)} · Not assessed ${m.unassessed}. Automatic coverage: ${m.total?'0%':'not defined (no candidates)'}. Registered ACCEPT is unavailable. These counts are not accuracy or selective-safety results; accuracy and unsafe acceptance have not been measured.`;
    }
    function eventIdentity(event) {
        return event?.candidate_id && event?.identity?.event_id ? JSON.stringify([event.candidate_id,event.identity.event_id]) : null;
    }
    function humanCorrected(event) {
        return ['CORRECTED','APPROVED'].includes(event?.human_review?.status) && event.human_review.revision_id===event.revision.revision_id;
    }
    function independentlyVerified(event) {
        const v=event?.human_verification;
        return v?.status==='INDEPENDENTLY_VERIFIED' && v.revision_id===event?.revision?.revision_id && !!v.actor_id && !!v.recorded_at && !!v.evidence_record_id;
    }
    function needsStudentReview(view) {
        if(view.routing?.route==='ABSTAIN')return true;
        if(view.routing?.route==='REVIEW' && !humanCorrected(view.canonical) && view.canonical?.human_review?.status!=='APPROVED')return true;
        if(!view.completeCandidate)return true;
        const task=view.reviewTask,event=view.canonical;
        if(['REVALIDATION_REQUIRED','SUBMITTED_CORRECTION','ESCALATED'].includes(task?.state))return true;
        if(humanCorrected(event) && (event.validation?.revision_id!==event.revision.revision_id ||
            ['NOT_ASSESSED','FAILED'].includes(event.validation?.status)))return true;
        // Missing calibration, independent verification and calendar approval
        // are separate gates, not evidence of an outstanding field correction.
        return false;
    }
    function mergeSavedViews(automatic,snapshot) {
        const latest=new Map(),tasks=new Map((snapshot.tasks || []).filter(t=>t.current_event).map(t=>[eventIdentity(t.current_event),t]));
        for(const event of snapshot.packet.events) {
            const key=eventIdentity(event);requireValue(key && !latest.has(key),'Saved event identities are missing or duplicated.');latest.set(key,event);
        }
        return automatic.map(view=>{
            const key=eventIdentity(view.canonical),event=latest.get(key);
            if(!event)return view;
            requireValue(event.revision.source_document_id===view.canonical.revision.source_document_id,'Saved event source differs.');
            return {...eventView(event,view.source),methodLabel:event.revision.method_id,reviewTask:tasks.get(key),routing:boundRouting(event,tasks.get(key)?.decision),savedReview:true};
        });
    }
    function assessmentBody(packet, reconstruction, layoutEvidence, policyProfile='m5-development-v1') {
        const raw = responseJSON.get(reconstruction);
        requireValue(raw, "Original reconstruction JSON unavailable; do not re-encode a digested envelope.");
        return '{"source_packet":' + JSON.stringify(packet) + ',"reconstruction":' + raw +
            ',"decoder_mode":"constrained","reconstruction_context":{},"policy_profile":'+JSON.stringify(policyProfile) +
            (layoutEvidence ? ',"layout_evidence":' + JSON.stringify(layoutEvidence) : '') + '}';
    }
    function reviewIngestBody(result) {
        requireValue(result?.extraction && result?.reconstruction, 'Review admission requires an original source packet and reconstruction.');
        if(result.pipeline==='tarumt-structured-review-v1') {
            requireValue(typeof result.reconstruction_json==='string' && JSON.stringify(JSON.parse(result.reconstruction_json))===JSON.stringify(result.reconstruction),'Original structured assessment JSON unavailable.');
            return '{"source_packet":'+JSON.stringify(result.extraction)+',"reconstruction":'+result.reconstruction_json+',"policy_profile":"m5-source-review-v1"}';
        }
        return assessmentBody(result.extraction,result.reconstruction,result.layoutEvidence,result.policyProfile || 'm5-development-v1');
    }
    function displayedEvents(result) {
        const output = result?.reconstruction?.canonical_output;
        const label = sourceLabel(result?.extraction?.source);
        return uniqueEvents(output ? [...output.packet.events, ...output.unresolved_candidates].map(e => ({...eventView(e, label),
            methodLabel:output.profile_id,routing:boundRouting(e,result.policy?.find(d=>d.candidate_id===e.candidate_id)), partialProjection:output.unresolved_candidates.some(p=>p.candidate_id===e.candidate_id)})) :
            (result?.extraction?.events || []).map(e => ({...eventView(e,label),methodLabel:result.pipeline})));
    }
    function sourceLabel(source, {sample = false} = {}) {
        if (sample && source == null) return "Synthetic demonstration sample";
        if (!record(source) || typeof source.source_document_id !== "string" || !HASH.test(source.content_sha256)) return "Unknown source";
        if (source.route === "tarumt_html" && source.media_type === "text/html") return "TAR UMT structured source";
        if (source.route === "native_pdf" && source.media_type === "application/pdf") return "Native PDF extraction";
        if (source.route === "ocr" && source.media_type === "application/pdf") return "PDF OCR extraction";
        if (source.route === "ocr" && ["image/png","image/jpeg"].includes(source.media_type)) return "OCR image upload";
        if (source.route === "text" && source.media_type === "text/plain") return "Copied timetable text";
        return "Unknown source";
    }
    function node(tag, text, className) {
        const element = global.document.createElement(tag);
        if (text != null) element.textContent = String(text);
        if (className) element.className = className;
        return element;
    }
    function details(title, value) {
        const box = node("details");box.append(node("summary", title), node("pre", typeof value === "string" ? value : JSON.stringify(value,null,2)));return box;
    }
    function sourceSpans(result) {
        const original=result.extraction?.evidence || [];
        if (!result.layoutEvidence) return original;
        const aliases=new Set(Object.values(result.reconstruction?.execution_metadata?.capture_aliases || {}));
        const extra=[...(result.reconstruction?.canonical_output?.packet?.evidence || []),...(result.reviewEvidence || [])];
        return [...original,...extra.filter(s=>!aliases.has(s.evidence_id) && !original.some(o=>o.evidence_id===s.evidence_id))];
    }
    function renderEvidence(result) {
        const container = global.document.getElementById("extractionEvidence"); if (!container) return;
        container.replaceChildren();
        container.append(node("h3", "Evidence and processing stages"),node("p", result.reviewContextNote || "Raw extraction, baseline candidates, P1, policy and calendar states are separate. Trusted-host corrections are unavailable."));
        container.append(details("Stage results and failures", {stages:result.stages, diagnostics:result.diagnostics}));
        if (result.layoutEvidence) container.append(details(result.layoutEvidence.profile === "ocr-block-grid-s2b-v1" ? "S2b supplementary words, colored regions and transforms (application only)" : "S2a supplementary word and pixel-grid evidence (application only)", result.layoutEvidence));
        if (result.layoutEvidence?.profile === "ocr-block-grid-s2b-v1") {
            const output=result.reconstruction?.canonical_output;
            const events=[...(output?.packet?.events || []),...(output?.unresolved_candidates || [])];
            const inspection=node("details");inspection.append(node("summary","Detected course blocks and OCR readings"));
            for (const page of result.layoutEvidence.pages) for (const region of page.regions) {
                const words=page.words.filter(w=>w.region_id===region.region_id);
                const ids=new Set(words.map(w=>w.evidence_id));
                const candidates=events.filter(e=>e.revision.observations.some(o=>o.evidence_ids.some(id=>ids.has(id))));
                const readings={};
                for (const w of words) (readings[w.capture_route] ||= []).push(w.text);
                const entry=node("div",null,"evidence-entry");entry.dataset.evidenceId=region.region_id;
                entry.append(node("strong",`Page ${page.page} · ${region.boundary_status}`),
                    details("Block OCR, original coordinates and candidate fields",{bbox:region.bbox,
                        readings:Object.fromEntries(Object.entries(readings).map(([key,value])=>[key,value.join(" ")])),
                        candidates:candidates.map(e=>({fields:e.revision.content,uncertain:e.revision.observations.filter(o=>o.state!=="PRESENT")})),
                        diagnostics:output?.layout?.regions?.find(r=>r.region_id===region.region_id)?.issues || []}));
                inspection.append(entry);
            }
            container.append(inspection);
        }
        const packet = result.extraction; if (!packet) return;
        container.append(node("p",`Source: ${sourceLabel(packet.source)} · Extraction mode: ${result.pipeline}`),details("Source metadata", packet.source),details("Extraction issues", packet.issues));
        container.append(details(`${result.pipeline}: ${packet.events.length} extraction candidates`, packet.events));
        if (result.pipeline === "evidence") container.append(node("p", "Evidence-only mode produces no baseline events. P1 hypotheses, if requested, are shown separately."));
        else if (!packet.events.length) container.append(node("p", `${result.pipeline} returned no candidates. Evidence availability is not timetable completion.`));
        if (result.reconstruction) container.append(details(result.reviewContextNote ? "Original P1 projection (historical, not the corrected revision)" : "P1 development search, findings and digests", result.reconstruction));
        container.append(details("M5 development policy (not calibrated)", result.policy || "NOT ASSESSED"));
        container.append(details("M6 read-only diagnostics and preview digest", result.calendar || "BLOCKED — NOT ASSESSED"));
        container.append(node("p", "M6 receives no fabricated full assessment: the public M5 response omits complete findings. No approval or synchronization is available."));
        for (const span of sourceSpans(result)) {
            const button = node("button", `Page ${span.page ?? "unknown"} · ${span.route} · Highlight source`, "text-button evidence-link");
            button.type="button";button.dataset.evidenceId=span.evidence_id;
            const entry=node("div",null,"evidence-entry");entry.dataset.evidenceId=span.evidence_id;
            entry.append(button,reference(span.evidence_id),node("pre",span.raw_text),node("small",span.bbox ? JSON.stringify(span.bbox) : "Coordinates unavailable"));
            button.addEventListener("click",()=>highlightEvidence(span.evidence_id));container.append(entry);
        }
    }
    function highlightEvidence(id) {
        global.document.querySelectorAll("[data-evidence-id]").forEach(element => element.classList.toggle("evidence-selected", element.dataset.evidenceId === id));
        const entry=[...global.document.querySelectorAll(".evidence-entry")].find(e=>e.dataset.evidenceId===id);
        if(entry?.getClientRects?.().length) entry.scrollIntoView?.({block:"nearest",behavior:"smooth"});
    }
    function originalEvidenceId(result,id) {
        return Object.entries(result?.reconstruction?.execution_metadata?.capture_aliases || {}).find(([,alias])=>alias===id)?.[0] || id;
    }
    function renderStructuredEvidence(packet) {
        const container=global.document.getElementById("extractionEvidence");if (!container) return;
        container.replaceChildren();if (!packet) { container.append(node("p","M1 structured evidence unavailable; M5 NOT ASSESSED."));return; }
        container.append(node("h3",`${sourceLabel(packet.source)} — structured adapter inspection`),details("M1 source provenance",packet.source),
            details("Structured parsing issues",packet.issues),details("Canonical fields, states and evidence references",packet.events),
            node("p","M5 NOT ASSESSED · Human review NOT REVIEWED · Calendar BLOCKED / NOT APPROVED. Raw HTML is not rendered."));
        for (const span of packet.evidence) container.append(details(`Source evidence ${span.evidence_id}`,span.raw_text));
    }
    function renderSource(result) {
        const container=global.document.getElementById("documentPreview");if (!container) return;
        if (container.dataset.previewUrl===result.previewUrl) {
            if (result.layoutEvidence) {
                const frame=container.querySelector(".source-frame"), image=frame?.querySelector("img");
                if (frame && image) drawBoxes(frame,image,result);
            }
            return;
        }
        container.replaceChildren();container.dataset.previewUrl=result.previewUrl || "";
        if (!result.previewUrl) { container.append(node("p", "No validated source preview available."));return; }
        if (result.mime === 'text/plain') {
            const text=node('pre',result.sourceText || '', 'copied-source-text');
            text.tabIndex=0;text.setAttribute('aria-label','Original copied timetable text');
            const download=node('a','Save original text for editing access','button secondary');
            download.href=result.previewUrl;download.download='copied-timetable.txt';
            container.append(text,download,node('p','Keep this exact text file to reconnect saved reviews. To enable editing, personally authorize the saved original in the local review app. Copied text does not retain the original page layout.'));
        } else if (result.mime === "application/pdf") {
            const frame=node("iframe");frame.src=result.previewUrl;frame.title="Local uploaded PDF";frame.setAttribute("sandbox", "");
            const link=node("a", "Open local PDF if embedded preview is unsupported");link.href=result.previewUrl;link.target="_blank";link.rel="noopener noreferrer";
            container.append(frame,link,node("p","PDF coordinates are listed with evidence; PDF overlay is not supported."));
        } else {
            const frame=node("div",null,"source-frame");const image=node("img");image.src=result.previewUrl;image.alt="Local uploaded timetable";
            image.addEventListener("load",()=>drawBoxes(frame,image,result));frame.append(image);container.append(frame);
            const expand=node("button","Expand source image","button secondary source-expand");expand.type="button";
            expand.addEventListener("click",()=>expandSource(result));container.append(expand);
            const orientation=result.layoutEvidence?.pages?.find(p=>p.page===1)?.transform?.source_exif_orientation;
            if (orientation != null && orientation !== 1) container.append(node("p","Source orientation differs: overlay disabled. Inspect the listed original coordinates."));
        }
    }
    function drawBoxes(frame,image,result) {
        frame.querySelectorAll(".evidence-box").forEach(e=>e.remove());
        if (!image.naturalWidth || !image.naturalHeight) return;
        const page=result.layoutEvidence?.pages?.find(p=>p.page===1);
        if (page && (page.width!==image.naturalWidth || page.height!==image.naturalHeight ||
            ![null,undefined,1].includes(page.transform?.source_exif_orientation))) return;
        // Block outlines are the useful default for S2b; all raw word/line
        // evidence stays accessible in the inspection panel.
        const overlaySpans=page?.regions?.length ? page.regions.map(r=>({bbox:r.bbox,page:1,evidence_id:r.region_id})) : sourceSpans(result);
        for (const span of overlaySpans) {
            const b=span.bbox;
            if (!b || b.coordinate_space!=="PIXELS" || span.page!==1 || b.x0<0 || b.y0<0 || b.x1>image.naturalWidth || b.y1>image.naturalHeight) continue;
            const box=node("button",null,"evidence-box");box.type="button";box.dataset.evidenceId=span.evidence_id;
            box.setAttribute("aria-label","Inspect original source region");box.title="Source region; technical references are available in Advanced Details";
            Object.assign(box.style,{left:`${100*b.x0/image.naturalWidth}%`,top:`${100*b.y0/image.naturalHeight}%`,width:`${100*(b.x1-b.x0)/image.naturalWidth}%`,height:`${100*(b.y1-b.y0)/image.naturalHeight}%`});
            box.addEventListener("click",()=>{
                highlightEvidence(span.evidence_id);
                const view=displayedEvents(result).find(v=>eventRegions(v.canonical,result).some(m=>m.region.region_id===span.evidence_id));
                if(view)inspectEvent(view.canonical,result,box);
            });frame.append(box);
        }
    }
    function uniqueEvents(events) {
        const seen=new Set();
        return events.filter(e=>{const key=e.id && `${e.id}:${e.canonical?.revision?.revision_id || ''}`;
            if (!key) return true; if (seen.has(key)) return false; seen.add(key); return true;});
    }
    function filteredEvents(events, filter='all', search='', sort='time') {
        const query=search.trim().toLowerCase();
        return uniqueEvents(events).filter(e=>(filter==='all' || (filter==='accepted' ? e.completeCandidate : filter==='review' ? needsStudentReview(e) : e.status===filter)) &&
            [e.subject,e.subjectName,e.lecturer,e.venue,e.section,e.type,e.date].join(' ').toLowerCase().includes(query))
            .sort((a,b)=>{
                if (sort==='course') {const name=(a.subject || '').localeCompare(b.subject || '');if(name)return name;}
                const key=e=>/^\d{4}-\d{2}-\d{2}$/.test(e.date || '') ? e.date : `9999-${e.canonical?.revision?.content?.day_of_week || 9}`;
                return key(a).localeCompare(key(b)) || (a.start || '99:99').localeCompare(b.start || '99:99') || (a.id || '').localeCompare(b.id || '');
            });
    }
    function verified(event) {
        return ['MANUALLY_VERIFIED_REFERENCE','INDEPENDENTLY_VERIFIED'].includes(event?.human_verification?.status);
    }
    function friendlyDate(content) {
        if (/^\d{4}-\d{2}-\d{2}$/.test(content.date || '')) {
            const date=new Date(content.date+'T12:00:00Z');
            if (Number.isFinite(date.getTime())) return new Intl.DateTimeFormat('en-GB',{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(date);
        }
        return content.day_of_week ? ['','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][content.day_of_week]+' · Date not confirmed' : 'Day and date not confirmed';
    }
    function friendlyTime(value) {
        if(value==='24:00')return '12:00 AM (next day)';
        const match=/^(\d{2}):(\d{2})$/.exec(value || '');
        return match ? `${Number(match[1])%12 || 12}:${match[2]} ${Number(match[1])<12?'AM':'PM'}` : 'Time not confirmed';
    }
    function eventRegions(event,result) {
        const observations=event.revision.observations;
        const observedIds=new Set(observations.flatMap(o=>o.evidence_ids));
        if(result.layoutEvidence?.profile==='ocr-ruled-grid-s2a-v1') {
            const anchors=new Set(observations.filter(o=>['course_code','course_name'].includes(o.field_name)).flatMap(o=>o.evidence_ids));
            const spans=(result.reconstruction?.canonical_output?.packet?.evidence || []).filter(s=>anchors.has(s.evidence_id) && s.locator?.startsWith('cell:') && s.bbox?.coordinate_space==='PIXELS');
            const unique=[...new Map(spans.map(s=>[`${s.page}:${JSON.stringify(s.bbox)}`,s])).values()];
            if(unique.length!==1)return [];
            const span=unique[0],page=result.layoutEvidence.pages.find(p=>p.page===span.page);
            return page?[{page,region:{region_id:span.evidence_id,bbox:span.bbox,source_cell:true}}]:[];
        }
        const sourceOwners=new Set((result.reconstruction?.canonical_output?.packet?.evidence || []).filter(s=>observedIds.has(s.evidence_id) && s.locator?.startsWith('region:')).map(s=>s.locator.slice('region:'.length)));
        if(sourceOwners.size===1)return (result.layoutEvidence?.pages || []).flatMap(page=>(page.regions || []).filter(r=>sourceOwners.has(r.region_id)).map(region=>({page,region})));
        const owners=new Set(observations.flatMap(o=>(o.transformations || []).filter(t=>t.startsWith('rt4-owner-region:')).map(t=>t.slice('rt4-owner-region:'.length))));
        if(owners.size===1)return (result.layoutEvidence?.pages || []).flatMap(page=>(page.regions || []).filter(r=>owners.has(r.region_id)).map(region=>({page,region})));
        const regionsFor=ids=>(result.layoutEvidence?.pages || []).flatMap(page=>(page.regions || []).filter(r=>
            page.words.some(w=>w.region_id===r.region_id && ids.has(w.evidence_id))).map(region=>({page,region})));
        // A spacing witness from another class is supporting provenance, not
        // ownership of that class's image. Prefer the course anchor's region.
        const anchors=regionsFor(new Set(observations.filter(o=>o.field_name==='course_code').flatMap(o=>o.evidence_ids)));
        return anchors.length ? anchors : regionsFor(new Set(observations.flatMap(o=>o.evidence_ids)));
    }
    function reference(id) {
        const row=node('div',null,'reference-row');const code=node('code',id.length>28?id.slice(0,14)+'…'+id.slice(-10):id);
        code.title=id;const copy=node('button','Copy full ID','text-button');copy.type='button';
        copy.addEventListener('click',async()=>{try {await global.navigator.clipboard.writeText(id);copy.textContent='Copied';}
            catch {copy.textContent='Copy unavailable';row.append(node('code',id));}});
        row.append(code,copy);return row;
    }
    function regionReadings(page,region) {
        const groups=new Map();
        for(const w of page.words.filter(w=>region.source_cell ? w.bbox.x0>=region.bbox.x0 && w.bbox.x1<=region.bbox.x1 && w.bbox.y0>=region.bbox.y0 && w.bbox.y1<=region.bbox.y1 : w.region_id===region.region_id)) {
            const key=w.capture_route+' / '+w.parent_line_id;
            if(!groups.has(key))groups.set(key,{route:w.capture_route,words:[]});groups.get(key).words.push(w);
        }
        return [...groups.values()].map(g=>({route:g.route,text:g.words.sort((a,b)=>a.bbox.x0-b.bbox.x0).map(w=>w.text).join(' '),
            confidence:g.words.every(w=>w.confidence!=null)?Math.round(100*g.words.reduce((sum,w)=>sum+w.confidence,0)/g.words.length)+'%':'Unavailable'}));
    }
    function inspectEvent(event,result,trigger) {
        const panel=global.document.getElementById('sourceInspection');if(!panel)return;
        const matches=eventRegions(event,result);panel.replaceChildren();panel.hidden=false;
        const back=node('button','Back to classes','text-button');back.type='button';
        back.addEventListener('click',()=>{panel.hidden=true;trigger?.focus?.();});
        const title=node('h3',event.revision.content.course_code || 'Selected class');title.tabIndex=-1;
        panel.append(back,title,node('p','Original image and OCR readings. These may differ from the interpreted fields.','inspection-note'));
        global.document.querySelectorAll('.class-card').forEach(card=>card.classList.toggle('is-selected',card.dataset.candidateId===event.candidate_id));
        if(!matches.length)panel.append(node('p','A source image region is unavailable. Open the class’s technical details to inspect its evidence.'));
        for(const {page,region} of matches) {
            highlightEvidence(region.region_id);
            const image=global.document.querySelector('#documentPreview img'),b=region.bbox;
            if(image?.complete && image.naturalWidth===page.width && image.naturalHeight===page.height && page.page===1 &&
                b.coordinate_space==='PIXELS' && [null,undefined,1].includes(page.transform?.source_exif_orientation) &&
                b.x0>=0 && b.y0>=0 && b.x1<=page.width && b.y1<=page.height && b.x1>b.x0 && b.y1>b.y0) {
                const canvas=node('canvas');canvas.width=Math.ceil(b.x1-b.x0)*2;canvas.height=Math.ceil(b.y1-b.y0)*2;
                canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Original source crop for selected class');
                canvas.getContext('2d').drawImage(image,b.x0,b.y0,b.x1-b.x0,b.y1-b.y0,0,0,canvas.width,canvas.height);panel.append(canvas);
            } else panel.append(node('p','Crop unavailable for this source orientation or coordinate space.'));
            const ocr=details('Original OCR readings',regionReadings(page,region));ocr.open=true;panel.append(ocr);
            panel.append(details('Interpreted fields',event.revision.content));
        }
        panel.scrollIntoView?.({block:'nearest',behavior:'smooth'});title.focus?.({preventScroll:true});
    }
    function expandSource(result) {
        const dialog=global.document.getElementById('sourceZoom');if(!dialog || !result.previewUrl)return;
        const area=global.document.getElementById('sourceZoomContent');area.replaceChildren();
        const image=node('img');image.src=result.previewUrl;image.alt='Expanded original timetable';area.append(image);
        const slider=global.document.getElementById('sourceZoomScale');slider.value='100';image.style.width='100%';
        slider.oninput=()=>{image.style.width=slider.value+'%';};
        global.document.getElementById('closeSourceZoom').onclick=()=>dialog.close();dialog.showModal();
    }
    function renderCards(events,result,container) {
        container.replaceChildren();
        if(!events.length) {
            const hidden=displayedEvents(result).length>0;
            const failed=['FAILED','EXECUTION_ABORTED'].some(status=>Object.values(result.stages || {}).includes(status));
            const message=result.busy?'Reading your timetable…':failed?
                (result.failureMessage || 'We couldn’t read this file. Try again or choose a clearer image.'):hidden?
                'No classes match this view. Clear your search or choose All classes.':
                'No classes were detected in this image.';
            const empty=node('div',null,'empty-list');empty.append(node('p',message));
            if(!result.busy && !hidden) {
                empty.append(node('p',failed?'See processing details for the failed stage.':
                    'Check that class text and day headers are visible. Try a clearer image or inspect processing details.'));
                const retry=node('button','Retry','secondary-button');retry.type='button';
                retry.disabled=!result.file;retry.onclick=()=>global.document.getElementById('retryUpload')?.click();empty.append(retry);
                empty.append(details('Processing details',{stages:result.stages || {},diagnostics:result.diagnostics || [],
                    regions:result.layoutEvidence?.pages?.reduce((n,p)=>n+(p.regions?.length || 0),0) || 0,
                    reconstruction:result.reconstruction?.canonical_output?.layout?.issues || []}));
            }
            container.append(empty);return;
        }
        for(const view of events) {
            const event=view.canonical,c=event.revision.content;
            const card=node('article',null,'event-card class-card');card.dataset.candidateId=event.candidate_id;
            const body=node('div',null,'event-main'),heading=node('div',null,'class-heading');
            heading.append(node('strong',view.subject),node('span',view.type,'class-type'));
            const needsReview=needsStudentReview(view);
            const status=node('span',needsReview?'Needs review · Still needs confirmation':'No outstanding field checks','class-status'+(needsReview?' needs-review':''));
            body.append(heading,node('p',friendlyDate(c),'class-date'),node('p',friendlyTime(c.start_time)+' – '+friendlyTime(c.end_time),'class-time'));
            const facts=node('div',null,'class-details');
            facts.append(node('span','Venue: '+(c.location || 'Not confirmed')));
            if(c.section)facts.append(node('span','Group: '+c.section));
            if(c.lecturer)facts.append(node('span','Lecturer: '+c.lecturer));
            if(!c.section && !c.location) {
                const matches=eventRegions(event,result);
                for(const {page,region} of matches) {
                    const detail=result.reconstruction?.canonical_output?.layout?.regions?.find(r=>r.region_id===region.region_id);
                    if(detail?.issues?.includes('BARE_IDENTIFIER_ROLE_UNRESOLVED')) {
                        const reading=regionReadings(page,region).find(r=>/^[A-Z]{1,5}(?: [A-Z0-9]{1,5})?$/.test(r.text));
                        if(reading)facts.append(node('span','Identifier: '+reading.text+' · group or venue needs confirmation','uncertain-field'));
                    }
                }
            }
            const regionDetails=result.reconstruction?.canonical_output?.layout?.regions || [];
            if(eventRegions(event,result).some(({region})=>regionDetails.some(d=>d.region_id===region.region_id && d.issues?.includes('RED_REGION_REQUIRES_CLASH_OR_HOLIDAY_REVIEW'))))
                facts.append(node('p','Red class block — check for a clash or public holiday.','uncertain-field'));
            const corrections=eventRegions(event,result).flatMap(({region})=>regionDetails.find(d=>d.region_id===region.region_id)?.ocr_corrections || []);
            if(!humanCorrected(event) && corrections.some(c=>c.status==='AMBIGUOUS'))facts.append(node('p','Possible OCR error — please check','uncertain-field'));
            const routing=boundRouting(event,view.reviewTask?.decision || result.policy?.find(d=>d.candidate_id===event.candidate_id));
            if(routing){
                facts.append(node('p',routing.route==='ACCEPT'?`${routing.registrationMode==='TEST_ONLY'?'TEST ONLY · ':''}ACCEPT · Registered policy; calendar approval is separate`:routing.route==='ABSTAIN'?'ABSTAIN · Not safe to use as a complete class':'REVIEW · Check this class against the original','source-routing '+routing.route.toLowerCase()));
                if(routing.support==='SOURCE_SUPPORTED')facts.append(node('p','Source-supported reading; this is not registered ACCEPT or independent verification.'));
                for(const reason of routing.explanations.filter(r=>r.code!=='REGISTERED_ACCEPT_UNAVAILABLE').slice(0,3))facts.append(node('p',reason.message+' '+reason.action));
            }else facts.append(node('p','Routing has not been assessed for this exact revision. Refresh or check the saved class.','uncertain-field'));
            body.append(facts);
            const footer=node('div',null,'class-footer');footer.append(node('span',humanCorrected(event)?'Human corrected':'Automatically extracted','class-status'),status,node('span',independentlyVerified(event)?'Independently verified':'Not yet verified independently','verification-note'));
            const inspect=node('button','View source','text-button');inspect.type='button';inspect.addEventListener('click',()=>inspectEvent(event,result,inspect));footer.append(inspect);const edit=node('button','Edit class','text-button');edit.type='button';edit.addEventListener('click',()=>global.document.dispatchEvent(new global.CustomEvent('stair-edit-class',{detail:{candidateId:event.candidate_id}})));footer.append(edit);body.append(footer);
            const technical=node('details',null,'class-technical');technical.append(node('summary','View OCR details'));
            for(const {page,region} of eventRegions(event,result))technical.append(details('Original OCR text and confidence',regionReadings(page,region)));
            if(corrections.length) {
                const correctionDetails=node('details');correctionDetails.append(node('summary','OCR character corrections'));
                for(const correction of corrections) {
                    const item=node('div',null,'compact-evidence');
                    item.append(node('p','Original OCR: '+correction.original));
                    const suggestions=correction.suggested || (correction.candidates || []).map(c=>c.value).join(' / ') || 'Not established';
                    item.append(node('p','Suggested: '+suggestions));
                    const reason=correction.rule==='independent-format-numeric-position'?'Confusable character in an independently supported numeric position':
                        correction.rule==='same-region-alternative-OCR'?'Supported by an alternative reading of the same source region':'Insufficient or competing source evidence — please check';
                    item.append(node('p','Reason: '+reason),node('p',correction.status==='SUPPORTED'?'Source-supported interpretation; not verified':'Ambiguous; human review required'));
                    item.append(details('Correction evidence',correction));correctionDetails.append(item);
                }
                technical.append(correctionDetails);
            }
            const refs=node('details');refs.append(node('summary','View source evidence'));
            const spans=sourceSpans(result),ids=[...new Set(event.revision.observations.flatMap(o=>o.evidence_ids))];
            for(const id of ids) {
                const entry=node('div',null,'compact-evidence');entry.append(reference(id));
                const span=spans.find(s=>s.evidence_id===originalEvidenceId(result,id));
                if(span)entry.append(details('Text and coordinates',{text:span.raw_text,confidence:span.confidence,bbox:span.bbox}));refs.append(entry);
            }
            technical.append(refs,details('Advanced technical details',{pipeline:view.methodLabel,fields:event.revision.observations,
                policy:result.policy?.find(d=>d.candidate_id===event.candidate_id) || 'NOT_ASSESSED',validation:event.validation,
                verification:event.human_verification,calendar:event.calendar,partialProjection:view.partialProjection || false}));body.append(technical);
            card.addEventListener('click',e=>{if(!e.target.closest?.('button,summary,details,a,input'))inspectEvent(event,result,inspect);});
            card.append(body);container.append(card);
        }
    }

    global.STAIRUpload = Object.freeze({MAX_BYTES,PIPELINES,Controller,validateFile,validatePacket,validateExtraction,validateReconstruction,
        calendarBundle,calendarBundleFromResult,boundRouting,routingSummary,renderRoutingSummary,copiedTextFile,validateCopiedText,eventIdentity,humanCorrected,independentlyVerified,needsStudentReview,mergeSavedViews,readerConfiguration,validatePolicy,eventView,sourceLabel,originalEvidenceId,displayedEvents,renderEvidence,renderStructuredEvidence,renderSource,drawBoxes,renderCards,uniqueEvents,filteredEvents,verified,eventRegions,friendlyDate,friendlyTime,inspectEvent,expandSource,diagnostic,friendlyFailure,reviewIngestBody,highlightEvidence});
})(globalThis);
