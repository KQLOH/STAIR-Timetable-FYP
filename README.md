# STAIR-Timetable

Schema-and Temporal-Consistency-Aware Timetable for Safe Calendar Synchronisation.
A Software Engineering FYP prototype with a FastAPI backend and HTML/CSS/JavaScript frontend.

The working system provides timetable extraction, source-linked class review, explicit human corrections, revision tracking, revalidation and a read-only calendar preview. The optional Windows review host authenticates the local Windows account and requires console consent for the exact uploaded file. It stores review history locally using user-scope DPAPI encryption.

Use the branch shared by the project owner. Local changes are not visible on GitHub until the owner confirms a commit and push. See [supervisor review and publication](docs/supervisor-review.md).

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

Open **http://127.0.0.1:8020** in Chrome. This single process serves the frontend and backend. It requires an interactive console. Upload your own timetable, request access in the page, and personally authorize the displayed nonce for the matching original file in the console. See the [complete correction/retry/restart instructions](docs/local-student-review.md).

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

## Verify

```powershell
.\.venv\Scripts\python.exe -B tools/run_public_tests.py
.\.venv\Scripts\python.exe -B -m starter_tools.markdown_links --root .
```

The runner executes the Python regression suite, frontend checks and Node transport security checks. Windows-specific DPAPI tests require Windows; live OCR checks require English Tesseract. Missing external runtimes must be reported rather than counted as successful tests. No real review session or calendar account is needed for synthetic verification. See [setup details](docs/setup.md) and [current scope](docs/scope-and-status.md).

## Scope and safety

- Bounded PDF/PNG/JPG extraction, native-PDF/OCR route separation, S2a/S2b layout profiles, RT4 contextual OCR correction and constrained application reconstruction are implemented. Different layout families still have important limitations; this is not a universal timetable recognizer.
- B1, original B2, separate B2H and Strong B2 remain distinct methods. Source and comparison tools are included; private sources, checked references, captures and research results are not bundled.
- A human correction creates an application revision. It does not establish independently VERIFIED Gold, M5 ACCEPT, complete-schedule verification or calendar approval.
- Calendar preview remains read-only. Production calendar writes are blocked; TEST_ONLY services use isolated synthetic fixtures.
- OCR1–OCR6 and the legacy CUI dataset are development-exposed or have uncertain exposure. They do not establish untouched independent generalization results. Generalization experiments are paused pending a properly separated additional sample set.

RO1–RO4/RQ1–RQ4 and research gates remain unchanged. Historical starter/release manifests and earlier installation reports describe their original snapshots, not the latest working tree. Source sharing is managed by the project owner; no new license or publication permission is implied.
