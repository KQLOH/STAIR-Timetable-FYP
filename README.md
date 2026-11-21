# STAIR-Timetable

Schema-and Temporal-Consistency-Aware Timetable for Safe Calendar Synchronisation.
A Software Engineering FYP prototype with a FastAPI backend and HTML/CSS/JavaScript frontend.

## Student account application

After installing the existing dependencies below, double-click **[Start STAIR.cmd](Start%20STAIR.cmd)**, or run `.venv\Scripts\python.exe -B tools/run_student_app.py`. Open **http://127.0.0.1:8020**. Create a STAIR account, upload/paste your timetable, check or correct classes, confirm dates and repeat schedules, then preview, approve and apply changes to your private local test calendar. Ordinary students no longer need console authorization commands in this mode.

This additive account host includes per-account encrypted history, revision-bound synchronization plans, JSON export/reference comparison, personal completeness checks and optional OAuth for a configured secondary Google test calendar. It remains a local FYP application. Registered ACCEPT and independently verified analysis still require their genuine research evidence; personal confirmation does not establish them. See the **[student workflow, Google setup and limitations](docs/student-account-workflow.md)**. The older operator modes documented below remain available separately.

For the account-local strict FR5/FR6 bridge, start the same launcher with `--calendar-policy registered-accept`. This policy accepts only current AUTHENTIC registry receipts and source-supported dates, with separate preview approval. Missing approved evidence blocks output; the default personal sandbox is not automatically promoted. See [strict output requirements](docs/student-account-workflow.md#strict-registered-accept-output). Registered FR7/P1-A and formal research evaluation remain incomplete.

Operational FR7 now includes an opt-in minimized provider interface, structured fact/window post-checks and facts-only fallback. Approved source context can also supply teaching weeks and replacement dates to strict output. Neither feature fabricates approval or enables registered experiments; see [configuration, privacy and acceptance boundaries](docs/grounded-analysis.md).

Optional Google setup: open **Review & output → Google setup guide**. The launcher accepts `--google-credentials` (a downloaded Web OAuth JSON outside Git), `--google-calendar-id` (a secondary test calendar) and `--check-google-config` for offline validation. No real Google account is configured by the repository and no write occurs from connecting alone. See [Google setup and acceptance](docs/student-account-workflow.md#optional-google-test-calendar).

The working system provides timetable extraction, source-linked class review, explicit human corrections, revision tracking, revalidation and a read-only calendar preview. The optional Windows review host authenticates the local Windows account and requires console consent for the exact uploaded file. It stores review history locally using user-scope DPAPI encryption.

Use the working-system checkpoint branch below. The project owner controls publication; setup details are in [the setup guide](docs/setup.md).

```powershell
git clone --branch codex/student-system-completion https://github.com/KQLOH/STAIR-Timetable-FYP.git
Set-Location STAIR-Timetable-FYP
```

This checkpoint includes image/PDF/copied-text input, manual reader selection, source-linked editing and saved-review reconnection, source-review routing, observed workload analysis, and a separate owner-approved TEST_ONLY calendar sandbox. Registered ACCEPT still requires independent validation and an approved operating point; production calendar writes remain disabled. The saved-correction display fix is implemented and covered by automated DPAPI restart and synthetic Chrome tests. **Verification against a genuine authorized OCR2 Chrome session remains incomplete.** Do not interpret these checks as real extraction accuracy, independent Gold or production calendar approval.

## Install

Run from the selected repository checkout. Use Python 3.12 and Node. Install English Tesseract separately for screenshot/raster-PDF OCR; no university account or private dataset is required for the synthetic tests or built-in demo.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install --only-binary=:all: -r requirements.txt -r requirements-ocr.txt -r requirements-test.txt -c requirements-team-lock.txt
.\.venv\Scripts\python.exe -B -m pip check
node --version
```

Set the actual local Tesseract path in the terminal that runs the backend. The common Windows installation is shown below; adjust it if necessary. `eng` must be listed.

```powershell
$env:STAIR_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
& $env:STAIR_TESSERACT_CMD --version
& $env:STAIR_TESSERACT_CMD --list-langs
```

## Run with secure editing on Windows

```powershell
whoami /user
.\.venv\Scripts\python.exe -B tools/run_student_review.py
```

Open **http://127.0.0.1:8020** in Chrome. Use **Upload → Review classes → Calendar preview**. Choose the **Timetable type** yourself: **Colored timetable**, **Grid timetable**, or **Standard image or PDF** (the default). Colored mode uses S2b with RT4 enabled by default; Grid uses word OCR and ruled-cell handling; General preserves native PDF text when available. Automatic routing is off and remains an optional advanced setting. Unsupported layouts and uncertain fields remain explicit limitations. Choose **Edit class**, inspect the original, enter a correction, select the source text and personally confirm your reading before saving. Technical controls remain under **Advanced Details**.

This single process serves the frontend and backend. It requires an interactive console. Upload your own timetable, request access in the page, and personally authorize the displayed nonce for the matching original file in the console. See the [complete correction/retry/restart instructions](docs/local-student-review.md).

You can also choose **Or paste your timetable → Read pasted timetable**. Copied text uses the standard application reader, preserves its exact source hash and stays available for inspection. Use at most 128 KiB / 2048 lines. To edit or reconnect this source, save **the original text** from its preview, personally authorize that exact `.txt` file in the review console, and reselect it after a restart. Pasted text loses the original visual layout; it does not become independently verified.

Encrypted review records stay beneath Windows LocalAppData, outside the repository. Store Python may redirect storage into its user-local package cache; the host prints the actual path. Reload/restart requires fresh browser authorization, while correction history and original OCR evidence persist. Reselect the original file for image crops. Type `quit` to stop. If the port is occupied, stop your earlier host in its own console; do not terminate unrelated processes.

## Run a read-only demo

Terminal one:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Terminal two:

```powershell
.\.venv\Scripts\python.exe -B -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Open **http://127.0.0.1:5500** and choose **Try sample**, or upload your own file. API docs are at http://127.0.0.1:8000/docs. Serve only `frontend/`, never the repository/data root. This mode does not enroll a correction operator. Stop these servers with Ctrl+C in their own terminals.

## Run the local TEST_ONLY calendar

Keep Student Review open in its own tab. In another interactive Windows PowerShell console, run:

```powershell
.\.venv\Scripts\python.exe -B tools/run_calendar_sandbox.py
```

Open **http://127.0.0.1:8022** (also linked from Calendar preview). This separate page accepts only its fixed invented timetable examples; it cannot import your timetable or access DPAPI review records.

1. Click **Request sandbox access**. Expand **Owner access instructions** and personally type `authorize-sandbox <nonce>` in the launcher console within 120 seconds.
2. Click **Connect approved access**. This grants 30-minute sandbox access, without approving an action.
3. Select **Original sample**, then **Preview proposed changes**. Inspect the exact class details and any blocked reasons.
4. Personally check the confirmation box, click **Approve this preview**, then **Apply approved changes**. Approval binds the exact source revisions, target snapshot and ordered actions, and expires after 10 minutes.
5. **Check previous attempt safely** checks the recorded attempt without resending it. A fresh preview of the same sample shows NO_OP; approve it explicitly if you want to execute that plan.
6. Select **Room change example**, preview and explicitly approve the UPDATE. The same two local classes remain. Missing-timezone and incomplete-repeat examples stay blocked.

History survives restart in an ignored, owner/SYSTEM-restricted `data/temp/m6-test-local-calendar-*` directory. It contains synthetic data and a local audit journal, not DPAPI review history. Do not publish it or copy it to another account. Restart requires fresh console authorization, and a previous grant's approvals cannot authorize new execution. Keep the latest room-change example selected after applying it; earlier source revisions are rejected. Use **Advanced Details** for exact bindings, technical outcomes and recovery. `quit` stops this host. Do not delete the journal to resolve errors: corrupt or inaccessible storage fails closed.

Google Calendar and every production external write remain disabled. Sandbox TEST_ELIGIBLE is not registered ACCEPT, human verification or complete-schedule verification. No approval is created merely by connecting or previewing.

## Verify

```powershell
.\.venv\Scripts\python.exe -B tools/run_public_tests.py
.\.venv\Scripts\python.exe -B -m starter_tools.markdown_links --root .
```

The runner executes the Python regression suite, frontend checks and Node transport security checks. Windows-specific DPAPI tests require Windows; live OCR checks require English Tesseract. Missing external runtimes must be reported rather than counted as successful tests. No real review session or calendar account is needed for synthetic verification. See [setup details](docs/setup.md) and [current scope](docs/scope-and-status.md).

## Source review and registered acceptance

The student application uses the versioned `m5-source-review-v1` application policy. A source-supported candidate is routed to **REVIEW** for checking; a missing mandatory reading, proven conflict, unresolved recurrence, incomplete search or unsafe reconstruction produces **ABSTAIN**. Optional missing fields and unknown group relationships are explicit review actions. Source-supported is an evidence/rule description, not a probability or independent verification. The original `m5-development-v1` policy remains available for legacy conditions and saved runs.

Registered **ACCEPT remains disabled**. The inspected inventory does not establish an independently checked, partitioned validation set for choosing and registering an operating point. No OCR confidence cutoff or synthetic accuracy was substituted for it. The review page reports automatic coverage, source-review demand and abstention counts over the current candidate population, including unresolved candidates. Accuracy and unsafe acceptance stay unmeasured without a bound genuine reference evaluation; zero automatic coverage does not establish a successful selective policy. Corrections must be revalidated against their current revision. Human review, independent verification, complete-schedule verification and calendar approval remain separate. Real uploads cannot reuse TEST_ONLY fixture permissions.

PRE-02/PRE-03 selection, trusted registration/loading and revision-bound runtime proofs are implemented. They do not supply the missing genuine validation evidence. See [FR5 operating-point commands and boundaries](docs/fr5-operating-point.md).

## Scope and safety

- Bounded PDF/PNG/JPG extraction, native-PDF/OCR route separation, S2a/S2b layout profiles, RT4 contextual OCR correction and constrained application reconstruction are implemented. Different layout families still have important limitations; this is not a universal timetable recognizer.
- B1, original B2, separate B2H and Strong B2 remain distinct methods. Source and comparison tools are included; private sources, checked references, captures and research results are not bundled.
- A human correction creates an application revision. It does not establish independently VERIFIED Gold, M5 ACCEPT, complete-schedule verification or calendar approval.
- Calendar preview remains read-only. Production calendar writes are blocked; TEST_ONLY services use isolated synthetic fixtures.
- OCR1–OCR6 and the legacy CUI dataset are development-exposed or have uncertain exposure. They do not establish untouched independent generalization results. Generalization experiments are paused pending a properly separated additional sample set.

RO1–RO4/RQ1–RQ4 and research gates remain unchanged. Historical starter/release manifests and earlier installation reports describe their original snapshots, not the latest working tree. Source sharing is managed by the project owner; no new license or publication permission is implied.
