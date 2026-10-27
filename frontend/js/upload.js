/* M5.1 browser integration. All data is ephemeral. No review or calendar mutations. */
(function (global) {
    "use strict";
    const MAX_BYTES = 20 * 1024 * 1024;
    const PIPELINES = Object.freeze(["evidence", "B1", "B2", "B2H-v1"]);
    const TYPES = {png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", pdf: "application/pdf"};
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

    async function validateFile(file) {
        const extension = String(file?.name || "").split(".").pop().toLowerCase();
        const mime = TYPES[extension];
        requireValue(mime, "Unsupported file: choose PNG, JPG/JPEG or PDF.");
        requireValue(file.size > 0 && file.size <= MAX_BYTES, "File must be nonempty and at most 20 MiB.");
        requireValue(!file.type || file.type === mime, "File extension and MIME type disagree.");
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
    function validatePolicy(payload, events) {
        requireValue(payload?.profile === "m5-development-v1" && payload.registered_routing_ready === false &&
            Array.isArray(payload.decisions) && payload.decisions.length > 0, "Invalid M5 development assessment response.");
        for (const decision of payload.decisions) {
            requireValue(["REVIEW", "ABSTAIN"].includes(decision.decision) && decision.registered_routing_eligible === false &&
                decision.calibrated_probability == null && Array.isArray(decision.reason_codes), "Unexpected policy promotion: development ACCEPT is disabled.");
            if (decision.candidate_id != null) requireValue(events.some(e => e.candidate_id === decision.candidate_id &&
                e.revision.revision_id === decision.revision_id), "Policy refers to an unknown or stale candidate.");
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
        async run(file, pipeline, {reconstruct = true, layout = false, ruledGrid = false, blockGrid = false} = {}) {
            this.reset();
            const sequence = this.sequence;
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
                const {mime, filename} = await validateFile(file);
                if (sequence !== this.sequence) return result;
                this.objectUrl = global.URL.createObjectURL(file); result.previewUrl = this.objectUrl; result.mime = mime;
                emit(`Extracting ${pipeline} evidence…`);
                const selectedLayoutProfile = blockGrid ? "ocr-block-grid-s2b-v1" : ruledGrid ? "ocr-ruled-grid-s2a-v1" : null;
                const extracted = await this.request(`/api/extract?pipeline=${encodeURIComponent(pipeline)}` + (selectedLayoutProfile ? "&layout_profile=" + selectedLayoutProfile : ""), {
                    method:"POST", headers:{"Content-Type":mime, "X-Filename":filename}, body:file}, signal);
                result.extraction = validateExtraction(extracted, pipeline);
                if (selectedLayoutProfile) {
                    requireValue(extracted.layout_profile === selectedLayoutProfile && extracted.layout_evidence?.schema_version === (blockGrid ? "s2b.1" : "s2a.1"), "Missing versioned supplementary layout evidence.");
                    result.layoutEvidence = extracted.layout_evidence;
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
                            body:assessmentBody(result.extraction, result.reconstruction, result.layoutEvidence)}, signal);
                        if (sequence !== this.sequence) return result;
                        const output = result.reconstruction.canonical_output;
                        result.policy = validatePolicy(response, output ? [...output.packet.events, ...output.unresolved_candidates] : []);
                        result.stages.policy = "DEVELOPMENT_ASSESSED";
                    } catch (error) {
                        if (signal.aborted || sequence !== this.sequence) return result;
                        result.stages.policy = "NOT_ASSESSED"; result.diagnostics.push("M5: " + diagnostic(error));
                    }
                } else result.stages.policy = "NOT_ASSESSED";
                const output = result.reconstruction?.canonical_output;
                const bundle = {packet:output?.packet || result.extraction,
                    reconstruction_status:output?.status || (result.stages.reconstruction === "EXECUTION_ABORTED" ? "EXECUTION_ABORTED" : "NOT_PROVIDED"), context:{}, assessments:[]};
                // Public M5 decisions omit complete findings; never manufacture a full M6 assessment from them.
                try {
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
                result.stages.extraction = "FAILED"; result.diagnostics.push(diagnostic(error)); emit("PROCESSING FAILED — no successful timetable claimed");
            } finally {
                if (sequence === this.sequence) { result.busy = false; this.abortController = null; this.notify(result); }
            }
            return result;
        }
    }

    function eventView(event, layer) {
        const content = event.revision.content;
        const required = ["course_code", "session_type", "start_time", "end_time", "location"];
        const complete = required.every(k => content[k] != null) && (content.date != null || content.day_of_week != null) &&
            !event.revision.observations.some(f => ["UNRESOLVED","CONFLICTING"].includes(f.state));
        return {id:event.candidate_id, subject:content.course_code || "Unresolved course", subjectName:content.course_name || "",
            day:DAY[content.day_of_week], date:content.date || "Unresolved date", start:content.start_time || "?", end:content.end_time || "?",
            venue:content.location || "Unresolved venue", type:content.session_type || "Unresolved type", source:layer,
            status:"review", completeCandidate:complete, selected:false, canonical:event};
    }
    function assessmentBody(packet, reconstruction, layoutEvidence) {
        const raw = responseJSON.get(reconstruction);
        requireValue(raw, "Original reconstruction JSON unavailable; do not re-encode a digested envelope.");
        return '{"source_packet":' + JSON.stringify(packet) + ',"reconstruction":' + raw +
            ',"decoder_mode":"constrained","reconstruction_context":{},"policy_profile":"m5-development-v1"' +
            (layoutEvidence ? ',"layout_evidence":' + JSON.stringify(layoutEvidence) : '') + '}';
    }
    function reviewIngestBody(result) {
        requireValue(result?.extraction && result?.reconstruction, 'Review admission requires an original source packet and reconstruction.');
        return assessmentBody(result.extraction,result.reconstruction,result.layoutEvidence);
    }
    function displayedEvents(result) {
        const output = result?.reconstruction?.canonical_output;
        const label = sourceLabel(result?.extraction?.source);
        return output ? [...output.packet.events, ...output.unresolved_candidates].map(e => ({...eventView(e, label),
            methodLabel:output.profile_id, partialProjection:output.unresolved_candidates.some(p=>p.candidate_id===e.candidate_id)})) :
            (result?.extraction?.events || []).map(e => ({...eventView(e,label),methodLabel:result.pipeline}));
    }
    function sourceLabel(source, {sample = false} = {}) {
        if (sample && source == null) return "Synthetic demonstration sample";
        if (!record(source) || typeof source.source_document_id !== "string" || !HASH.test(source.content_sha256)) return "Unknown source";
        if (source.route === "tarumt_html" && source.media_type === "text/html") return "TAR UMT structured source";
        if (source.route === "native_pdf" && source.media_type === "application/pdf") return "Native PDF extraction";
        if (source.route === "ocr" && source.media_type === "application/pdf") return "PDF OCR extraction";
        if (source.route === "ocr" && ["image/png","image/jpeg"].includes(source.media_type)) return "OCR image upload";
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
        const extra=result.reconstruction?.canonical_output?.packet?.evidence || [];
        return [...original,...extra.filter(s=>!aliases.has(s.evidence_id) && !original.some(o=>o.evidence_id===s.evidence_id))];
    }
    function renderEvidence(result) {
        const container = global.document.getElementById("extractionEvidence"); if (!container) return;
        container.replaceChildren();
        container.append(node("h3", "Evidence and processing stages"),node("p", result.reviewContextNote || "Raw extraction, baseline candidates, P1, policy and calendar states are separate. Trusted-host corrections are unavailable."));
        container.append(details("Stage results and failures", {stages:result.stages, diagnostics:result.diagnostics}));
        if (result.layoutEvidence) container.append(details(result.layoutEvidence.profile === "ocr-block-grid-s2b-v1" ? "S2b supplementary words, colored regions and transforms (application only)" : "S2a supplementary word and pixel-grid evidence (application only)", result.layoutEvidence));
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
            const button = node("button", `${span.evidence_id} · page ${span.page ?? "unknown"} · ${span.route} · OCR confidence ${span.confidence ?? "unavailable"}`, "text-button evidence-link");
            button.type="button";button.dataset.evidenceId=span.evidence_id;
            const entry=node("div",null,"evidence-entry");entry.dataset.evidenceId=span.evidence_id;
            entry.append(button,node("pre",span.raw_text),node("small",span.bbox ? JSON.stringify(span.bbox) : "Coordinates unavailable"));
            button.addEventListener("click",()=>highlightEvidence(span.evidence_id));container.append(entry);
        }
    }
    function highlightEvidence(id) {
        global.document.querySelectorAll("[data-evidence-id]").forEach(element => element.classList.toggle("evidence-selected", element.dataset.evidenceId === id));
        const entry=[...global.document.querySelectorAll(".evidence-entry")].find(e=>e.dataset.evidenceId===id);
        entry?.scrollIntoView?.({block:"nearest",behavior:"smooth"});
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
        if (result.mime === "application/pdf") {
            const frame=node("iframe");frame.src=result.previewUrl;frame.title="Local uploaded PDF";frame.setAttribute("sandbox", "");
            const link=node("a", "Open local PDF if embedded preview is unsupported");link.href=result.previewUrl;link.target="_blank";link.rel="noopener noreferrer";
            container.append(frame,link,node("p","PDF coordinates are listed with evidence; PDF overlay is not supported."));
        } else {
            const frame=node("div",null,"source-frame");const image=node("img");image.src=result.previewUrl;image.alt="Local uploaded timetable";
            image.addEventListener("load",()=>drawBoxes(frame,image,result));frame.append(image);container.append(frame);
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
        for (const span of sourceSpans(result)) {
            const b=span.bbox;
            if (!b || b.coordinate_space!=="PIXELS" || span.page!==1 || b.x0<0 || b.y0<0 || b.x1>image.naturalWidth || b.y1>image.naturalHeight) continue;
            const box=node("button",null,"evidence-box");box.type="button";box.dataset.evidenceId=span.evidence_id;
            box.setAttribute("aria-label",`Highlight evidence ${span.evidence_id}`);box.title=span.evidence_id;
            Object.assign(box.style,{left:`${100*b.x0/image.naturalWidth}%`,top:`${100*b.y0/image.naturalHeight}%`,width:`${100*(b.x1-b.x0)/image.naturalWidth}%`,height:`${100*(b.y1-b.y0)/image.naturalHeight}%`});
            box.addEventListener("click",()=>highlightEvidence(span.evidence_id));frame.append(box);
        }
    }
    function renderCards(events,result,container) {
        container.replaceChildren();
        if (!events.length) { container.append(node("p",result.stages.extraction === "FAILED" ? "Processing failure — no successful candidate list." : result.busy ? "Processing…" :
            "No candidates in this view. Inspect evidence, pipeline choice, unresolved output and diagnostics.")); return; }
        for (const view of events) {
            const event=view.canonical;const card=node("article",null,"event-card review");const body=node("div",null,"event-main");
            const decision=result.policy?.find(d=>d.candidate_id===event.candidate_id);
            body.append(node("strong",`${view.subject}${view.subjectName ? " · " + view.subjectName : ""} · ${view.type}`),node("p",`${view.date} / ${view.day || "Unresolved day"} · ${view.start}–${view.end} · ${view.venue}`),
                node("p",`${view.source} · ${view.methodLabel || "candidate"}: ${view.completeCandidate ? "Complete candidate (field presence only)" : "Partial / unresolved candidate"}; extraction ${event.extraction_status}${view.partialProjection ? "; unselected supported-field projection, not a decoder prediction" : ""}`),
                node("p",`M5: ${decision?.decision || "NOT ASSESSED"} · Human review: ${event.human_review?.status || "NOT_REVIEWED"} · Verification: ${event.human_verification?.status || "NOT_VERIFIED"}`),
                node("p","Calendar: BLOCKED · Approval: NOT APPROVED · Synchronization: NOT SUBMITTED"));
            if (decision) body.append(details("Policy reasons and review requirements",decision));
            body.append(details("Field states, evidence links and alternatives",event.revision.observations),details("Constraint status and findings",event.validation));
            for (const id of new Set(event.revision.observations.flatMap(f=>f.evidence_ids))) {
                const label=result.layoutEvidence ? `${/^s2[ab]-word-/.test(id) ? "word" : "region"} ${id.slice(-12)}` : id;
                const link=node("button",`Source evidence: ${label}`,"text-button");link.type="button";link.title=id;link.addEventListener("click",()=>highlightEvidence(originalEvidenceId(result,id)));body.append(link);
            }
            card.append(body);container.append(card);
        }
    }
    global.STAIRUpload = Object.freeze({MAX_BYTES,PIPELINES,Controller,validateFile,validatePacket,validateExtraction,validateReconstruction,
        validatePolicy,eventView,sourceLabel,originalEvidenceId,displayedEvents,renderEvidence,renderStructuredEvidence,renderSource,drawBoxes,renderCards,diagnostic,reviewIngestBody,highlightEvidence});
})(globalThis);
