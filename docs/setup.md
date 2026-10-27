# Complete application setup

Use Python 3.12. The verified dependency constraints target Windows/Python 3.12; another OS needs its own verification. Install Node for the JavaScript regression checks. No university account, private dataset or historical journal is required for synthetic development.

```powershell
git clone --branch feature/complete-v1-handover https://github.com/KQLOH/STAIR-Timetable-FYP.git
cd STAIR-Timetable-FYP
# Run this after the owner publishes the full-source feature branch.
# After it is merged, ordinary main checkout can be used instead.
python -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install --only-binary=:all: -r requirements.txt -r requirements-ocr.txt -r requirements-test.txt -c requirements-team-lock.txt
.\.venv\Scripts\python.exe -B -m pip check
node --version
```

Install Tesseract separately from a trusted distribution with English data. No executable/models are shipped. Set an explicit path in the current terminal; this does not edit system PATH:

```powershell
$env:STAIR_TESSERACT_CMD = 'C:\Tools\Tesseract-OCR\tesseract.exe'
& $env:STAIR_TESSERACT_CMD --version
& $env:STAIR_TESSERACT_CMD --list-langs
```

Adjust the example path to the actual local installation. `eng` must be listed. Alternatively create an untracked `config/ocr-runtime.local.json` from the example. Never commit a personal path or copy another person's local configuration. OCR absence returns an explicit diagnostic and some live-OCR tests skip; native PDF text never substitutes for the registered B1/B2 OCR route.

In terminal one (with the OCR override if needed):

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In terminal two:

```powershell
.\.venv\Scripts\python.exe -B -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Open http://127.0.0.1:5500 and http://127.0.0.1:8000/docs. The frontend API base and backend CORS agree on these ports. Serve only `frontend/`, never the repository/data root. If a port is occupied, identify its owner; do not terminate unrelated processes or change the API contract silently.

```powershell
.\.venv\Scripts\python.exe -B tools/run_public_tests.py
.\.venv\Scripts\python.exe -B -m starter_tools.markdown_links --root .
```

The test runner executes public unittest discovery plus the independent frontend transport security check. It creates fresh synthetic temporary outputs only. Historical S4B host/tests are excluded explicitly; never recreate their retained inputs or enroll a live browser just to run onboarding tests. Stop the two servers with Ctrl+C in their own terminals.

Fresh clones must not be expected to contain Gold/audit records or real university response samples. Protected review and calendar actions stay denied without a separately authorized host; the ordinary website does not grant itself authority. No real calendar integration is enabled by setup.
