# FR5 selection and registration

Genuine ACCEPT is disabled in the default student host. The selection and
registration software does not create reference labels, certify human checking,
or approve calendar actions. Research inputs and outputs belong outside Git.

## Selection

Chapter 3 Table 3.9 and equations 4.5–4.8 govern these modules:

- `backend/review/selection.py`: PRE-02 development configuration selection and
  chained PRE-03 validation threshold selection; create-only CLI outputs.
- `backend/review/registration.py`: trusted host loader, frozen feature provider,
  proof issuance and current source/revision checks.
- `backend/review/operating_point.py`: legacy offline PRE-03 proposal utility.
  Its arbitrary-grid proposals remain non-authoritative.

From the project directory, with privately prepared inputs:

```powershell
.\.venv\Scripts\python.exe -B -m backend.review.selection pre02 --input "$private\development.json" --out "$private\pre02.json"
.\.venv\Scripts\python.exe -B -m backend.review.selection pre03 --input "$private\validation.json" --development "$private\development.json" --pre02 "$private\pre02.json" --out "$private\pre03.json"
```

`$private` must be an existing private evidence directory. Existing outputs are
never overwritten. Model contracts are `DevelopmentInput`, `FrozenConfiguration`
and `ValidationInput`; no example labels or accuracy results are supplied.

PRE-02 evaluates every supplied non-threshold configuration and the full
0.50–0.95 grid in steps of 0.05. Feasibility requires a nonzero accepted count,
zero accepted hard violations, zero harmful final states and accepted risk at
most 0.05. Feasible configurations are ordered by exact reconstruction F1,
recall, fewer REVIEW rows at the best feasible point, then the complete numeric
parameter tuple. The development threshold is discarded. The tuple must include
`wX`, `wR`, `wC`, `wP` and every other non-threshold parameter being compared.

PRE-03 cannot change PRE-02 inputs, configuration, providers or privacy hashes.
Validation feasibility requires coverage >=0.80, nonzero ACCEPT and zero accepted
hard violations. Selection minimizes exact-event selective risk, then maximizes
coverage, then minimizes threshold. The review floor is 0.40. All-fail inputs
produce an infeasible record, never an automatically relaxed threshold.

## Trusted loading

The normal startup command is unchanged. After genuine PRE-01 closure, both
selection stages and explicit operating-point approval, an operator can use:

```powershell
.\.venv\Scripts\python.exe -B tools/run_student_review.py --operating-point "$private\approved-bootstrap.json" --operating-approval-sha256 $approvedArtifactHash
```

There is no HTTP registration route or browser switch. The approval hash must
come from the trusted approval process, not from an untrusted proposal. Startup
fails closed if the evidence, chronology, approval or hashes cannot be verified.
The bootstrap has schema `approved-operating-point-v1`, origin `AUTHENTIC`, and:

- `development_path`, `pre02_path`, `validation_path`, `pre03_path`, `bundle_path`,
  `settings_path` and `evidence_root`: paths relative to its private directory.
- `approval_artifact_id` and `approval_artifact`: an authentic, hash-bound
  `operating_point_approval` artifact kept **outside** the immutable PRE-01
  bundle. This later approval binds the PRE-01 input, PRE-02/PRE-03 digests,
  configuration and pipeline, approver and actual timestamp.
- `runtime_files`: each frozen artifact maps to `{root, path}`, with `root`
  either `project` (the running project) or `evidence` (the private evidence root).

Required artifact categories are code, evidence, candidates, rules, scoring,
routing, calendar, baselines, annotations, splits, provider, privacy, analysis and
seeds. The code artifact must be a `freeze-file-set-v1` JSON manifest whose
nonempty `files` entries contain `root`, `path`, `sha256`. Other file-set manifests
are checked transitively too. Runtime members are rehashed; changed files revoke
the registration. Hashes do not themselves prove completeness or human approval.

Authentic loading rechecks PRE-01, the frozen Gold manifest, every registered
development/validation page, source hashes, reference provenance and temporal
ordering. The native feature provider recomputes the frozen M4 ranking indices;
unsupported profiles, missing features and corrected revisions cannot inherit
old scores. Uncalibrated indices are never labelled probabilities. The approved
provider and pipeline must match the actual runtime; a CUI operating point does
not automatically apply to TAR UMT HTML or another reader.

## Runtime and calendar boundaries

The authenticated review store consumes an explicitly installed registry.
Anonymous assessments never gain authentic ACCEPT authority from caller-supplied
data. Native ranking, registered eligibility, human verification, whole-schedule
verification and calendar approval remain separate.

Calendar diagnostics resolve server-held signed references and check exact source,
evidence and current revision. Reassessment supersedes earlier proofs, including
when sibling changes invalidate the same revision. Restart, revocation or changed
configuration requires a fresh assessment/preview. Calendar approval and execution
recheck registered proofs; existing approval expiry and idempotency controls stay
in force. All production writes remain blocked.

`register_test_only` / `load_test_only` require explicit invented source IDs and
capture hashes. They are never installed by normal startup. Synthetic ACCEPT
cannot authorize a genuine upload or borrow a real-event calendar grant. Software
tests may exercise only the separately authorized local TEST_ONLY sandbox.
