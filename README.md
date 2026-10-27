# STAIR-Timetable

Schema-and Temporal-Consistency-Aware Timetable for Safe Calendar Synchronisation.
Software Engineering FYP, FastAPI backend and HTML/CSS/JavaScript website.

This is the complete bounded V1.0 prototype source, integrated into the existing repository on `feature/complete-v1-handover` at the team leader's direction. It awaits final Commit/Push review; it is not yet on public main. All original starter document paths and the starter commit are preserved. `PUBLIC-STARTER-MANIFEST.json` describes that historical starter, not the current application. The team leader manages source-sharing/publication permissions; this integration makes no new license or research-result claim.

## Implemented application scope

- Bounded PDF/PNG/JPG evidence extraction; separate native PDF and optional English Tesseract OCR routes, coordinates and provenance.
- Registered B1/B2 adapters; separately versioned B2H-v1; P1 development reconstruction/constraints and matched B3 contracts.
- Opt-in M5.2 layout, S2a ruled-grid and S2b colored-block development profiles.
- Canonical event/evidence contracts, conservative TAR UMT structured-source adapter and mocked portal tests.
- Responsive website, evidence inspection, protected human-review APIs, read-only calendar diagnostics, isolated TEST_ONLY services and grounded schedule analysis.

Ordinary website review/calendar mutations remain default-deny. No reviewer grants, credentials, private timetables, historical journals, Gold or held-out material are included. TAR UMT live authentication is optional external functionality; local demonstration/tests need no university account. Real recognition accuracy, full-schedule verification and formal research evaluation are not established by synthetic software tests. History/export/persistence and authorization limitations are documented in [status](docs/scope-and-status.md).

## Run locally

Use Python 3.12, Node for frontend regression tests, and separately installed English Tesseract for live OCR. Follow [setup](docs/setup.md), [public test inventory](tools/public_test_inventory.json) and [architecture](docs/architecture.md). The backend runs on loopback port 8000; serve only `frontend/` on loopback port 5500. No npm packages or frontend build are needed.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install --only-binary=:all: -r requirements.txt -r requirements-ocr.txt -r requirements-test.txt -c requirements-team-lock.txt
.\.venv\Scripts\python.exe -B -m pip check
.\.venv\Scripts\python.exe -B tools/run_public_tests.py
```

For live image/PDF OCR, select your own separately installed Tesseract executable in the backend terminal (English data required; no global PATH change):

```powershell
$env:STAIR_TESSERACT_CMD = 'C:\Tools\Tesseract-OCR\tesseract.exe'
& $env:STAIR_TESSERACT_CMD --version
& $env:STAIR_TESSERACT_CMD --list-langs
```

Start the backend in that terminal:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal at the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Open http://127.0.0.1:5500; API documentation is http://127.0.0.1:8000/docs. Stop each service with Ctrl+C. After the owner pushes this integration, teammates can clone `feature/complete-v1-handover` as described in [setup](docs/setup.md). No university account or private dataset is needed for synthetic development.

See [publication gates](docs/publication-checklist.md) and [source clearance](docs/source-sharing-status.md). The 844 historical local tests are not a public checkout result. Actual integrated-checkout results are recorded in [installation and tests](docs/installation-and-test-results.json). RO1–RO4/RQ1–RQ4 and approved research gates remain unchanged; 1,934 pre-annotations are not independently verified Gold.
