const API_BASE = "http://127.0.0.1:8000";


// =========================================================
// Application state
// =========================================================

const state = {
    events: [],
    source: null,
    meta: null,
    upload: null,
    canonical: null,
    reviewSnapshot: null,
    reviewStale: false,

    filter: "all",
    search: "",

    connectionToken: null,
    studentName: null
};

// Source-changing requests share a generation; late portal replies cannot replace newer input.
let sourceGeneration = 0;
const shellControls = typeof STAIRControls === "undefined" ? null : new STAIRControls.Shell();
let calendarViewport = null;

const analysisController = typeof STAIRAnalysis === "undefined" ? null : new STAIRAnalysis.Controller({
    base: API_BASE, getSource: () => ({upload:state.reviewStale || state.reviewSnapshot ? null : state.upload,
        canonical:state.reviewStale ? null : state.reviewSnapshot?.packet || state.canonical, sample:state.source === "sample"})
});
const reviewController = typeof STAIRReview === "undefined" ? null : new STAIRReview.Controller({
    base:API_BASE, getSource:reviewSource,
    openDialog:()=>shellControls?.openDialog("reviewDrawer"), closeDialog:()=>shellControls?.close(),
    onInvalidate:invalidateReviewDiagnostics, onSnapshot:applyReviewSnapshot, onHighlight:highlightReviewEvidence,
    onForget:clearReviewSessionProjection
    // No host transport or grant is installed by normal startup.
});
const calendarSandboxController = typeof STAIRSandbox === "undefined" ? null : new STAIRSandbox.Controller({base:API_BASE});

function reviewSource() {
    if (state.reviewSnapshot) return {packet:state.reviewSnapshot.packet,result:state.upload};
    const output=state.upload?.reconstruction?.canonical_output;
    const packet=output ? {...output.packet,events:[...output.packet.events,...output.unresolved_candidates]} : state.upload?.extraction || state.canonical;
    return {packet,result:state.upload};
}

function sameReviewCapture(source) {
    const current=reviewSource().packet?.source;
    return !!current && current.source_document_id===source.source_document_id && current.content_sha256===source.content_sha256;
}

function clearReviewSessionProjection() {
    if (!state.reviewSnapshot && !state.reviewStale) return;
    sourceGeneration++;state.reviewSnapshot=null;state.reviewStale=false;calendarViewport=null;
    if (state.upload) {
        state.events=STAIRUpload.displayedEvents(state.upload);
        STAIRUpload.renderEvidence(state.upload);
    } else if (state.source==="authorized_review") {
        clearUploadView();state.canonical=null;state.events=[];state.source=null;state.meta=null;
        document.getElementById("documentPreview")?.replaceChildren();
        setText("documentName","No document selected");setText("documentMeta","—");
    }
    analysisController?.invalidate(null,true);renderEverything();
    setText("processingStatus","Private review projection cleared. Any remaining local upload shows its original extraction, not current protected revisions.");
}

function renderHistoricalReviewEvidence() {
    if (!state.upload) return;
    STAIRUpload.renderEvidence({...state.upload,policy:[],calendar:null,
        stages:{...state.upload.stages,policy:"INVALIDATED_BY_APPLICATION_REVIEW",calendar:"INVALIDATED_BY_APPLICATION_REVIEW"},
        reviewContextNote:"Original extraction and decoder predictions are retained below as historical evidence. Current human revisions and revision-bound read-only diagnostics are in Application review. No Gold or calendar approval is established."});
}

function invalidateReviewDiagnostics(source) {
    if (!sameReviewCapture(source)) return;
    state.reviewStale=true;sourceGeneration++;
    analysisController?.invalidate(null,true);
    renderHistoricalReviewEvidence();renderEverything();
    setText("processingStatus","Application correction pending or stale — previous policy, calendar preview and analysis invalidated.");
}

function applyReviewSnapshot(snapshot) {
    const active=reviewSource().packet;
    // Selecting a task must never merge it into a different local capture.
    if (active && !sameReviewCapture(snapshot.packet.source)) return false;
    if (!active && state.upload?.busy) return false;
    if (!active) {
        clearUploadView();state.source="authorized_review";state.canonical=snapshot.packet;
        const preview=document.getElementById("documentPreview");
        preview?.replaceChildren();
        if (preview) {const note=document.createElement("p");note.textContent="This authorized review capture has no local source-image preview. Inspect its source-linked text, page and coordinates in candidate details.";preview.append(note);}
        setText("documentName","Authorized application review capture");setText("documentMeta",snapshot.packet.source.source_document_id);
    }
    sourceGeneration++;state.reviewSnapshot=snapshot;state.reviewStale=false;state.meta=null;
    state.events=snapshot.packet.events.map(event=>({...STAIRUpload.eventView(event,STAIRUpload.sourceLabel(snapshot.packet.source)),methodLabel:event.revision.method_id}));
    analysisController?.invalidate(null,true);
    renderHistoricalReviewEvidence();renderEverything();
    setText("processingStatus","Backend-confirmed application revisions — no new decoder prediction, Gold verification or calendar approval.");
    return true;
}

function highlightReviewEvidence({source,evidence,id}) {
    const original=state.upload?.extraction?.source;
    if (!original || original.source_document_id!==source.source_document_id || original.content_sha256!==source.content_sha256) {
        showToast("Different source capture","This evidence cannot be highlighted over the current local preview. Inspect its own source text, page and coordinates.",true);return;
    }
    if (id.startsWith("human:")) {showToast("Human assertion","This is a linked human reading, not an original OCR word or Gold annotation.");return;}
    if (evidence?.page && original.media_type?.startsWith("image/") && evidence.page!==1) {showToast("Page mismatch","No image overlay was created for another source page.",true);return;}
    STAIRUpload.highlightEvidence(STAIRUpload.originalEvidenceId(state.upload,id));
}


const DAY_MAP = {
    Mon: "monday",
    Tue: "tuesday",
    Wed: "wednesday",
    Thu: "thursday",
    Fri: "friday",
    Sat: "saturday",
    Sun: "sunday"
};


const DAY_LABEL = {
    monday: "Mon",
    tuesday: "Tue",
    wednesday: "Wed",
    thursday: "Thu",
    friday: "Fri",
    saturday: "Sat",
    sunday: "Sun"
};


// =========================================================
// Start application
// =========================================================

document.addEventListener(
    "DOMContentLoaded",
    () => {

        setupNavigation();
        shellControls?.setup();
        reviewController?.setup();
        calendarSandboxController?.setup();

        setupSourceButtons();

        setupReviewControls();

        setupCalendarControls();
        document.getElementById("analyzeSchedule")?.addEventListener("click", () => analysisController.run());
        for (const id of ["analysisStart", "analysisEnd", "analysisZone", "analysisScope", "analysisCohort"]) {
            document.getElementById(id)?.addEventListener("input", () => analysisController?.invalidate(null, true));
        }
        document.getElementById("cancelUpload")?.addEventListener("click", () => uploadController.cancel());
        document.getElementById("retryUpload")?.addEventListener("click", () => { if (state.upload?.file && !state.upload.busy) previewUploadedFile(state.upload.file); });
        document.getElementById("replaceFile")?.addEventListener("click", () => document.getElementById("fileInput")?.click());
        document.getElementById("refreshIntranet")?.addEventListener("click", refreshIntranet);
        document.getElementById("disconnectIntranet")?.addEventListener("click", disconnectIntranet);
        const dropZone = document.getElementById("dropZone");
        dropZone?.addEventListener("dragover", event => event.preventDefault());
        dropZone?.addEventListener("drop", event => { event.preventDefault(); if (event.dataTransfer?.files?.[0]) previewUploadedFile(event.dataTransfer.files[0]); });
        dropZone?.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); document.getElementById("fileInput")?.click(); } });

        renderEverything();
    }
);


// =========================================================
// Navigation
// =========================================================

function setupNavigation() {

    const navButtons =
        document.querySelectorAll(".nav-item");

    navButtons.forEach(button => {

        button.addEventListener(
            "click",
            () => {

                const view =
                    button.dataset.view;

                showView(view);
            }
        );

    });


    document
        .getElementById("newExtractionTop")
        ?.addEventListener(
            "click",
            () => showView("upload")
        );


    document
        .getElementById("workspaceUpload")
        ?.addEventListener(
            "click",
            () => showView("upload")
        );


    document
        .getElementById("reviewToCalendar")
        ?.addEventListener(
            "click",
            () => showView("calendar")
        );
}


function showView(viewName) {

    // An invalid target must not hide every screen.
    if (!document.getElementById(`view-${viewName}`)) return;

    document
        .querySelectorAll(".view")
        .forEach(view => {

            view.classList.remove(
                "active"
            );

        });


    const target =
        document.getElementById(
            `view-${viewName}`
        );

    if (target) {
        target.classList.add(
            "active"
        );
    }


    document
        .querySelectorAll(".nav-item")
        .forEach(button => {

            button.classList.toggle(
                "active",
                button.dataset.view ===
                    viewName
            );
            if (button.dataset.view === viewName) button.setAttribute("aria-current", "page");
            else button.removeAttribute?.("aria-current");

        });


    const titles = {
        analysis: "Schedule Analysis",

        workspace:
            "Workspace",

        upload:
            "New extraction",

        review:
            "Review events",

        calendar:
            "Calendar",

        history:
            "History"

    };


    const pageTitle =
        document.getElementById(
            "pageTitle"
        );

    if (pageTitle) {

        pageTitle.textContent =
            titles[viewName]
            ?? "STAIR Timetable";

    }


    if (viewName === "calendar") {
        renderCalendar();
    }
    shellControls?.navigate();
}


// =========================================================
// Source buttons
// =========================================================

function setupSourceButtons() {

    document
        .querySelectorAll(
            '[data-action="choose-file"]'
        )
        .forEach(button => {

            button.addEventListener(
                "click",
                () => {

                    document
                        .getElementById(
                            "fileInput"
                        )
                        ?.click();

                }
            );

        });


    document
        .getElementById("browseButton")
        ?.addEventListener(
            "click",
            event => {

                event.stopPropagation();

                document
                    .getElementById(
                        "fileInput"
                    )
                    ?.click();

            }
        );


    document
        .getElementById("dropZone")
        ?.addEventListener(
            "click",
            event => {

                if (
                    event.target.id
                    !== "browseButton"
                ) {

                    document
                        .getElementById(
                            "fileInput"
                        )
                        ?.click();

                }

            }
        );


    document
        .getElementById("fileInput")
        ?.addEventListener(
            "change",
            event => {

                const file =
                    event.target.files?.[0];

                if (file) {
                    event.target.value = ""; // Allow an explicit retry with the same file.
                    previewUploadedFile(
                        file
                    );
                }

            }
        );


    document
        .getElementById(
            "connectIntranet"
        )
        ?.addEventListener(
            "click",
            openIntranetLogin
        );

    document
        .getElementById(
            "connectIntranetUpload"
        )
        ?.addEventListener(
            "click",
            openIntranetLogin
        );


    document
        .getElementById(
            "quickSample"
        )
        ?.addEventListener(
            "click",
            loadSampleData
        );


    document
        .getElementById(
            "loadSampleTop"
        )
        ?.addEventListener(
            "click",
            loadSampleData
        );


    document
        .getElementById(
            "loadSampleUpload"
        )
        ?.addEventListener(
            "click",
            loadSampleData
        );
}


// =========================================================
// TAR UMT Intranet
// =========================================================

// =========================================================
// TAR UMT LIVE LOGIN
// =========================================================

function openIntranetLogin() {
    shellControls?.openDialog("intranetModal");

    const modal =
        document.getElementById(
            "intranetModal"
        );

    const backdrop =
        document.getElementById(
            "intranetModalBackdrop"
        );

    const errorBox =
        document.getElementById(
            "intranetLoginError"
        );


    errorBox?.classList.remove(
        "visible"
    );


    if (modal) {

        modal.classList.add(
            "open"
        );

        modal.setAttribute(
            "aria-hidden",
            "false"
        );

    }


    backdrop?.classList.add(
        "visible"
    );


    document.getElementById("intranetStudentId")?.focus();
}


function closeIntranetLogin() {
    if (shellControls?.active === "intranetModal") shellControls.close();

    const modal =
        document.getElementById(
            "intranetModal"
        );

    const backdrop =
        document.getElementById(
            "intranetModalBackdrop"
        );


    modal?.classList.remove(
        "open"
    );


    modal?.setAttribute(
        "aria-hidden",
        "true"
    );


    backdrop?.classList.remove(
        "visible"
    );


    /*
      Important:
      Remove password immediately
      when dialog closes.
    */

    const password =
        document.getElementById(
            "intranetPassword"
        );


    if (password) {

        password.value = "";

    }
}


// =========================================================
// Modal controls
// =========================================================

document
    .getElementById(
        "closeIntranetModal"
    )
    ?.addEventListener(
        "click",
        closeIntranetLogin
    );


document
    .getElementById(
        "cancelIntranetLogin"
    )
    ?.addEventListener(
        "click",
        closeIntranetLogin
    );


document
    .getElementById(
        "intranetModalBackdrop"
    )
    ?.addEventListener(
        "click",
        closeIntranetLogin
    );


// =========================================================
// LOGIN FORM
// =========================================================

document
    .getElementById(
        "intranetLoginForm"
    )
    ?.addEventListener(
        "submit",
        connectLiveIntranet
    );


async function connectLiveIntranet(
    event
) {

    event.preventDefault();


    const studentId =
        document
            .getElementById(
                "intranetStudentId"
            )
            ?.value
            .trim();


    const passwordInput =
        document.getElementById(
            "intranetPassword"
        );


    const password =
        passwordInput?.value;


    const errorBox =
        document.getElementById(
            "intranetLoginError"
        );


    const loginButton =
        document.getElementById(
            "intranetLoginButton"
        );


    if (
        !studentId
        ||
        !password
    ) {

        showIntranetLoginError(
            "Please enter your Student ID and password."
        );

        return;

    }

    const generation = ++sourceGeneration;


    if (errorBox) {

        errorBox.classList.remove(
            "visible"
        );

    }


    if (loginButton) {

        loginButton.disabled =
            true;

        loginButton.textContent =
            "Connecting...";

    }


    showLoading(
        true,

        "Connecting to TAR UMT",

        "Signing in and fetching your latest timetable…"
    );


    try {

        const response =
            await fetch(
                `${API_BASE}/api/intranet/connect`,
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify(
                        {
                            student_id:
                                studentId,

                            password:
                                password
                        }
                    )
                }
            );


        const payload =
            await response.json();

        if (generation !== sourceGeneration) {
            if (response.ok && payload.success && payload.connection_token) {
                try { await STAIRIntranet.sessionRequest(API_BASE, "/api/intranet/disconnect", payload.connection_token); } catch (_) { /* No credentials or private responses logged. */ }
            }
            return;
        }


        if (!response.ok) {

            throw new Error(
                payload.detail
                ||
                "Unable to connect to TAR UMT."
            );

        }


        if (!payload.success) {

            throw new Error(
                "TAR UMT connection failed."
            );

        }


        // ---------------------------------------------
        // IMPORTANT:
        // Immediately clear password from browser field
        // ---------------------------------------------

        passwordInput.value =
            "";


        // ---------------------------------------------
        // Save STAIR session information
        // ---------------------------------------------

        STAIRIntranet.structuredPacket(payload.data);
        const previousToken = state.connectionToken;
        state.connectionToken =
            payload.connection_token;


        state.studentName =
            payload.student_name
            ?? null;


        state.source =
            "tarumt_intranet_live";


        // ---------------------------------------------
        // Timetable
        // ---------------------------------------------

        const data =
            payload.data;


        STAIRIntranet.structuredPacket(data);
        clearUploadView();
        state.source = "tarumt_intranet_live";
        state.canonical = data.canonical || null;
        if (previousToken && previousToken !== state.connectionToken) {
            try { await STAIRIntranet.sessionRequest(API_BASE, "/api/intranet/disconnect", previousToken); } catch (_) { /* Local session cleanup is best effort. */ }
            if (generation !== sourceGeneration) return;
        }
        updateSessionControls();
        state.events =
            data.events.map(
                (
                    timetableEvent,
                    index
                ) =>

                    normaliseIntranetEvent(
                        timetableEvent,
                        index
                    )
            );


        state.meta = {

            semester:
                data.semester,

            week:
                data.week,

            weekStart:
                data.week_start,

            weekEnd:
                data.week_end

        };


        // ---------------------------------------------
        // UI
        // ---------------------------------------------

        updateIntranetPreview();

        markPipelineComplete();

        renderEverything();


        closeIntranetLogin();


        showView(
            "review"
        );


        const student =
            state.studentName
            ? ` for ${state.studentName}`
            : "";


        showToast(
            "TAR UMT connected",
            `${state.events.length} live timetable events loaded${student}.`
        );

    }

    catch (error) {


        if (generation === sourceGeneration) showIntranetLoginError(
            error.message
        );

    }

    finally {
        passwordInput.value = "";

        showLoading(
            false
        );


        if (loginButton) {

            loginButton.disabled =
                false;

            loginButton.textContent =
                "Connect Intranet";

        }

    }
}


function showIntranetLoginError(
    message
) {

    const errorBox =
        document.getElementById(
            "intranetLoginError"
        );


    if (!errorBox) {
        return;
    }


    errorBox.textContent =
        message;


    errorBox.classList.add(
        "visible"
    );
}


// =========================================================
// Convert backend event into frontend event
// =========================================================

function normaliseIntranetEvent(
    event,
    index
) {

    const day =
        DAY_MAP[event.day]
        ?? event.day?.toLowerCase();


    const structurallyValid =
        Boolean(
            event.subject_code
            &&
            event.date
            &&
            event.start_time
            &&
            event.end_time
            &&
            event.start_time
                < event.end_time
        );


    // Legacy completeness is a presentation fact, never M5 acceptance.
    let status = "review";


    /*
        Replacement / clash / public holiday
        should be checked by the student before
        safe calendar sync.
    */

    if (
        event.class_status
        !== "normal"
    ) {

        status =
            "review";

    }


    return {

        id:
            `tar-${index}-${event.date}-${event.start_time}-${event.subject_code}`,

        subject:
            event.subject_code,

        subjectName:
            event.subject_name
            ?? "",

        day,

        date:
            event.date,

        start:
            event.start_time,

        end:
            event.end_time,

        venue:
            event.venue
            ?? "",

        venueFull:
            event.venue_full
            ?? "",

        type:
            event.class_type
            ?? "",

        lecturer:
            event.lecturer
            ?? "",

        classStatus:
            event.class_status,

        source:
            "tarumt_intranet",

        /*
          Actual STAIR confidence will come
          from stair_validator.py later.
        */

        confidence:
            null,

        status,
        completeCandidate: structurallyValid,
        policyState: "NOT ASSESSED",
        calendarEligibility: "BLOCKED",

        selected:
            false
    };
}


// =========================================================
// Uploaded files use the existing M3/M4/M5/M6 read-only interfaces.
// =========================================================

const uploadController = new STAIRUpload.Controller({base:API_BASE, notify:result => {
    state.reviewSnapshot=null;state.reviewStale=false;
    state.upload = result;
    state.source = "upload";
    state.canonical = null;
    state.events = STAIRUpload.displayedEvents(result);
    state.meta = null;
    setText("documentName", result.filename);
    setText("documentMeta", `${formatFileSize(result.size)} · ${result.mime || "validating"} · ${result.pipeline}`);
    setText("processingStatus", result.status);
    setText("runStatus", result.status);
    const progress = document.getElementById("uploadProgress"); if (progress) progress.hidden = !result.busy;
    const cancel = document.getElementById("cancelUpload");
    if (cancel) cancel.disabled = !result.busy;
    const retry = document.getElementById("retryUpload"); if (retry) retry.disabled = result.busy;
    document.querySelectorAll("#pipeline li").forEach(item => {
        const complete = item.dataset.step === "ingest" ? Boolean(result.previewUrl) : item.dataset.step === "extract" ? Boolean(result.extraction) :
            item.dataset.step === "decode" ? result.stages.reconstruction === "COMPLETE_FINITE_SEARCH" : false;
        item.classList.toggle("done", complete);item.classList.toggle("active", result.busy && !complete);
    });
    STAIRUpload.renderSource(result);
    const image = document.querySelector("#documentPreview img");
    if (image) STAIRUpload.drawBoxes(image.parentElement, image, result);
    STAIRUpload.renderEvidence(result);
    renderEverything();
}});

function previewUploadedFile(file) {
    sourceGeneration++;
    showView("review");
    const pipeline = document.getElementById("extractionPipeline")?.value || "evidence";
    return uploadController.run(file, pipeline, {reconstruct:document.getElementById("runP1")?.checked !== false,
        layout:document.getElementById("runLayoutAssociation")?.checked === true,
        ruledGrid:document.getElementById("runRuledGridS2a")?.checked === true,
        blockGrid:document.getElementById("runBlockGridS2b")?.checked === true});
}

function clearUploadView() {
    state.reviewSnapshot=null;state.reviewStale=false;
    uploadController.reset({notify:false}); state.upload = null;
    document.getElementById("extractionEvidence")?.replaceChildren();
    const preview = document.getElementById("documentPreview");
    if (preview) delete preview.dataset.previewUrl;
    setText("processingStatus", "");
    const cancel = document.getElementById("cancelUpload"); if (cancel) cancel.disabled = true;
    const retry = document.getElementById("retryUpload"); if (retry) retry.disabled = true;
    const progress = document.getElementById("uploadProgress"); if (progress) progress.hidden = true;
}

function updateSessionControls() {
    for (const id of ["refreshIntranet", "disconnectIntranet"]) {
        const button = document.getElementById(id); if (button) button.disabled = !state.connectionToken;
    }
}

function clearExpiredIntranetConnection(error, token, generation) {
    if (error.status !== 401 || token !== state.connectionToken) return;
    state.connectionToken = null; state.studentName = null; updateSessionControls();
    if (generation === sourceGeneration) setText("processingStatus", "TAR UMT session expired. Reconnect when needed. Current timetable remains an ephemeral, unassessed display.");
}

async function refreshIntranet() {
    if (!state.connectionToken) return;
    const generation = ++sourceGeneration;
    const token = state.connectionToken;
    try {
        const payload = await STAIRIntranet.sessionRequest(API_BASE, "/api/intranet/timetable", state.connectionToken);
        if (generation !== sourceGeneration || token !== state.connectionToken) return;
        STAIRIntranet.structuredPacket(payload.data);
        clearUploadView();
        state.source = "tarumt_intranet_live";
        state.canonical = payload.data.canonical || null;
        state.events = payload.data.events.map(normaliseIntranetEvent);
        state.meta = {semester:payload.data.semester, week:payload.data.week, weekStart:payload.data.week_start, weekEnd:payload.data.week_end};
        updateIntranetPreview(); renderEverything();
        setText("processingStatus", "Structured source refreshed — M5 NOT ASSESSED; calendar BLOCKED.");
    } catch (error) {
        if (generation !== sourceGeneration || token !== state.connectionToken) return;
        clearExpiredIntranetConnection(error, token, generation);
        showToast("Refresh failed", error.message, true);
    }
}

async function disconnectIntranet() {
    if (!state.connectionToken) return;
    const generation = ++sourceGeneration;
    const token = state.connectionToken;
    try {
        await STAIRIntranet.sessionRequest(API_BASE, "/api/intranet/disconnect", state.connectionToken);
        if (token !== state.connectionToken) return;
        state.connectionToken = null; state.studentName = null; updateSessionControls();
        if (generation === sourceGeneration) setText("processingStatus", "Disconnected. Current timetable remains an ephemeral, unassessed display.");
    } catch (error) {
        if (token !== state.connectionToken) return;
        clearExpiredIntranetConnection(error, token, generation);
        if (generation === sourceGeneration) showToast("Disconnect failed", error.message, true);
    }
}

window.addEventListener("pagehide", () => {
    sourceGeneration++;
    uploadController.reset({notify:false});
    analysisController?.invalidate(null, true);
});

// =========================================================
// Preview TAR UMT source
// =========================================================

function updateIntranetPreview() {

    const name =
        document.getElementById(
            "documentName"
        );

    const meta =
        document.getElementById(
            "documentMeta"
        );

    const preview =
        document.getElementById(
            "documentPreview"
        );


    if (name) {

        name.textContent =
            "TAR UMT Student Intranet";

    }


    if (meta) {

        meta.textContent =
            `Semester ${state.meta.semester} · Week ${state.meta.week}`;

    }


    if (!preview) {
        return;
    }


    const lines =
        state.events.map(event => {

            return [
                `${event.date} ${DAY_LABEL[event.day]}`,
                `${event.start} - ${event.end}`,
                event.subject,
                event.type,
                event.venue
            ].join(" · ");

        });


    preview.innerHTML = `
        <pre>${escapeHtml(
            [
                "TAR UMT Student Intranet",
                "",
                `Semester: ${state.meta.semester}`,
                `Week: ${state.meta.week}`,
                `Period: ${state.meta.weekStart} - ${state.meta.weekEnd}`,
                "",
                ...lines
            ].join("\n")
        )}</pre>
    `;
    STAIRUpload.renderStructuredEvidence(state.canonical);
}


// =========================================================
// Rendering
// =========================================================

function renderEverything() {
    if (typeof STAIRAnalysis !== "undefined") analysisController.invalidate(state.reviewStale ? "review-invalidated" : state.reviewSnapshot || state.upload?.reconstruction || state.upload?.extraction || state.upload || state.canonical || state.source);
    reviewController?.updateSource();

    renderMetrics();

    renderReview();

    renderCalendar();
}


function renderMetrics() {

    const detected =
        state.events.length;


    const ready =
        state.events.filter(
            event =>
                event.completeCandidate === true
        ).length;


    const review =
        state.events.filter(
            event =>
                event.completeCandidate !== true && event.status === "review"
        ).length;


    setText(
        "metricDetected",
        detected || "—"
    );


    setText(
        "metricReady",
        detected ? ready : "—"
    );


    setText(
        "metricReview",
        detected ? review : "—"
    );


    /*
        We purposely do not invent
        STAIR confidence yet.
    */

    setText(
        "metricConfidence",
        "—"
    );


    setText(
        "reviewNavCount",
        review
    );


    setText(
        "filterAll",
        detected
    );


    setText(
        "filterReady",
        ready
    );


    setText(
        "filterReview",
        review
    );


    // Legacy UI labels are not M5/M6 authority. No frontend write bridge exists.
    setText(
        "syncReadyCount",
        0
    );


    setText(
        "syncMalformed",
        countMalformed()
    );


    setText(
        "syncDuplicates",
        countDuplicates()
    );


    const subtitle =
        document.getElementById(
            "reviewSubtitle"
        );


    if (subtitle) {

        subtitle.textContent = state.reviewStale ? "Application review pending or stale — old policy, preview and analysis are invalidated." : state.reviewSnapshot
            ? `${detected} authorized visible application records. Current revisions are separate from original decoder predictions; complete schedule remains unverified.` : state.upload
            ? `${state.upload.status}. ${detected} displayed hypotheses; inspect separate baseline and P1 stages.`
            : detected ? `${detected} structured/demo events; M5 NOT ASSESSED; calendar BLOCKED.` : "Upload or import a timetable to begin.";

    }
}


// =========================================================
// Review events
// =========================================================

function setupReviewControls() {

    document
        .querySelectorAll(
            "#eventFilters button"
        )
        .forEach(button => {

            button.addEventListener(
                "click",
                () => {

                    document
                        .querySelectorAll(
                            "#eventFilters button"
                        )
                        .forEach(item =>
                            item.classList.remove(
                                "active"
                            )
                        );


                    button.classList.add(
                        "active"
                    );


                    state.filter =
                        button.dataset.filter;


                    renderReview();
                }
            );

        });


    document
        .getElementById(
            "eventSearch"
        )
        ?.addEventListener(
            "input",
            event => {

                state.search =
                    event.target.value
                        .trim()
                        .toLowerCase();

                renderReview();

            }
        );


    document
        .getElementById(
            "approveSelected"
        )
        ?.addEventListener(
            "click",
            approveSelected
        );


    document
        .getElementById(
            "deleteSelected"
        )
        ?.addEventListener(
            "click",
            deleteSelected
        );


    document
        .getElementById(
            "selectAll"
        )
        ?.addEventListener(
            "change",
            event => {

                const checked =
                    event.target.checked;


                document
                    .querySelectorAll(
                        ".event-select"
                    )
                    .forEach(input => {

                        input.checked =
                            checked;


                        const item =
                            state.events.find(
                                event =>
                                    event.id
                                    === input.dataset.id
                            );


                        if (item) {
                            item.selected =
                                checked;
                        }

                    });

            }
        );


    document
        .getElementById(
            "eventList"
        )
        ?.addEventListener(
            "change",
            event => {

                if (
                    !event.target.classList.contains(
                        "event-select"
                    )
                ) {
                    return;
                }


                const item =
                    state.events.find(
                        timetableEvent =>
                            timetableEvent.id
                            === event.target.dataset.id
                    );


                if (item) {

                    item.selected =
                        event.target.checked;

                }

            }
        );


    document
        .getElementById(
            "eventList"
        )
        ?.addEventListener(
            "click",
            event => {

                const deleteButton =
                    event.target.closest(
                        "[data-delete]"
                    );


                if (deleteButton) {

                    deleteEvent(
                        deleteButton.dataset.delete
                    );

                }

            }
        );


    document
        .getElementById(
            "exportJson"
        )
        ?.addEventListener(
            "click",
            exportJSON
        );
}


function renderReview() {

    const container =
        document.getElementById(
            "eventList"
        );


    if (!container) {
        return;
    }

    // Uploads must use canonical evidence/status rendering, never the legacy TAR UMT template.
    const demo = state.source === "sample";
    for (const id of ["approveSelected", "deleteSelected", "selectAll"]) {
        const control = document.getElementById(id); if (control) control.disabled = !demo;
    }
    if (state.upload) {
        const display=state.reviewSnapshot || state.reviewStale ? {...state.upload,policy:state.reviewStale?[]:state.reviewSnapshot.tasks.map(t=>t.decision)} : state.upload;
        STAIRUpload.renderCards(getVisibleEvents(), display, container);
        return;
    }
    if (state.reviewSnapshot) {
        STAIRUpload.renderCards(getVisibleEvents(),{extraction:state.reviewSnapshot.packet,policy:state.reviewStale?[]:state.reviewSnapshot.tasks.map(t=>t.decision),status:"Current application revisions"},container);
        return;
    }


    const events =
        getVisibleEvents();


    if (!events.length) {

        container.innerHTML = `
            <div class="empty-list">
                <p>No events found.</p>
            </div>
        `;

        return;
    }


    container.innerHTML =
        events.map(event => {

            const isReview =
                event.status
                === "review";


            const title =
                event.subjectName
                ? `${event.subject} · ${event.subjectName}`
                : event.subject;


            return `

                <article
                    class="event-card ${isReview ? "review" : ""}"
                >

                    <input
                        class="event-select"
                        type="checkbox"
                        data-id="${escapeHtml(event.id)}"
                        ${event.selected ? "checked" : ""}
                    >


                    <div class="event-main">

                        <div class="event-title-row">

                            <strong>
                                ${escapeHtml(title)}
                            </strong>

                            <span class="
                                event-status
                                ${isReview ? "review" : "ready"}
                            ">

                                ${event.source === "sample" ? (isReview ? "Demo review" : "Demo reviewed") : "NOT ASSESSED"}

                            </span>

                        </div>


                        <div class="event-facts">

                            <span>
                                ${escapeHtml(
                                    `${DAY_LABEL[event.day] ?? event.day} ${event.date}`
                                )}
                            </span>

                            <i></i>

                            <span>
                                ${escapeHtml(
                                    `${event.start} - ${event.end}`
                                )}
                            </span>

                            <i></i>

                            <span>
                                ${escapeHtml(
                                    event.type || "Class"
                                )}
                            </span>

                            <i></i>

                            <span>
                                ${escapeHtml(
                                    event.venue || "No venue"
                                )}
                            </span>

                        </div>


                        <div class="confidence-line">

                            <span>
                                ${escapeHtml(STAIRUpload.sourceLabel(state.canonical?.source, {sample:event.source === "sample"}))}
                                · M5 NOT ASSESSED · Human review NOT REVIEWED
                                · Calendar BLOCKED / NOT APPROVED
                            </span>

                        </div>

                    </div>


                    <div class="event-menu" ${event.source === "sample" ? "" : "hidden"}>

                        <button
                            type="button"
                            data-delete="${escapeHtml(event.id)}"
                            aria-label="Delete event"
                        >
                            <svg>
                                <use href="#i-trash"></use>
                            </svg>
                        </button>

                    </div>

                </article>
            `;

        }).join("");
}


function getVisibleEvents() {

    return state.events.filter(
        event => {

            const filterOK =
                state.filter === "all"
                ||
                (state.filter === "accepted" ? event.completeCandidate === true :
                    state.filter === "review" ? !event.completeCandidate && event.status === "review" : event.status === state.filter);


            const haystack =
                [
                    event.subject,
                    event.subjectName,
                    event.venue,
                    event.type,
                    event.lecturer,
                    event.date
                ]
                .join(" ")
                .toLowerCase();


            const searchOK =
                !state.search
                ||
                haystack.includes(
                    state.search
                );


            return (
                filterOK
                &&
                searchOK
            );

        }
    );
}


function approveSelected() {
    if (state.source !== "sample") { showToast("Review unavailable", "Trusted-host review is not configured; no backend approval.", true); return; }

    let count = 0;


    state.events.forEach(
        event => {

            if (event.selected) {

                event.status =
                    "accepted";

                event.selected =
                    false;

                count++;

            }

        }
    );


    renderEverything();


    showToast(
        "Demo labels updated",
        `${count} event(s) marked reviewed in this display only; no backend approval.`
    );
}


function deleteSelected() {
    if (state.source !== "sample") return; // Display-only sample removal, never source or calendar deletion.

    const before =
        state.events.length;


    state.events =
        state.events.filter(
            event =>
                !event.selected
        );


    const removed =
        before
        -
        state.events.length;


    renderEverything();


    showToast(
        "Events removed",
        `${removed} event(s) deleted.`
    );
}


function deleteEvent(id) {
    if (state.source !== "sample") return;

    state.events =
        state.events.filter(
            event =>
                event.id !== id
        );


    renderEverything();
}


// =========================================================
// Calendar preview
// =========================================================

function setupCalendarControls() {
    for (const [id, direction] of [["previousWeek", -1], ["nextWeek", 1]]) {
        document.getElementById(id)?.addEventListener("click", () => moveCalendarWeek(direction));
    }
    const button = document.getElementById("syncButton");
    if (!button) return;
    button.disabled = true;
    button.title = "NOT AUTHORIZED: legacy UI status cannot approve sandbox or external writes.";
    button.addEventListener("click", () => {
        showToast("Calendar writes blocked", "No authenticated M6 approval is connected to this display.", true);
    });
    const exportButton = document.getElementById("downloadIcs");
    if (exportButton) {
        exportButton.disabled = true;
        exportButton.title = "Export blocked pending timezone, recurrence and eligibility authority.";
    }
}

// Dates are source-observed calendar days, never inferred recurrence or UTC instants.
function calendarDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return null;
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date : null;
}

function shiftCalendarDate(value, days) {
    const date = calendarDate(value);
    if (!date) return null;
    date.setUTCDate(date.getUTCDate() + days);
    const iso = date.toISOString().slice(0, 10);
    return calendarDate(iso) ? iso : null;
}

function mondayOf(value) {
    const date = calendarDate(value);
    return date ? shiftCalendarDate(value, -((date.getUTCDay() + 6) % 7)) : null;
}

function loadedCalendarScope() {
    const dates = state.events.map(event => event.date).filter(value => calendarDate(value));
    const start = state.meta?.weekStart, end = state.meta?.weekEnd;
    if (calendarDate(start) && calendarDate(end) && start <= end) dates.push(start, end);
    if (!dates.length) return null;
    dates.sort();
    const first = mondayOf(dates[0]), last = mondayOf(dates[dates.length - 1]);
    if (!first || !last || !shiftCalendarDate(last, 6)) return null;
    return {first, last, start: dates[0], end: dates[dates.length - 1],
        key: JSON.stringify([state.source, sourceGeneration, first, last])};
}

function moveCalendarWeek(direction) {
    if (direction !== -1 && direction !== 1) return;
    const scope = loadedCalendarScope();
    if (!scope) return;
    const current = calendarViewport?.key === scope.key ? calendarViewport.start : scope.first;
    const next = shiftCalendarDate(current, 7 * direction);
    if (!next || next < scope.first || next > scope.last) return;
    calendarViewport = {key: scope.key, start: next};
    renderCalendar();
    if (document.activeElement?.disabled) document.getElementById("calendarRange")?.focus();
}

function renderCalendar() {
    const grid = document.getElementById("weekGrid");
    if (!grid) return;
    if (state.reviewStale) {
        for(const id of ["previousWeek","nextWeek"]) {const button=document.getElementById(id);if(button)button.disabled=true;}
        setText("calendarRange","Previous revision view invalidated by application review");
        setText("calendarScopeNote","Refresh the authorized task and its current revision before using calendar diagnostics. Writes remain blocked.");
        setText("calendarUndated","");grid.innerHTML='<p class="calendar-scope-note">No current revision preview is available.</p>';return;
    }
    const scope = loadedCalendarScope();
    if (!scope || calendarViewport?.key !== scope.key) calendarViewport = scope ? {key: scope.key, start: scope.first} : null;
    const first = calendarViewport?.start;
    const end = first && shiftCalendarDate(first, 6);
    const undated = state.events.filter(event => !calendarDate(event.date)).length;
    for (const [id, disabled, reason] of [
        ["previousWeek", !scope || first <= scope.first, "No earlier week is present in the loaded date scope."],
        ["nextWeek", !scope || first >= scope.last, "No later week is present in the loaded date scope."]
    ]) {
        const button = document.getElementById(id);
        if (button) {
            button.disabled = disabled;
            button.title = disabled ? (!scope ? "Week navigation requires usable dates from the loaded source." : reason) : "Show another week within the loaded date scope; no classes are repeated.";
        }
    }
    setText("calendarRange", scope ? `${first} – ${end}` : "No usable dated source scope loaded");
    setText("calendarScopeNote", scope ? `Loaded date scope: ${scope.start} – ${scope.end}. Only candidates dated in this week are shown; empty days do not establish availability or schedule completeness.` : "Week navigation is unavailable without usable source dates. No weekly recurrence is inferred.");
    setText("calendarUndated", undated ? `${undated} candidate(s) lack a usable date. Inspect them in Review Events; they are not repeated across weeks.` : "");
    if (!scope) {
        grid.innerHTML = '<p class="calendar-scope-note">No dated candidates to place in a week. Inspect available source evidence in Review Events.</p>';
        return;
    }
    const days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
    grid.innerHTML = days.map((day, offset) => {
        const date = shiftCalendarDate(first, offset);
        const events = state.events.filter(event => event.date === date)
            .sort((a, b) => String(a.start || "").localeCompare(String(b.start || "")));
        const body = events.length ? events.map(event => `
            <div class="calendar-event ${event.status === "review" ? "review" : ""}">
                <strong>${escapeHtml(event.subject)}</strong>
                <span>${escapeHtml(`${event.start || "?"} – ${event.end || "?"}`)}</span>
                <span>${escapeHtml(event.venue || "Venue unresolved")}</span>
                ${event.day && event.day !== day ? '<span>Printed weekday differs; inspect source evidence.</span>' : ""}
            </div>`).join("") : '<div class="day-empty">No dated candidate displayed (completeness unverified)</div>';
        return `<div class="day-column"><div class="day-heading">${DAY_LABEL[day]}<small>${date}</small></div>${body}</div>`;
    }).join("");
}


// =========================================================
// Validation helpers
// =========================================================

function isStructurallyValid(event) {

    return Boolean(

        event.subject

        &&
        event.date

        &&
        event.start

        &&
        event.end

        &&
        event.start
        < event.end

    );
}


function countMalformed() {

    return state.events.filter(
        event =>
            !isStructurallyValid(
                event
            )
    ).length;
}


function countDuplicates() {

    const keys =
        new Set();


    let duplicates =
        0;


    state.events.forEach(
        event => {

            const key =
                [
                    event.subject,
                    event.date,
                    event.start,
                    event.end,
                    event.venue
                ].join("|");


            if (keys.has(key)) {
                duplicates++;
            }

            else {
                keys.add(key);
            }

        }
    );


    return duplicates;
}


// =========================================================
// Download JSON
// =========================================================

function exportJSON() {

    const payload = {

        source:
            state.source,

        metadata:
            state.meta,

        events:
            state.events

    };


    downloadFile(
        "stair-timetable.json",
        JSON.stringify(
            payload,
            null,
            2
        ),
        "application/json"
    );
}


// =========================================================
// Sample data
// =========================================================

function loadSampleData() {
    sourceGeneration++;
    clearUploadView(); state.canonical = null;

    state.source =
        "sample";


    state.meta = {

        semester:
            "SAMPLE",

        week:
            1,

        weekStart:
            "2026-01-05",

        weekEnd:
            "2026-01-11"

    };


    state.events = [

        {
            id: "sample-1",
            subject: "BACS3413",
            subjectName: "Project II",
            day: "monday",
            date: "2026-01-05",
            start: "10:00",
            end: "12:00",
            venue: "DK A",
            type: "Lecture",
            lecturer: "",
            classStatus: "normal",
            source: "sample",
            completeCandidate: true,
            confidence: null,
            status: "accepted",
            selected: false
        },

        {
            id: "sample-2",
            subject: "BMIT3273",
            subjectName: "Cloud Computing",
            day: "tuesday",
            date: "2026-01-06",
            start: "14:00",
            end: "16:00",
            venue: "B001K",
            type: "Practical",
            lecturer: "",
            classStatus: "replacement",
            source: "sample",
            completeCandidate: true,
            confidence: null,
            status: "review",
            selected: false
        }

    ];


    setText("documentName", "Synthetic demonstration sample");
    setText("runStatus", "Synthetic sample — not assessed");
    setText("documentMeta", "Synthetic test data — not a verified timetable");
    setText("processingStatus", "Demo only — M5 NOT ASSESSED; calendar BLOCKED.");
    const preview = document.getElementById("documentPreview");
    if (preview) {
        preview.replaceChildren();
        const text = document.createElement("pre");
        text.textContent = ["Synthetic demonstration sample", ...state.events.map(e => `${e.date} ${e.day} ${e.start}–${e.end} ${e.subject} ${e.venue}`)].join("\n");
        preview.append(text);
    }
    renderEverything();

    showView(
        "review"
    );
}


// =========================================================
// Loading
// =========================================================

function showLoading(
    visible,
    title = "",
    text = ""
) {

    const overlay =
        document.getElementById(
            "loadingOverlay"
        );


    if (!overlay) {
        return;
    }


    overlay.classList.toggle(
        "visible",
        visible
    );


    overlay.setAttribute(
        "aria-hidden",
        visible
        ? "false"
        : "true"
    );


    if (title) {

        setText(
            "loadingTitle",
            title
        );

    }


    if (text) {

        setText(
            "loadingText",
            text
        );

    }


    const progress =
        document.getElementById(
            "loadingProgress"
        );


    if (progress) {

        progress.style.width = "";
        progress.classList.toggle("indeterminate", visible);

    }
}


// =========================================================
// Pipeline
// =========================================================

function markPipelineComplete() {

    document
        .querySelectorAll(
            "#pipeline li"
        )
        .forEach(item => {

            item.classList.remove(
                "active"
            );

            item.classList.toggle("done", item.dataset.step === "extract");

        });


    const status =
        document.getElementById(
            "runStatus"
        );


    if (status) {

        status.textContent = "Structured source imported — NOT ASSESSED";

        status.className =
            "status-label success";

    }
}


// =========================================================
// Toast
// =========================================================

function showToast(
    title,
    message,
    isError = false
) {

    const region =
        document.getElementById(
            "toastRegion"
        );


    if (!region) {
        return;
    }


    const toast =
        document.createElement(
            "div"
        );


    toast.className =
        `toast ${isError ? "error" : ""}`;


    toast.innerHTML = `

        <div>

            <strong>
                ${escapeHtml(title)}
            </strong>

            <span>
                ${escapeHtml(message)}
            </span>

        </div>

    `;


    region.appendChild(
        toast
    );


    setTimeout(
        () => toast.remove(),
        3500
    );
}


// =========================================================
// Helpers
// =========================================================

function setText(
    id,
    value
) {

    const element =
        document.getElementById(id);


    if (element) {

        element.textContent =
            value;

    }
}


function formatFileSize(bytes) {

    if (bytes < 1024) {
        return `${bytes} B`;
    }


    if (
        bytes
        < 1024 * 1024
    ) {

        return `${
            (
                bytes
                / 1024
            ).toFixed(1)
        } KB`;

    }


    return `${
        (
            bytes
            / 1024
            / 1024
        ).toFixed(1)
    } MB`;
}


function escapeHtml(value) {

    return String(
        value ?? ""
    )
        .replaceAll(
            "&",
            "&amp;"
        )
        .replaceAll(
            "<",
            "&lt;"
        )
        .replaceAll(
            ">",
            "&gt;"
        )
        .replaceAll(
            '"',
            "&quot;"
        )
        .replaceAll(
            "'",
            "&#039;"
        );
}


function downloadFile(
    filename,
    content,
    type
) {

    const blob =
        new Blob(
            [content],
            { type }
        );


    const url =
        URL.createObjectURL(
            blob
        );


    const link =
        document.createElement(
            "a"
        );


    link.href =
        url;

    link.download =
        filename;


    link.click();


    URL.revokeObjectURL(
        url
    );
}
