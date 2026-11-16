# Setup for the selected working-system branch

The README in the branch being reviewed is the primary setup guide. The repository's default branch can lag the owner's unpublished local work. Clone the **published branch supplied by the owner**, not an assumed historical release branch.

After the proposed supervisor branch has actually been approved and published, an example checkout is:

```powershell
git clone --branch codex/supervisor-review-20261116 https://github.com/KQLOH/STAIR-Timetable-FYP.git
Set-Location STAIR-Timetable-FYP
```

That branch name is a publication proposal until the owner confirms it exists. If a different branch is shared, substitute that exact name. A supervisor can inspect a public branch in GitHub without cloning or using private timetable data.

## Dependencies

Windows with Python 3.12 is the target for the local Windows-identity/DPAPI review host. Node runs frontend/security checks. Install Tesseract with English data separately; binaries and OCR models are not shipped.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install --only-binary=:all: -r requirements.txt -r requirements-ocr.txt -r requirements-test.txt -c requirements-team-lock.txt
.\.venv\Scripts\python.exe -B -m pip check
node --version
$env:STAIR_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
& $env:STAIR_TESSERACT_CMD --version
& $env:STAIR_TESSERACT_CMD --list-langs
```

If `py` is unavailable, use your actual Python 3.12 executable for venv creation. Adjust the Tesseract path to the actual installation. It must list `eng`. The environment variable affects only this terminal; keep it set in the terminal running the host/backend. An untracked `config/ocr-runtime.local.json` may also be created from the public example. Never copy another machine's personal configuration or credentials.

## Windows student review

```powershell
whoami /user
.\.venv\Scripts\python.exe -B tools/run_student_review.py
```

Open http://127.0.0.1:8020. Use your own interactive Windows console and source file. Follow [source authorization, correction and restart](local-student-review.md). No university password is needed for local uploaded-file review. Windows account authentication is not TAR UMT SSO. The ordinary server does not automatically grant authority. Type `quit` to stop.

Do not delete existing storage to fix a startup problem. The host creates absent user-local directories securely, rejects unsafe paths/reparse points, and fails closed on corruption/permission failures. It prints the real encrypted storage path, including Store Python redirection. Records are not portable plaintext exports and are never a shipped research dataset.

## Read-only demo

Use two terminals from this checkout:

```powershell
# Terminal one; keep the Tesseract environment variable here.
.\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal two; serve only the frontend directory.
.\.venv\Scripts\python.exe -B -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Open http://127.0.0.1:5500 and select **Try sample**. Uploading your own source exercises extraction. Corrections remain blocked without the separate approved review host. Native PDF text does not silently replace the registered baseline OCR condition. API docs: http://127.0.0.1:8000/docs. Stop with Ctrl+C in each server terminal.

Do not bind these local prototypes to `0.0.0.0` or change ports/origins casually. If a port is busy, identify its owner and close only your own prior STAIR host. Never kill another person's process for setup.

## Regression and security checks

```powershell
.\.venv\Scripts\python.exe -B tools/run_public_tests.py
.\.venv\Scripts\python.exe -B -m starter_tools.markdown_links --root .
```

The test runner discovers current synthetic unittest modules and executes the independent Node transport security check. The JSON result reports failures, errors and skips separately. No private labels, retained journals or real review approval are required. Windows-only tests skip on unsupported operating systems; live OCR checks can skip if Tesseract is absent. Such runs are not equivalent to the fully provisioned Windows result.

See [supervisor publication](supervisor-review.md), [scope](scope-and-status.md) and [privacy](public-safety.md). Historical manifests and earlier results remain historical; they are not a current accuracy claim.
