# Operational grounded analysis and source-backed calendar contexts

This implements application code and synthetic verification. It does not enable the registered RQ4 experiment, create independent source verification, freeze research settings or claim empirical performance. The report was inspected; the separate approved Supervisor/Moderator Review and Supervision Answers remain unavailable. Research RO1–RO4/RQ1–RQ4 and baseline definitions are unchanged.

## What a student sees

The existing whole-timetable check now has **Optional AI study suggestions**. A source owner must first complete source checking and obtain the existing independent verification. Enter explicit availability, read the minimized-data explanation, check consent and request suggestions. Each request resets consent afterward. Changed source evidence, checklist edits or expired verification prevent old suggestions from being used. Personal account checkboxes still cannot issue independent verification.

The operational provider receives only aggregate facts, opaque fact/window IDs, busy intervals, scope dates and term bounds. It never receives source text, images, course/lecturer/room labels, timetable identity, verification tokens or credentials. It returns structured fact claims and selected study/project windows; the server checks exact fact values, allowed windows, duplicates, overlaps and no-window claims. Displayed prose is rendered locally from checked structure. This conservative output design is provisional operational guidance and requires review before use as the frozen experimental P1-A condition. Unrestricted generated prose is not accepted as verified fact.

Missing configuration, independent verification, availability or consent produces NOT_EXECUTED with a reason. A started request that fails, times out or returns unsupported content produces a facts-only fallback. No automatic retry or regeneration occurs. Verification is rechecked after generation, and the protected source-check route rereads source state before returning output. No-calendar side effects occur.

## Provider configuration

The recommended candidate is OpenAI [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), pinned to `gpt-5.4-mini-2026-03-17`, using `https://api.openai.com/v1/chat/completions`. This is a configuration recommendation, not a frozen research choice or proof of account availability. The transport remains configurable for a compatible endpoint. It fixes temperature 0, reasoning effort none, 700 completion tokens, 30-second I/O timeout, a 30-second orchestration deadline, zero transport/application retries, no redirects and `store: false`. The required JSON response must finish normally; truncated or oversized responses are rejected. Async/streaming behavior follows the [HTTPX documentation](https://www.python-httpx.org/async/).

OpenAI's [data controls](https://developers.openai.com/api/docs/guides/your-data) distinguish no training from retention: normal abuse-monitoring retention can be 30 days. `store: false` does not establish zero retention or a particular processing region. Record actual account/project, region, training exclusion, retention, access, subprocessors and deletion conditions; do not assume regional processing or zero-data-retention eligibility.

Keep provider configuration and all approval/policy evidence outside Git. The `ProviderSettings` contract in [provider.py](../backend/analysis/provider.py) defines mandatory fields. `privacy_record`, `api_policy_record` and `deletion_record` each reference an actual file relative to that private configuration directory, with SHA-256. File traversal, missing/changed records and changed configuration fail closed. `approved_by`, `approved_at`, `approval_record_id`, actual model revision/region/account-setting record, training exclusion, retention and access fields must be supplied by the real approval procedure. Hash matching establishes integrity, not that a human actually approved it. No program creates those approvals.

Inspect the schema and current prompt digest without networking:

```powershell
.venv\Scripts\python.exe -B -c "import json; from backend.analysis.provider import ProviderSettings; print(json.dumps(ProviderSettings.model_json_schema(), indent=2))"
```

Set `STAIR_ANALYSIS_API_KEY` privately in the server's environment. Never paste the key into chat, source files, screenshots or an approved configuration document. Start the existing independent-review host with its approved provider file and separately trusted hash:

```powershell
.venv\Scripts\python.exe -B tools/run_student_review.py --analysis-provider '<private approved provider JSON>' --analysis-provider-approval-sha256 '<actual trusted SHA-256>'
```

The account launcher accepts the same two flags. This does not create a per-account independent reviewer or completeness authority. The existing Windows independent-review host retains that workflow. On ordinary startup no provider is installed, no key is read for a request and no external call occurs. The generic analysis route cannot mint verification or enable registered research.

## Teaching weeks, holidays and replacement classes

Strict output can load an approved source-specific `CalendarContext` using `--calendar-context` and `--calendar-context-approval-sha256`. [calendar_context.py](../backend/student/calendar_context.py) defines `approved-calendar-context-v1`: real approval metadata and unique records bound to source ID, source hash and the complete current evidence digest. Timezone, term, teaching weeks and exceptions must reference authority evidence already present in that registered source packet. Analysis-only temporal projections are never promoted into ACCEPT or calendar evidence.

In strict weekly mode, the requested range selects occurrences from the source mapper. Teaching-week exclusions, explicit cancelled dates and replacement/included dates determine actual occurrences, including a replacement on a different weekday. Requested ranges cannot extend beyond the source term. Empty/ambiguous mappings fail closed. Occurrence keys bind the account, stable source-session identity and actual source date; repeated preview/application retains the existing idempotency controls. File changes and new source evidence require a newly approved context and fresh preview/approval. This does not silently infer term dates or grant source-lineage replacement permission.

## Verification and remaining boundaries

Tests use invented sources, mocked verifiers and HTTP MockTransport. No real AI request, actual human review, formal held-out evaluation or external calendar mutation was performed. The raw provider response is used transiently for validation, not logged or retained by this application module. The formal RQ4 protocol still requires approved frozen input/window/provider configuration, independent reference schedules, retained protected raw/delivered audit artifacts and ratings under its own retention procedure; that experiment remains disabled. Operational output cannot substitute for those results. Real API compatibility, account-region/privacy acceptance, browser visual/assistive-technology checks and integration with genuinely approved source evidence remain acceptance work.
