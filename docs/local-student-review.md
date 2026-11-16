# Local student correction on Windows

This opt-in host uses the Windows process logon SID plus your explicit console confirmation for an exact source file. It is local application authentication, **not TAR UMT SSO or independent research verification**. Normal `backend.main:app` startup remains read-only. No hard-coded reviewer, token or public grant endpoint is installed.

## Start the host yourself

Open an interactive PowerShell window:

```powershell
# Run from your selected repository checkout.
whoami /user
$env:STAIR_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
.\.venv\Scripts\python.exe -B tools/run_student_review.py
```

Check that `whoami` shows your own Windows account; its SID should match the eventual browser actor reference (prefixed `windows-logon:`). Keep this console open. Open **http://127.0.0.1:8020** in Chrome. Do not use the ordinary port 5500 page for this workflow. The new host serves the existing frontend and backend together on loopback; it needs neither a separate 5500 server nor a university password.

If port 8020 is occupied, stop the earlier STAIR host in its own console; do not kill unrelated processes. The port/origin are deliberately fixed. There is no remote/shared-computer deployment mode.

## Authorize only your uploaded source

1. Choose **Colored timetable** in the **Timetable type** dropdown for a colored-block source; use **Grid timetable** or **Standard image or PDF** for the other supported layouts. RT4 is enabled by default for Colored mode and can be inspected in Advanced Details. Upload your original timetable file. Inspect the class cards and **View source** crops.
2. Click **Request access to this timetable** in the local review panel.
3. At `STAIR owner>` in your PowerShell console, personally type the following, replacing the nonce with the one displayed in Chrome:

   ```text
   authorize <displayed nonce> "C:\path\to\your-timetable.jpg"
   ```

   The request expires after 120 seconds. The host checks the original file's SHA-256, the pending browser channel and the current Windows logon before granting 30-minute access to that exact document/hash. This does not submit a field correction or approve a calendar.
4. In Chrome, click **Connect approved review**. Matching saved revisions now load directly into the main class cards and calendar preview. Use **Reconnect saved reviews** after re-uploading a source without an active grant. No correction is resubmitted by reconnecting. Actor/source details remain in **Advanced review workspace**.
5. For existing saved reviews, do not queue or submit anything merely to display them. Use **Edit class** only when another edit is needed. Advanced queue controls remain available for inspection. If **Needs review** remains after values reconnect, inspect unresolved fields or use **Check saved class** for a genuinely pending revalidation; absence of independent verification alone does not count as a pending correction.

No account name entered in a webpage grants authority. Cookies are HttpOnly/SameSite Strict; the separate request binding remains in browser memory. Neither credentials nor grants are saved to web storage or restored after a restart. Do not share the console or Windows account with another reviewer.

## Submit a correction yourself

For each task, inspect the original crop before editing. In the task drawer:

1. Select the field. For an unresolved venue, change **Field state** to **PRESENT**.
2. Enter your personally checked reading in **Raw source reading** and the intended value in **Normalized value**.
3. Select the original evidence belonging to this class. For a reading that differs from OCR, check **I read this value in the original source...** and provide a justification. Do not select unrelated classes or old human assertions as the original evidence.
4. Inspect **Alternatives**. If your source inspection resolves the ambiguity, explicitly remove obsolete alternatives (`[]`); otherwise retain the uncertainty. Do not claim the field is resolved while preserving competing readings unintentionally.
5. Provide a correction reason, click **Add / replace field change**, inspect the pending proposal, then personally submit it using **Submit source-supported correction**.
6. Click **Revalidate current revision**. Inspect its result and revision-bound read-only preview. A correction invalidates dependent sibling assessments: after finishing both events, revisit and revalidate any task marked **REVALIDATION_REQUIRED**. Editing does not establish ACCEPT, VERIFIED, Gold or calendar approval. Existing identity/context/verification restrictions may keep all operations blocked.

The human-assertion checkbox must be checked **before** clicking **Add / replace field change**. A pending proposal is a snapshot: checking the box afterwards does not change it. Click **Add / replace field change** again and verify the pending row explicitly says **explicit human assertion, not original OCR** before submitting.

If a previous attempt returned `UNSUPPORTED_CORRECTION_VALUE`, its explanation/reason alone did not constitute a structured human assertion. Keep the failed record. For an **ESCALATED** task, use **Assign to me** and **Start / resume assigned review** to begin a fresh attempt with a current ETag. Select the field first, enter the reading, check the human-assertion box, add/replace the pending change, verify its annotation, then submit a new request. Do not resubmit an old proposal or requeue the source.

Use **Review history** to inspect original prediction, proposal, real actor/time, revision links and validation. Corrections to different events need separate task submissions. Existing OCR2 reference values are for you to inspect and confirm; the application does not insert them automatically.

## Reload and backend restart

- Default storage: `%LOCALAPPDATA%\STAIR\StudentReview`. Missing directories are created with a protected current-user/SYSTEM ACL. Storage must remain beneath the Windows Known Folder LocalAppData root; custom paths outside that root are rejected. Microsoft Store Python may redirect the logical path into its user-local package cache; use the **Encrypted storage** path printed by the host to locate the actual files. This is supported redirection, not permission to traverse a junction or symbolic link. It is outside Git. Full original predictions, OCR evidence, corrections, audit/revision histories and validation outcomes are protected using Windows user-scope DPAPI. [Microsoft DPAPI documentation](https://learn.microsoft.com/en-us/windows/win32/seccrypto/example-c-program-using-cryptprotectdata).
- Type `quit` in the host console to stop. Restart with the same PowerShell commands and Windows account. Do not change the storage path between runs.
- Reloading Chrome or restarting the backend requires a **new** access request and your new console authorization. Choose the same timetable type and correction settings as the original upload, and upload the same original file to restore image/crop viewing, then reconnect and open the existing queue. Do **not** requeue a duplicate or resubmit the previous correction.
- Use **Review history** to verify the original correction timestamp and revision survived. A current task snapshot drives both class cards and the read-only preview; old approvals/previews are not reused.
- **End authorized review session** revokes access. It retains encrypted records. No production calendar writes are provided.

## Storage and recovery limits

The host uses an append-only encrypted snapshot journal with a head marker, OS writer lock, bounded records, audit checks and atomic publication. Sessions/bearer tokens are excluded. Corruption, missing records, wrong-owner data and write failure fail closed instead of claiming success or silently starting empty.

Keep the Windows account/profile and encrypted review directory backed up privately. These files are tied to the Windows DPAPI context, not a portable plaintext export. They are not independently signed research evidence, Gold, university authentication or a multi-user hosted database. Same-account malicious software is outside this local trust boundary. A lost profile/key may make the data unrecoverable.

Do not delete or edit a corrupt journal to unblock a demo. Preserve it and diagnose the failure separately. An interrupted uncommitted publication may require recovery; there is no automatic rollback to an older revision. Original image/PDF bytes remain your local file; the journal preserves text/coordinate evidence, not an uploaded-image archive.
