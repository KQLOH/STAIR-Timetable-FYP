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
            lecturer:content.lecturer || "", section:content.section || "", status:"review", completeCandidate:complete, selected:false, canonical:event};
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
        return uniqueEvents(output ? [...output.packet.events, ...output.unresolved_candidates].map(e => ({...eventView(e, label),
            methodLabel:output.profile_id, partialProjection:output.unresolved_candidates.some(p=>p.candidate_id===e.candidate_id)})) :
            (result?.extraction?.events || []).map(e => ({...eventView(e,label),methodLabel:result.pipeline})));
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
        if (result.mime === "application/pdf") {
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
            box.setAttribute("aria-label",`Highlight evidence ${span.evidence_id}`);box.title=span.evidence_id;
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
        return uniqueEvents(events).filter(e=>(filter==='all' || (filter==='accepted' ? e.completeCandidate : filter==='review' ? !e.completeCandidate : e.status===filter)) &&
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
        const match=/^(\d{2}):(\d{2})$/.exec(value || '');
        return match ? `${Number(match[1])%12 || 12}:${match[2]} ${Number(match[1])<12?'AM':'PM'}` : 'Time not confirmed';
    }
    function eventRegions(event,result) {
        const ids=new Set(event.revision.observations.flatMap(o=>o.evidence_ids));
        return (result.layoutEvidence?.pages || []).flatMap(page=>page.regions.filter(r=>
            page.words.some(w=>w.region_id===r.region_id && ids.has(w.evidence_id))).map(region=>({page,region})));
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
        for(const w of page.words.filter(w=>w.region_id===region.region_id)) {
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
        if(!events.length) {container.append(node('div',result.busy?'Reading your timetable…':result.stages?.extraction==='FAILED'?
            'We couldn’t read this file. Try again or choose a clearer image.':'No classes match this view. Clear your search or choose All classes.','empty-list'));return;}
        for(const view of events) {
            const event=view.canonical,c=event.revision.content;
            const card=node('article',null,'event-card class-card');card.dataset.candidateId=event.candidate_id;
            const body=node('div',null,'event-main'),heading=node('div',null,'class-heading');
            heading.append(node('strong',view.subject),node('span',view.type,'class-type'));
            const status=node('span',view.completeCandidate?'All required fields detected':'Needs review','class-status'+(view.completeCandidate?'':' needs-review'));
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
            body.append(facts);
            const footer=node('div',null,'class-footer');footer.append(status,node('span',verified(event)?'Verification recorded':'Not yet verified','verification-note'));
            const inspect=node('button','View source','text-button');inspect.type='button';inspect.addEventListener('click',()=>inspectEvent(event,result,inspect));footer.append(inspect);body.append(footer);
            const technical=node('details',null,'class-technical');technical.append(node('summary','View OCR details'));
            for(const {page,region} of eventRegions(event,result))technical.append(details('Original OCR text and confidence',regionReadings(page,region)));
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
        validatePolicy,eventView,sourceLabel,originalEvidenceId,displayedEvents,renderEvidence,renderStructuredEvidence,renderSource,drawBoxes,renderCards,uniqueEvents,filteredEvents,verified,eventRegions,friendlyDate,friendlyTime,inspectEvent,expandSource,diagnostic,reviewIngestBody,highlightEvidence});
})(globalThis);
