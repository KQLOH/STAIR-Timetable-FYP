# Student account application

This is the ordinary student workflow, built on the existing FastAPI and HTML/CSS/JavaScript application. The legacy Windows operator host remains available for its separate independent-review workflow and regression compatibility. No existing research datasets, research references, baseline programs or database schemas are modified.

## Start and use

After the README installation steps, double-click [Start STAIR.cmd](../Start%20STAIR.cmd). It starts one loopback server and opens http://127.0.0.1:8020 when ready. Alternatively run `.venv\Scripts\python.exe -B tools/run_student_app.py`. Keep that process running; stop it with Ctrl+C when finished. Students do not enter console authorization commands.

1. Create a STAIR username and password, then sign in. This account is separate from TAR UMT and Google. Use a unique password of at least ten characters.
2. Upload your original file, paste timetable text or explicitly connect Intranet. Choose the reader matching the source. Uploads connect to the signed-in account after their exact bytes match the extraction hash. Intranet connection tokens are bound to that account's current session.
3. In **Review classes**, inspect the source and use **Edit class**. Corrections retain original predictions, create new revisions and use the existing evidence and revalidation checks. Reopen saved timetables from this page. Reselect an original image/PDF to inspect its image preview after a restart.
4. In **Review & output** (the former Calendar step), select your own classes. Under **Date settings for selected classes**, choose **Once, on the dates shown in my timetable** or **Every week, until a date I choose**. Printed dates are used automatically. Only undated one-time classes need a date entered on their row. Choose the timezone once for the selection. Weekly repeats need a known start/end range; no semester is guessed. Notes and excluded teaching dates are optional. Check the single selection-confirmation box after reviewing the details.
5. Choose **Preview selected classes**. The backend checks each selected class and saves its own revision-bound schedule. Classes with missing details, stale revisions or conflicting selected times are listed individually and excluded from the preview; inspect these warnings before continuing. The preview shows the exact new, updated and unchanged occurrences that remain. Approve the entire preview once, then apply it. No calendar deletion is performed; previously synchronized dates outside the new selection remain in the target.
6. Use **My local calendar and sync history** to inspect saved occurrences and check previous attempts. The **STAIR local test calendar (this computer)** destination saves events inside your STAIR account's encrypted local records; it is not Google Calendar or Windows Calendar. Repeating an already-recorded application does not resend it. Changes to schedules invalidate pending plans; revision/target changes, expiry and a new login require a fresh preview and approval.
7. **Schedule Analysis** contains a personal completeness checklist and the existing observed workload analysis. Missing classes, wrong groups or unknown teaching breaks must be resolved before relying on a schedule. Personal confirmation never creates independent verification or verified learning windows.

The dashboard has a separate **Calendar** button, also available in the sidebar. It opens the week/month calendar of successfully saved local output. It does not include pending extraction records or approved-but-unapplied previews. After applying a local output, **Open Calendar** opens this same page. Navigate by date, search courses or venues, change the display timezone, or click a saved class for details. The **Review & output** button returns to preparing changes; it does not perform a write. The source timetable preview and output history remain in the original workflow. No drag editing, deletion, Google writes or research ACCEPT decisions are introduced by this calendar display.

**Select all** selects all classes in the currently opened timetable (up to the existing 100-class preview limit); **Clear selection** unchecks them. You can uncheck individual classes afterward. Selection does not confirm date settings, approve synchronization or include another saved timetable. Any selection change clears the current preview and its personal-check checkbox.

## JSON tools

The analysis page can export source identity, original predictions, current revisions and routing separately. A blank reference template is included in the export and can be downloaded separately. Its format is:

```json
{
  "format": "stair-personal-reference-v1",
  "source_sha256": "COPY_THE_EXACT_SOURCE_HASH_FROM_THE_EXPORT",
  "checked_by": "Actual reviewer name or reference",
  "note": "How and when this source was checked",
  "events": [
    {"candidate_id": "COPY_THE_MATCHING_CANDIDATE_ID", "content": {"course_code": "EXAMPLE101"}}
  ]
}
```

The hash/ID placeholders must be replaced. `content` follows the existing EventContent schema. Supply the personally checked full content, using null for unknown values; omitted optional fields are normalized to null. Add a reference row with a distinct identifier for a source class that the machine missed. Do not copy machine readings and call them independently checked annotations. Comparisons report missing/extra IDs, changed fields and exact content matches, separately for original and current events. These are descriptive development differences, not approved research exact-event metrics or held-out evaluation. Imported references remain user-supplied.

## Optional Google test calendar

Open the **Google setup guide** button in Review & output, or visit [the local setup page](http://127.0.0.1:8020/google-calendar-setup) after starting the account app. It shows the callback URI for the running port. The student confirmed that no Google Cloud OAuth credentials have been created yet, so live Google acceptance is still pending.

The launcher can now read Google's downloaded **Web application** client JSON directly from a private path outside the repository. It validates the client type and exact redirect URI without copying the secret into source or saving tokens. Create a secondary test calendar and supply its actual Calendar ID. First validate offline, replacing the example path and ID:

```powershell
.venv/Scripts/python.exe -B tools/run_student_app.py --google-credentials "C:/Users/Asus/Downloads/your-client.json" --google-calendar-id "your-test-calendar-id" --check-google-config
```

Remove `--check-google-config` and add `--open-browser` to launch with that configuration. The same command works in Git Bash from the project folder. Use Web application credentials, not Google's Desktop-app quickstart credentials. For another port, include `--port` and register its exact callback URI. Offline validation checks local configuration only; it does not verify Cloud API enablement, Google permissions or remote calendar ownership.

Connect Google yourself, then preview, approve and apply a selected batch. Successful output includes an **Open Google Calendar** link; select your configured STAIR test calendar there. Google output does not populate the independent local STAIR sandbox view. Disconnecting or starting a new Google connection invalidates pending Google previews/approvals on the account; completed history and saved local events remain. Tokens remain in session memory. Equivalent timestamp formatting is normalized for comparison so Google returning UTC `Z` does not create false repeated updates. Concurrent changes still require fresh review instead of overwriting.

The local calendar works without Google configuration. To enable Google, the project owner must create a Google OAuth web client, enable Calendar API, configure the consent screen/test users and register the exact redirect URL `http://127.0.0.1:8020/api/student/google/callback` (adjust the port if using `--port`). As an alternative to the private JSON launcher arguments above, existing deployments can supply these process environment variables privately before starting STAIR:

- `STAIR_GOOGLE_CLIENT_ID`
- `STAIR_GOOGLE_CLIENT_SECRET`
- `STAIR_GOOGLE_TEST_CALENDAR_ID`: the ID of a dedicated **secondary test calendar**, never `primary`.

Do not put secrets in source files or screenshots. The application requests `calendar.events` and `calendar.calendarlist.readonly`, verifies target write access and refuses the primary calendar. Google account permissions apply to the Google-granted scopes; the application itself restricts writes to the one configured target and its own deterministic event IDs. Use a dedicated test Google account/calendar for acceptance.

Click **Connect Google Calendar** in Review & output and complete Google's authorization yourself. Connection does not approve any writes. Preview and personally approve every batch. Tokens stay in server memory; sign-out, session expiry and server restart clear them. **Disconnect Google** clears this app session's tokens; revoke the app in your Google account settings to withdraw Google's grant entirely.

OAuth state and PKCE bind the callback to the login session. Deterministic IDs prevent repeat creates; updates require the previewed ETag. An intent record is saved before sending writes. A timeout or crash produces a result requiring inspection, without automatic resend. **Check saved result** reads the target; it does not write. The application does not send invitations or delete events.

Implementation follows Google's [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server), [event IDs and properties](https://developers.google.com/workspace/calendar/api/v3/reference/events), and [conditional version updates](https://developers.google.com/workspace/calendar/api/guides/version-resources). Mock tests do not establish a real Google acceptance test.

## Security and storage boundaries

The new launcher uses a separate `STAIR/StudentAccounts` directory beneath Windows user-local storage. Existing encrypted review records remain unchanged. Account state and each account's review history use the existing DPAPI journal and owner/SYSTEM ACL protections. Passwords use per-account salted scrypt hashes. Cookies are HttpOnly/SameSite; mutation requests require a same-origin check and session CSRF binding. Review bridges are per request and stores are per account. Session secrets and Google/Intranet connections are not persisted. Sign out before another person uses the browser.

This remains a **single-process Windows localhost FYP application**, not an Internet deployment or enterprise identity system. HTTP cookies are restricted to the loopback host; remote deployment would need HTTPS, a reviewed deployment/authentication design and separately approved persistence changes. There is no email verification, password reset/recovery or cross-device account service. Do not use an important password. Application capacity is bounded (100 accounts, 16 captures per account, 100 selected classes / 500 occurrences per preview).

## Research status

RO1–RO4/RQ1–RQ4, baseline definitions and formal evaluation gates remain unchanged. The full approved Research Report, Supervisor / Moderator Review and Supervision Answers were not present in this checkout during implementation; local summaries are not substitutes for those originals.

### Strict registered-ACCEPT output

Current extension: approved source-context files can now supply teaching weeks and exceptions, and operational grounded analysis has a configured-provider path with protected completeness/consent checks. See [current setup and boundaries](grounded-analysis.md). The historical limits below describe the original strict-bridge checkpoint; formal research registration/evaluation remains incomplete.

The account launcher now supports `--calendar-policy registered-accept`. This is a separate host-selected policy; ordinary browser requests cannot change it. Without an authentic approved operating point, strict output remains blocked with a readable explanation. Existing saved classes can still be viewed. Normal startup defaults to `registered-accept`. The explicit `--calendar-policy human-confirmed` option retains the separate personal application sandbox; its outputs are not FR6 registered ACCEPT results.

Start the strict policy, initially blocked when evidence is unavailable:

```powershell
.venv\Scripts\python.exe -B tools/run_student_app.py --calendar-policy registered-accept
```

When genuine PRE-01 closure, PRE-02/PRE-03 selection and explicit approval exist, add `--operating-point` and `--operating-approval-sha256` as specified in [FR5 registration](fr5-operating-point.md). Supply actual private paths and the independently obtained approval hash. No example labels, automatic audit or placeholder approvals can unlock this mode.

For every selected class the server reads its current protected snapshot, verifies an AUTHENTIC ACCEPT receipt with the existing registry, requires complete reconstruction and valid constraints, and invokes the existing M6 evidence-backed calendar mapper. Selected dates must be a subset of the mapped source occurrences. Typing a timezone, confirming a date or choosing weekly repeats cannot invent source authority. Unsupported teaching-week/exception mappings remain blocked; no authority is inferred from personal notes. Current account snapshots expose source timezone and term authority; richer week and exception contexts still require a separately approved integration.

Preview, approval and execution each recompute the receipt/source binding. Revocation, reassessment, revision changes or changed source mapping invalidate the plan. Switching host policy invalidates pending plans, including older personal previews. Strict output is restricted to the local sandbox; the HTTP route rejects Google targets before accessing their adapter. Approval remains a separate action. Saved-event details identify the policy used at save time without claiming the receipt is still current today. This bridge does not change the original isolated TEST_ONLY M6 adapter's authority rules.

The bridge closes a software integration gap; without actual independent references and approved configuration it does not establish completed FR5/FR6 research acceptance. FR7's registered C1/P1-A path remains blocked in the current analysis implementation; a frozen analysis/window configuration, approved provider/privacy integration, independent complete schedules and the planned evaluation are still outstanding. The deterministic observed facts and gated development suggestions are not the registered P1-A condition.

- FR5 registered ACCEPT remains evidence-gated. Existing operating-point loading is supported through `--operating-point` and `--operating-approval-sha256`; these must identify genuinely approved artifacts. Personal review and synthetic tests do not enable ACCEPT.
- The account calendar is a human-confirmed **application** sandbox workflow. It does not relabel arbitrary uploads as TEST_ONLY fixtures or claim registered M6 policy eligibility. The original research M6 sandbox remains separate and unchanged. Obtain supervisor review of this explicit human-confirmation path before claiming it as full FR6 research acceptance.
- FR7 personal completeness remains distinct from independent source verification. The existing independent Windows review host is retained; this account app does not create independent attestations or verified learning windows from self-registered accounts.
- No independent audit, gold freeze, test seal or held-out evaluation is performed by these buttons or software tests. The 1,934 pre-annotation rows are not promoted to independently verified Gold.
- Real Intranet success is the project owner's previously reported test result; no new institutional sign-in was performed here. Real Google OAuth/write acceptance requires owner configuration and an explicit approved test.
