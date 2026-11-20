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

## Copied timetable text

Use **Or paste your timetable** on Upload, then **Read pasted timetable**. Review the extracted classes and original copied text. Text is limited to 128 KiB / 2048 lines and uses the evidence-only application reader, never an OCR baseline. Its original visual layout is unavailable.

For editing, click **Save original text for editing access** in the source preview. Personally authorize that exact downloaded `.txt` file with the same console procedure below. Do not retype or reformat it: even whitespace changes produce a different source hash. Reselect the saved `.txt` file after restart, request fresh access and reconnect saved reviews. This does not fabricate a correction or independent verification.

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


## Whole-timetable check and workload (FR7)

After reconnecting the exact source and current saved corrections, open **Schedule Analysis**. Enter the dates and source timezone; specify a source-supported class group when requesting verified analysis. **Show observed workload** reports printed local class slots, hours, active-day workload and possible overlaps without claiming that blank time is free.

Choose **Start whole-timetable check**, then **View original**. Inspect every original page beside the reconstructed classes. PDFs use a locally rendered page image from the exact original bytes; choose each page, including blank pages. Rendering checks the authorized source hash and page, has bounded size/time, and runs in a separate process. The full-source link remains available. For each source session record its inclusion, omission or uncertainty, original page/location and linked class evidence. Add missed sessions explicitly. Check every page and confirm the scope only after inspecting it; save your check. Missing/uncertain sessions, duplicate mappings, unresolved fields and pending revalidation block completeness. Correct errors through **Edit class** and revalidate before starting a fresh check.

Checks use a separate protected DPAPI journal beside the existing review journal, with the suffix `-ScheduleChecks`. Corrections are not migrated or rewritten. **Reopen saved check** recovers the source-bound inventory after reauthorization; changed source, evidence or revisions invalidate it. Historical checks survive restart, but live independent authority does not.

A primary owner check never establishes independent verification, Gold, ACCEPT or calendar approval. The local host now supports **owner-consented independent Windows reauthentication**. It remains bound to `127.0.0.1:8020`; it neither switches the host account nor shares DPAPI files with another user. The reviewer must be a real second person using a distinct local/domain Windows account accepted by this laptop, with a working Windows account password. A typed name, an owner session, a PIN-only Windows Hello credential or an unavailable account cannot substitute for a successful Windows password logon. Distinct accounts do not by themselves prove distinct people; do not use an owner's alternate account to manufacture independent review.

### Genuine two-person local workflow

1. Start the existing host from an interactive PowerShell window: from the repository root, run `.\.venv\Scripts\python.exe -B tools/run_student_review.py`. Upload the exact original file and reconnect the owner source grant normally. Existing OCR2 corrections are not resubmitted.
2. In **Schedule Analysis**, start/reopen the source check. Inspect all original pages and every source session. Under **Confirm timetable dates, weeks and class group**, enter only source-supported information, with original readings, page links and explicit confirmation. Add each teaching-week date mapping when required. Class exceptions apply only where the source says that particular class does not meet. Blank values stay unknown; `none` is an explicit assertion requiring source support. Context is an analysis-only descendant of the current saved revisions, not a rewrite of extraction or correction history. Saving it invalidates prior inventory confirmation and verification. Recheck and save the inventory afterwards.
3. Expand **Invite an independent reviewer**. The owner personally runs `invite-check <check ID> "<original file path>"` in the existing host console. It checks the exact original bytes and inventory version. The invitation lasts ten minutes and grants no access by itself.
4. The real reviewer opens the returned link in a **separate Chrome Guest window** and clicks **Request reviewer sign-in**. In the host console, personally run the displayed `authenticate-reviewer <nonce>` command within two minutes. The reviewer enters **their own** Windows account password in the native Windows dialog, never in Chrome or chat. Windows validates the account; the returned token SID must differ from the owner and all correction authors. No process impersonation or account switching occurs. The token is closed and native password buffers are cleared; account passwords are never journaled, logged or stored in browser storage.
5. Click **Connect reviewer**. The reviewer sees the exact original, current inventory and source context using a separate 15-minute read-only cookie/channel. Inspect every source page and every current class, including omissions, recurrence, holidays and source-supported scope. Check the three confirmations, enter a genuine independent note and click **Confirm my independent check**. This only prepares a short-lived confirmation; it does not record verification yet.
6. The reviewer personally runs the displayed `confirm-reviewer <nonce>` command in the host console. The native dialog explicitly identifies this as **verification**, shows the date range and source fingerprint, and requires the **same reviewer's** Windows password again. A wrong identity, cancellation, stale check, missing context or normalization failure records no successful verification. Refresh the reviewer page after the console confirms success.
7. Enter personal availability and analyze the checked scope. Completeness alone never supplies personal availability. Both users can end access; the owner can run `revoke-reviewers` to revoke all reviewer channels and live proofs. A browser reload/restart requires fresh reviewer authentication; historical verification is retained but never silently reactivated.

A separate Windows account must actually exist and support this authentication method; STAIR does not create accounts, change account policy, collect passwords through HTTP or enable remote access. The Windows APIs used are [CredUIPromptForCredentialsW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-creduipromptforcredentialsw) with credential persistence disabled and [LogonUserW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-logonuserw) with an interactive authentication token. The token is used only to read the authenticated identity. Same-account malicious software or a compromised Windows owner session is outside this local trust boundary.

Only a complete live verified scope with resolvable source timezone, dates/recurrence, class-group membership and explicit personal availability can produce deterministic study/project windows. Unknown semester boundaries or teaching-week authority are never inferred; weekly schedules without the existing required temporal authority remain blocked. The local source-context interface supports finite dated schedules and explicitly evidenced weekly schedules, including week mappings and per-class exceptions. It cannot infer absent semester boundaries, timezone, class-group membership or holiday rules. Multiple class groups must be resolved before whole-source completeness can pass. Class contact hours and occupied hours are distinct when classes overlap. Weeks are labelled by calendar-week start, not invented teaching-week numbers. Synthetic verified browser fixtures exercise software behavior only. No external AI provider or calendar mutation is used.


### Source-check draft safety and recovery

Typing in a whole-source check creates an **Unsaved draft**, not a saved check or independent verification. After a short pause, an authorized primary owner gets an encrypted recovery copy in the existing private schedule-check journal. It includes typed row statuses, source locations, selected evidence, notes and temporal-form controls, even when an entry is too incomplete to submit. Drafts are bound to the exact check/source/current revisions and primary actor; other reviewers cannot read them. No timetable text, passwords or tokens are written into browser storage. Browser storage contains only opaque check/client IDs.

A failed **Save my source check** keeps the form intact and identifies the class/field to fix. For **Included** classes, explicitly select the original class passage inspected; typing a location or note alone does not select evidence. Original source locations allow 200 characters and notes 2,000 characters. Recovery can retain over-limit text while you shorten it. Source-context projections retain the class's original evidence choices. Nothing automatically changes a class to Included or Verified.

**Reopen saved check** warns before replacing unsaved edits. Cancel keeps the form. After an explicit replacement or a restart/reconnection, **Recover my unsaved entries** offers the recovery copy as an unsubmitted draft. Restored confirmations remain draft input until a new explicit save. New edits made while loading or saving are retained. Saving the primary inventory also preserves unsaved temporal-form work. Checked-schedule analysis requires unsaved edits to be resolved first.

After a successful source save, the UI shows **Saved source check**, its Check ID and **Copy Check ID**. Incomplete saved checks still need attention; only a current separate verifier proof displays **Independently verified**. Recovery drafts never create a source-inventory SAVE or verification audit entry.

If recovery cannot be written (for example, authorization expired), keep the tab open and reconnect; the UI warns that only the displayed copy is confirmed. The browser warns before leaving an unsaved form. After a backend restart, authenticate for the exact original source, reopen its check, then explicitly recover any offered draft. Recovery is restricted to the matching source/revisions; stale inputs are never silently applied to changed classes.

The older UI did not persist failed submissions or unsaved form values. Entries already overwritten by its Reopen action cannot be reconstructed from a START-only audit. Keep any other tab or existing copy; never recreate human check notes from machine predictions.


Navigation between Review Classes and Schedule Analysis keeps the current checklist and unsaved form controls. Reconnecting an already approved source refreshes its saved classes without discarding the checklist. Schedule Analysis discovers only the current owner's checks bound to the exact source hash and revisions; an existing recovery draft is preferred over an empty newer check. If several drafts match, choose explicitly. Opening an existing check restores empty date/timezone controls from its stored scope and offers **Recover my unsaved entries** without creating a new START or verification record. Changed revisions remain blocked, with a specific explanation instead of an incorrect reconnect instruction.


### Normal workload analysis (no review sign-in)

Open **Analyze My Schedule** after an upload or copied-text extraction. The default summary counts supported printed class slots, reports observed daily hours, busy/light active days, occupied times and possible conflicts. Dates are optional filters; supported detected dates and a consistent explicit source timezone can prefill the controls. No semester or recurrence is invented, and unknown timezone does not prevent printed clock-duration arithmetic. Exact duplicate readings are counted once and flagged. Blank days are not confirmed free time.

**Advanced / Research Verification (optional)** retains the protected source checklist, encrypted drafts, Check IDs and independent Windows review. Expand it to reconnect/recover a saved check. Verified study/project windows still require the original complete-source and temporal authority, plus explicit availability. The observed-only endpoint accepts no attestation or approval fields and never enables calendar writes. Connected saved corrections are used in the normal summary; disconnected private revisions are not loaded or disclosed.
