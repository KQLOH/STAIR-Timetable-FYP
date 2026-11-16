# Current working-system scope

This describes the current working-tree implementation. Its presence locally does not mean it has been committed or pushed. The project owner controls source publication; a supervisor must use the published branch the owner shares.

| Area | Implemented | Current limit |
|---|---|---|
| Extraction | Bounded PDF/PNG/JPG handling, native PDF and optional English Tesseract OCR | External OCR runtime required; arbitrary layouts are not solved |
| Baselines | B1, original B2, separate B2H-v1, versioned Strong B2 source | No private benchmark data or formal performance claim shipped |
| Reconstruction | Generic P1 development constraints and matched B3; M5.2/S2a/S2b application profiles and RT4 | Explicit supported-layout assumptions, uncertainty and resource caps remain |
| Student review | Class cards, source inspection, explicit evidence-linked human readings, revisions, history, revalidation | Ordinary startup is read-only; editing needs source-scoped trusted-host authorization |
| Local Windows host | Genuine Windows logon SID, console/browser consent for the exact file, expiring grants | Local Windows mode only; not university SSO or a hosted multi-user identity provider |
| Persistence | User-scope DPAPI, protected Windows ACLs, append-only encrypted review snapshots, original predictions/evidence and audit/revision links | Reauthorization required after restart; source image/PDF must be reselected for crops; corruption fails closed |
| Calendar | Current-revision read-only preview and diagnostics; isolated TEST_ONLY services | Production writes remain blocked; edits do not imply ACCEPT, VERIFIED or calendar approval |
| Research tools | Generic capture/scoring/annotation/readiness source and synthetic tests | Research evidence, private captures and manually checked references are excluded from the review source set |

Generalization experiments are paused. OCR1–OCR6 are development-exposed; legacy CUI exposure is uncertain. No untouched independent layout-generalization result has been established. A passing software suite is not extraction accuracy or independently verified Gold. Post-human-correction outcomes must stay separate from machine-only extraction performance.

RO1–RO4/RQ1–RQ4 and the approved research gates remain in force. No baseline has been changed for this supervisor-preparation task. Historical starter/integration documents are retained as records of earlier snapshots; use the current [README](../README.md), [setup](setup.md) and [review instructions](supervisor-review.md) for this implementation.
