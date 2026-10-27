# Full V1.0 GitHub integration review

**READY_FOR_FINAL_PUBLIC_PUSH_REVIEW.** Complete application integrated into the existing checkout on `feature/complete-v1-handover`. The owner manages source-sharing/publication permissions; no new declaration workflow was initiated. Commit, Push, merge and owner V1 sign-off have not occurred.

## Exact Git state

- Branch: `feature/complete-v1-handover`
- HEAD and unchanged main: `ea3b20132508d20b13c420bd10e9e07f2e03ac1d`
- Commit objects reachable from all refs: 1 (the preserved starter)
- Origin: `https://github.com/KQLOH/STAIR-Timetable-FYP.git`
- Tracked files modified, unstaged: **12**
- New source/documentation files, untracked: **173**
- Staged: **0**; commits created: **0**; pushes: **0**; deleted starter files: **0**
- Ignored local `.venv/` and freshly generated `data/` test outputs are excluded from publication.

## Actual integrated-checkout verification

Fresh checkout-local Python3.12.14 environment installed the three requirements plus locked constraints from40 public HTTPS wheels. `pip check` and imports pass; all40 locked distributions match. Original environment/source remain untouched.

**818/818 Python tests passed;0 failed,0 errored,0 skipped;67.084s.** Additional Node transport security check passed. Test IDs exactly match the earlier818 public inventory. Historical844 local tests are not claimed here:34 old S4B cases are excluded,8 starter documentation cases are included. Portable security assertions and original application/test/config bytes remain intact.

**30/30 actual HTTP checks pass** from the integrated servers, including real English OCR/native PDF route separation, B1/B2/B2H, S2a/S2b, reconstruction, conservative policy, read-only preview, bounded analysis, invalid input and protected403 routes. Actual1280x720 browser upload produced1 unverified candidate, retained missing date/source evidence and blocked calendar actions. Both servers and the temporary tab are stopped. Exact319/390px browser checks were not newly performed; responsive automated tests passed.

See [full factual results](installation-and-test-results.json), [teammate setup](setup.md), [exact manifest](../COMPLETE-SOURCE-RELEASE-MANIFEST.json) and [manual publication commands](publication-checklist.md). No real university login, privileged browser enrollment, real calendar, Gold/PRE changes or formal research evaluation was performed.

## Exact changed-file list

This is `git status --short --untracked-files=all`, including this report and the manifest. ` M` means modified/unstaged;`??` means new/untracked. No private runtime paths or payloads are included.

```text
 M .gitattributes
 M .gitignore
 M CONTRIBUTING.md
 M README.md
 M docs/architecture.md
 M docs/github-upload.md
 M docs/public-safety.md
 M docs/research-roadmap.md
 M docs/scope-and-status.md
 M docs/setup.md
 M docs/starter-verification.md
 M docs/team-workflow.md
?? COMPLETE-SOURCE-RELEASE-MANIFEST.json
?? backend/__init__.py
?? backend/analysis/__init__.py
?? backend/analysis/api.py
?? backend/analysis/authority.py
?? backend/analysis/models.py
?? backend/analysis/service.py
?? backend/baselines/__init__.py
?? backend/baselines/b1_ocr_regex.py
?? backend/baselines/b2_hardening.py
?? backend/baselines/b2_rule_parser.py
?? backend/baselines/b2h_v1.json
?? backend/baselines/base.py
?? backend/baselines/baseline_b1.yaml
?? backend/baselines/baseline_b2.yaml
?? backend/baselines/contracts.py
?? backend/calendar/__init__.py
?? backend/calendar/access.py
?? backend/calendar/api.py
?? backend/calendar/eligibility.py
?? backend/calendar/mapping.py
?? backend/calendar/models.py
?? backend/calendar/persistence.py
?? backend/calendar/planning.py
?? backend/calendar/sandbox.py
?? backend/calendar/testing.py
?? backend/calendar/workflow.py
?? backend/calendar/workflow_api.py
?? backend/layout/__init__.py
?? backend/layout/api.py
?? backend/layout/block_capture.py
?? backend/layout/block_grid.py
?? backend/layout/block_models.py
?? backend/layout/ruled.py
?? backend/layout/ruled_capture.py
?? backend/layout/ruled_models.py
?? backend/layout/service.py
?? backend/main.py
?? backend/models/__init__.py
?? backend/models/event.py
?? backend/models/evidence.py
?? backend/models/extraction.py
?? backend/reconstruction/__init__.py
?? backend/reconstruction/adapter.py
?? backend/reconstruction/api.py
?? backend/reconstruction/candidates.py
?? backend/reconstruction/constraints.py
?? backend/reconstruction/decoder.py
?? backend/reconstruction/models.py
?? backend/reconstruction/scoring.py
?? backend/reconstruction/service.py
?? backend/research/__init__.py
?? backend/research/agreement.py
?? backend/research/audit.py
?? backend/research/audit_detection_simulation_v1.py
?? backend/research/baseline_reader.py
?? backend/research/cli.py
?? backend/research/integrity.py
?? backend/research/inventory.py
?? backend/research/models.py
?? backend/research/readiness.py
?? backend/research/workflow.py
?? backend/review/__init__.py
?? backend/review/api.py
?? backend/review/auth.py
?? backend/review/binding.py
?? backend/review/corrections.py
?? backend/review/layout_binding.py
?? backend/review/models.py
?? backend/review/policy.py
?? backend/review/store.py
?? backend/review/validation.py
?? backend/services/__init__.py
?? backend/services/event_adapter.py
?? backend/services/extraction_adapter.py
?? backend/services/file_extractor.py
?? backend/services/normalizer.py
?? backend/services/ocr_runtime.py
?? backend/services/stair_validator.py
?? backend/services/tarumt_client.py
?? backend/services/timetable_parser.py
?? config/m4-development.json
?? config/m4_constraint_registry.json
?? config/m5-development.json
?? config/m52-layout-development-v1.json
?? config/m7-development-v1.json
?? config/ocr-runtime.example.json
?? docs/README.md
?? docs/dependency-inventory.json
?? docs/github-integration-review.md
?? docs/installation-and-test-results.json
?? docs/publication-checklist.md
?? docs/source-sharing-status.md
?? docs/team-handover.md
?? frontend/index.html
?? frontend/js/analysis.js
?? frontend/js/app.js
?? frontend/js/controls.js
?? frontend/js/intranet.js
?? frontend/js/review.js
?? frontend/js/sandbox.js
?? frontend/js/upload.js
?? frontend/styles.css
?? requirements-ocr.txt
?? requirements-team-lock.txt
?? requirements-test.txt
?? requirements.txt
?? tests/__init__.py
?? tests/fixtures.py
?? tests/m51_frontend_checks.js
?? tests/m52_frontend_checks.js
?? tests/m71_frontend_checks.js
?? tests/m7_frontend_checks.js
?? tests/portable_s4b_manifest_v1.json
?? tests/portable_s4b_spec_v1.json
?? tests/portable_s4b_v1.py
?? tests/research_fixtures.py
?? tests/s1_functional_audit.py
?? tests/s2b_fixtures.py
?? tests/s3a_frontend_checks.js
?? tests/s3b_frontend_checks.js
?? tests/s3b_workflow_fixtures.py
?? tests/s3c_fixtures.py
?? tests/s3c_frontend_checks.js
?? tests/s4a_acceptance.py
?? tests/s4b_host_bootstrap.js
?? tests/s4b_transport.py
?? tests/s4b_transport_checks.js
?? tests/s4bf1_frontend_checks.js
?? tests/synthetic_samples/s2b_preparation/01-two-row-colored.png
?? tests/synthetic_samples/s2b_preparation/02-scaled-translated.png
?? tests/synthetic_samples/s2b_preparation/03-colors-and-small-fonts.png
?? tests/synthetic_samples/s2b_preparation/04-missing-grid-rules.png
?? tests/synthetic_samples/s2b_preparation/05-unreadable-and-no-meridiem.png
?? tests/synthetic_samples/s2b_preparation/06-ambiguous-block-boundary.png
?? tests/synthetic_samples/s2b_preparation/07-transposed-unsupported.png
?? tests/synthetic_samples/s2b_preparation/08-unrelated-colored-notice.png
?? tests/synthetic_samples/s2b_preparation/09-adjacent-same-color.png
?? tests/synthetic_samples/s2b_preparation/10-touching-ambiguous-regions.png
?? tests/synthetic_samples/s2b_preparation/README.md
?? tests/synthetic_samples/s2b_preparation/generate.py
?? tests/synthetic_samples/s2b_preparation/manifest.json
?? tests/test_api_contract.py
?? tests/test_event_adapter.py
?? tests/test_event_contract.py
?? tests/test_m31_ocr_and_b2.py
?? tests/test_m3_extraction.py
?? tests/test_m4_reconstruction.py
?? tests/test_m51_integration.py
?? tests/test_m52_layout.py
?? tests/test_m5_api.py
?? tests/test_m5_policy.py
?? tests/test_m5_review.py
?? tests/test_m6_api_frontend.py
?? tests/test_m6_calendar.py
?? tests/test_m71_integration.py
?? tests/test_m7_analysis.py
?? tests/test_normalizer.py
?? tests/test_portable_s4b_v1.py
?? tests/test_research_baseline_reader.py
?? tests/test_research_readiness.py
?? tests/test_research_workflow.py
?? tests/test_s2a_ruled.py
?? tests/test_s2b_blocks.py
?? tests/test_s3a_controls.py
?? tests/test_s3b_frontend.py
?? tests/test_s3b_review.py
?? tests/test_s3c_frontend.py
?? tests/test_s3c_workflow.py
?? tests/test_s4a_acceptance.py
?? tests/test_s4bf1_frontend.py
?? tools/public_test_inventory.json
?? tools/run_public_tests.py
```
