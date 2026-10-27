"""PRE-01 evidence gate. Schema validity and spreadsheet flags cannot pass it."""

from collections import Counter
import json
from pathlib import Path

from backend.models.event import FieldState
from backend.research.agreement import agreement_manifest
from backend.research.audit import AuditError, audit_progress, prepare_audit
from backend.research.integrity import check_artifacts, check_sources, check_splits, file_sha256, record_digest, safe_path, source_for_page
from backend.research.inventory import compare_provisional, inventory_findings
from backend.research.models import Finding, ReadinessReport, ResearchBundle, WorkflowState
from backend.research.workflow import REGISTERED_CONDITIONAL_FIELDS, WorkflowError, export_gold_manifest, has_unresolved_placeholder, resolved_labels, validate_history

REQUIRED_FREEZE_KINDS = {"gold_manifest", "source_manifest", "source_inventory", "raw_labels", "reconciliation",
                         "audit_sample", "agreement", "adjudication", "exclusions", "split_manifest",
                         "field_registry", "annotation_guide", "role_access_manifest"}


def assess_pre01(bundle: ResearchBundle, evidence_root: Path) -> ReadinessReport:
    findings = list(check_sources(bundle.sources, bundle.pages))
    findings.extend(check_splits(bundle.pages))
    findings.extend(check_artifacts(evidence_root, bundle.artifacts))
    if bundle.origin != "AUTHENTIC":
        findings.append(Finding(code="SYNTHETIC_INPUT_NOT_RESEARCH_EVIDENCE"))
    artifacts = {a.artifact_id: a for a in bundle.artifacts}
    page_map = {p.page_id: p for p in bundle.pages}
    known_refs = set(artifacts) | {e.evidence_id for i in bundle.inventories for e in i.evidence}
    known_refs |= {e.evidence_id for w in bundle.workflows for s in w.submissions for e in s.inventory.evidence}
    known_refs |= {e.evidence_id for w in bundle.workflows if w.final_inventory for e in w.final_inventory.evidence}

    def visit(value):
        if isinstance(value, dict):
            if value.get("origin") == "SYNTHETIC":
                findings.append(Finding(code="SYNTHETIC_EVIDENCE"))
            for key, child in value.items():
                if key.endswith("evidence_ids") and isinstance(child, list) and set(child) - known_refs:
                    findings.append(Finding(code="UNKNOWN_EVIDENCE_REFERENCE"))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(bundle.model_dump(mode="json"))

    def require_attestation_proof(attestation, kind, reason):
        if attestation is None or not any(artifacts.get(i) and artifacts[i].kind == kind for i in attestation.evidence_ids):
            findings.append(Finding(code=reason))

    for source in bundle.sources:
        if source.source_path and source.original_sha256:
            try:
                path = safe_path(evidence_root, source.source_path)
                if not path.is_file():
                    findings.append(Finding(code="SOURCE_FILE_MISSING"))
                elif file_sha256(path) != source.original_sha256:
                    findings.append(Finding(code="SOURCE_HASH_MISMATCH"))
            except (OSError, ValueError):
                findings.append(Finding(code="SOURCE_FILE_UNSAFE_OR_UNREADABLE"))
    for inventory in bundle.inventories:
        findings.extend(inventory_findings(inventory))
        page = page_map.get(inventory.page_id)
        if page is None or inventory.source_sha256 != getattr(source_for_page(bundle.sources, page), "original_sha256", None):
            findings.append(Finding(code="INVENTORY_SOURCE_HASH_MISMATCH"))
        if page is not None and any(e.page != page.page_number for e in inventory.evidence):
            findings.append(Finding(code="INVENTORY_PAGE_EVIDENCE_MISMATCH"))
    inventory_ids = [i.page_id for i in bundle.inventories]
    if len(set(inventory_ids)) != len(inventory_ids):
        findings.append(Finding(code="DUPLICATE_PAGE_INVENTORY"))
    bearing_ids = {p.page_id for p in bundle.pages if p.timetable_bearing is True}
    if set(inventory_ids) != bearing_ids:
        findings.append(Finding(code="SOURCE_INVENTORY_PAGE_COVERAGE_INCOMPLETE"))
    findings.extend(compare_provisional(bundle.inventories, bundle.provisional_annotations, bundle.reconciliation))
    for annotation in bundle.provisional_annotations:
        if annotation.original_artifact_id not in artifacts:
            findings.append(Finding(code="ORIGINAL_ANNOTATION_ARTIFACT_MISSING"))

    workflow_ids = [w.page_id for w in bundle.workflows]
    if len(set(workflow_ids)) != len(workflow_ids):
        findings.append(Finding(code="DUPLICATE_PAGE_WORKFLOW"))
    if set(workflow_ids) != bearing_ids:
        findings.append(Finding(code="ANNOTATION_WORKFLOW_PAGE_COVERAGE_INCOMPLETE"))
    verified_count = 0
    for workflow in bundle.workflows:
        if not validate_history(workflow):
            findings.append(Finding(code="WORKFLOW_HISTORY_INVALID"))
            continue
        if not workflow.assignments:
            findings.append(Finding(code="REVIEWER_ASSIGNMENTS_UNAPPROVED"))
        for person in workflow.people:
            if not any(artifacts.get(i) and artifacts[i].kind == "identity_confirmation" for i in person.identity_evidence_ids):
                findings.append(Finding(code="REVIEWER_IDENTITY_EVIDENCE_MISSING"))
        for assignment in workflow.assignments:
            require_attestation_proof(assignment.approved_by, "role_approval", "ROLE_APPROVAL_EVIDENCE_MISSING")
        require_attestation_proof(workflow.verification, "verification_record", "VERIFICATION_ACTION_EVIDENCE_MISSING")
        page = page_map.get(workflow.page_id)
        if page is None or workflow.source_sha256 != getattr(source_for_page(bundle.sources, page), "original_sha256", None):
            findings.append(Finding(code="WORKFLOW_SOURCE_HASH_MISMATCH"))
        if page is not None and workflow.source_id != page.source_id:
            findings.append(Finding(code="WORKFLOW_SOURCE_ID_MISMATCH"))
        if workflow.state not in {WorkflowState.MANUALLY_VERIFIED_REFERENCE, WorkflowState.INDEPENDENTLY_VERIFIED} or not workflow.verification or not workflow.final_inventory:
            findings.append(Finding(code="GOLD_WORKFLOW_INCOMPLETE"))
        else:
            verified_count += len(resolved_labels(workflow))
            findings.extend(inventory_findings(workflow.final_inventory))
            if workflow.final_inventory.source_sha256 != workflow.source_sha256 or workflow.final_inventory.page_id != workflow.page_id:
                findings.append(Finding(code="FINAL_INVENTORY_SOURCE_MISMATCH"))
            labels = resolved_labels(workflow)
            left, right = [a for a, _ in workflow.label_session_bindings], [b for _, b in workflow.label_session_bindings]
            if (set(left) != {l.label_id for l in labels} or set(right) != {s.session_id for s in workflow.final_inventory.sessions if s.disposition == "IN_SCOPE"}
                    or len(set(left)) != len(left) or len(set(right)) != len(right)):
                findings.append(Finding(code="GOLD_SOURCE_SESSION_BIJECTION_MISSING"))
            for label in labels:
                states = {o.field_name: o.state for o in label.revision.observations}
                if not label.revision.core_fields_present or any(states[k] not in {FieldState.PRESENT, FieldState.SOURCE_ABSENT, FieldState.NOT_APPLICABLE} for k in REGISTERED_CONDITIONAL_FIELDS):
                    findings.append(Finding(code="GOLD_FIELD_INCOMPLETE"))
                if has_unresolved_placeholder(label.revision):
                    findings.append(Finding(code="GOLD_SOURCE_PLACEHOLDER_UNRESOLVED"))
        if len(workflow.disagreements) != len(workflow.adjudications):
            findings.append(Finding(code="DISAGREEMENTS_UNRESOLVED"))
        independent = next((a.actor_id for a in workflow.assignments if a.role == "INDEPENDENT"), None)
        primary = next((a.actor_id for a in workflow.assignments if a.role == "PRIMARY"), None)
        if independent and independent == primary:
            findings.append(Finding(code="REVIEWER_INDEPENDENCE_VIOLATION"))
        if workflow.state == WorkflowState.INDEPENDENTLY_VERIFIED:
            reviewer = next((p for p in workflow.people if p.actor_id == independent), None)
            adjudicator = next((a.actor_id for a in workflow.assignments if a.role == "ADJUDICATOR"), None)
            if (not reviewer or reviewer.contributed_to_implementation is not False or independent == primary
                    or workflow.comparison is None or not workflow.verification or workflow.verification.actor_id not in {independent, adjudicator}
                    or workflow.verification.actor_id == primary):
                findings.append(Finding(code="REVIEWER_INDEPENDENCE_VIOLATION"))
    if not bundle.workflows:
        findings.extend((Finding(code="GOLD_WORKFLOW_INCOMPLETE"), Finding(code="REVIEWER_ASSIGNMENTS_UNAPPROVED")))
    elif verified_count == 0:
        findings.append(Finding(code="GOLD_RETAINED_EVENT_SET_EMPTY"))

    progress = audit_progress(bundle.audit_sample, bundle.workflows, bundle.pages)
    if bundle.audit_policy is None or bundle.audit_policy.approval is None:
        findings.append(Finding(code="AUDIT_SPEC_UNAPPROVED"))
    else:
        require_attestation_proof(bundle.audit_policy.approval, "audit_policy_approval", "AUDIT_POLICY_APPROVAL_EVIDENCE_MISSING")
        if bundle.audit_policy.registered_spec_artifact_id not in artifacts:
            findings.append(Finding(code="AUDIT_REGISTERED_SPEC_EVIDENCE_MISSING"))
    if bundle.audit_sample is None:
        findings.append(Finding(code="AUDIT_SAMPLE_NOT_FINALIZED"))
    elif bundle.audit_policy is not None:
        try:
            recomputed = prepare_audit(bundle.pages, bundle.audit_policy, bundle.audit_sample.finalized_by,
                                      version=bundle.audit_sample.version)
            if (recomputed.page_ids != bundle.audit_sample.page_ids or recomputed.dependency_groups != bundle.audit_sample.dependency_groups
                    or recomputed.policy_digest != bundle.audit_sample.policy_digest or recomputed.frame_digest != bundle.audit_sample.frame_digest):
                findings.append(Finding(code="AUDIT_SAMPLE_REPRODUCIBILITY_MISMATCH"))
        except AuditError as error:
            findings.append(Finding(code=str(error)))
    if progress["status"] != "COMPLETE":
        findings.append(Finding(code="INDEPENDENT_AUDIT_INCOMPLETE"))
    if progress["by_partition"].get("test", 0) == 0:
        findings.append(Finding(code="HELDOUT_AUDIT_REPRESENTATION_MISSING"))

    if bundle.access_history_review is None:
        findings.append(Finding(code="ACCESS_HISTORY_REVIEW_MISSING"))
    else:
        require_attestation_proof(bundle.access_history_review, "access_history_review", "ACCESS_HISTORY_REVIEW_EVIDENCE_MISSING")
    for access in bundle.history_access:
        if access.artifact_id not in artifacts:
            findings.append(Finding(code="HISTORICAL_ACCESS_EVIDENCE_MISSING"))
        if access.partition == "test" and access.resolution is None:
            findings.append(Finding(code="HISTORICAL_HELDOUT_EXPOSURE_UNRESOLVED"))
        elif access.partition == "test" and not any(artifacts.get(i) and artifacts[i].kind == "protocol_amendment" for i in access.resolution.evidence_ids):
            findings.append(Finding(code="HISTORICAL_EXPOSURE_AMENDMENT_REQUIRED"))
    if bundle.ambiguities:
        findings.append(Finding(code="OUTSTANDING_AMBIGUITIES", count=len(bundle.ambiguities)))

    requirements = ((bundle.guide_artifact_id, "annotation_guide", "ANNOTATION_GUIDE_MISSING"),
                    (bundle.field_registry_artifact_id, "field_registry", "FIELD_REGISTRY_MISSING"),
                    (bundle.split_generator_artifact_id, "split_generator", "SPLIT_GENERATOR_EVIDENCE_MISSING"),
                    (bundle.split_manifest_artifact_id, "split_manifest", "SPLIT_MANIFEST_EVIDENCE_MISSING"),
                    (bundle.split_generation_record_artifact_id, "split_generation_record", "SPLIT_GENERATION_REPRODUCIBILITY_UNPROVEN"),
                    (bundle.simulation_artifact_id, "audit_simulation", "FINITE_FRAME_SIMULATION_MISSING"),
                    (bundle.agreement_artifact_id, "agreement", "PRE_ADJUDICATION_AGREEMENT_MISSING"))
    for artifact_id, kind, code in requirements:
        if artifact_id not in artifacts or artifacts[artifact_id].kind != kind:
            findings.append(Finding(code=code))
    if bundle.split_generation_record_artifact_id in artifacts:
        try:
            generation = json.loads(safe_path(evidence_root, artifacts[bundle.split_generation_record_artifact_id].relative_path).read_text(encoding="utf-8"))
            generator = artifacts[bundle.split_generator_artifact_id]
            manifest = artifacts[bundle.split_manifest_artifact_id]
            reproduction = artifacts[generation["reproduction_artifact_id"]]
            reproduced = json.loads(safe_path(evidence_root, reproduction.relative_path).read_text(encoding="utf-8"))
            expected_assignments = {p.page_id: p.partition for p in bundle.pages}
            if (generation.get("seed") != 20260831 or generation.get("generator_sha256") != generator.sha256
                    or generation.get("manifest_sha256") != manifest.sha256 or reproduction.kind != "split_reproduction"
                    or reproduced.get("assignments") != expected_assignments):
                findings.append(Finding(code="SPLIT_GENERATION_REPRODUCIBILITY_MISMATCH"))
        except (OSError, ValueError, KeyError, TypeError):
            findings.append(Finding(code="SPLIT_GENERATION_REPRODUCIBILITY_MISMATCH"))
    if bundle.simulation_artifact_id in artifacts:
        try:
            simulation = json.loads(safe_path(evidence_root, artifacts[bundle.simulation_artifact_id].relative_path).read_text(encoding="utf-8"))
            digest_body = {k: v for k, v in simulation.items() if k != "output_digest"}
            if (simulation.get("output_digest") != record_digest(digest_body) or simulation.get("seed") != 20260903
                    or simulation.get("frame_pages") != 168 or simulation.get("sample_digest") != record_digest(bundle.audit_sample)
                    or {s.get("affected_page_percent") for s in simulation.get("scenarios", [])} != {5, 10, 20}
                    or any(s.get("iterations") != 100000 for s in simulation.get("scenarios", []))):
                findings.append(Finding(code="FINITE_FRAME_SIMULATION_INVALID"))
        except (OSError, ValueError, TypeError, KeyError):
            findings.append(Finding(code="FINITE_FRAME_SIMULATION_INVALID"))
    if bundle.gold_freeze is None:
        findings.append(Finding(code="GOLD_FREEZE_MISSING"))
    else:
        findings.extend(check_artifacts(evidence_root, bundle.gold_freeze.artifacts))
        if not REQUIRED_FREEZE_KINDS.issubset(a.kind for a in bundle.gold_freeze.artifacts):
            findings.append(Finding(code="GOLD_FREEZE_ARTIFACT_SET_INCOMPLETE"))
        if len({a.kind for a in bundle.gold_freeze.artifacts}) != len(bundle.gold_freeze.artifacts):
            findings.append(Finding(code="DUPLICATE_FREEZE_ARTIFACT_KIND"))
        require_attestation_proof(bundle.gold_freeze.frozen_by, "gold_freeze_approval", "GOLD_FREEZE_APPROVAL_EVIDENCE_MISSING")
        snapshots = {
            "source_manifest": [s.model_dump(mode="json") for s in bundle.sources],
            "source_inventory": [i.model_dump(mode="json") for i in bundle.inventories],
            "raw_labels": [{"page_id": w.page_id, "submissions": [s.model_dump(mode="json") for s in w.submissions]} for w in bundle.workflows],
            "reconciliation": [r.model_dump(mode="json") for r in bundle.reconciliation],
            "audit_sample": bundle.audit_sample.model_dump(mode="json") if bundle.audit_sample else None,
            "agreement": agreement_manifest(bundle.workflows),
            "adjudication": [{"page_id": w.page_id, "adjudications": [a.model_dump(mode="json") for a in w.adjudications]} for w in bundle.workflows],
            "split_manifest": [p.model_dump(mode="json") for p in bundle.pages],
        }
        for frozen in bundle.gold_freeze.artifacts:
            if frozen.kind in snapshots:
                try:
                    actual = json.loads(safe_path(evidence_root, frozen.relative_path).read_text(encoding="utf-8"))
                    if record_digest(actual) != record_digest(snapshots[frozen.kind]):
                        findings.append(Finding(code="FROZEN_PREPARATION_CONTENT_MISMATCH", scope=frozen.kind))
                except (OSError, ValueError):
                    findings.append(Finding(code="FROZEN_PREPARATION_CONTENT_UNREADABLE", scope=frozen.kind))
        try:
            gold = export_gold_manifest(bundle.workflows)
            frozen_gold = next(a for a in bundle.gold_freeze.artifacts if a.kind == "gold_manifest")
            actual = json.loads(safe_path(evidence_root, frozen_gold.relative_path).read_text(encoding="utf-8"))
            if record_digest(actual) != record_digest(gold) or frozen_gold.sha256 != bundle.gold_freeze.dataset_digest:
                findings.append(Finding(code="GOLD_FREEZE_CONTENT_MISMATCH"))
        except (WorkflowError, StopIteration, OSError, ValueError):
            findings.append(Finding(code="GOLD_FREEZE_CONTENT_UNVERIFIABLE"))
    if bundle.closure_attestation is None:
        findings.append(Finding(code="PRE01_HUMAN_CLOSURE_APPROVAL_MISSING"))
    else:
        require_attestation_proof(bundle.closure_attestation, "pre01_closure_approval", "PRE01_CLOSURE_APPROVAL_EVIDENCE_MISSING")
    # Collapse repeated reason codes while retaining multiplicity. No private values.
    counts = Counter(f.code for f in findings for _ in range(f.count))
    return ReadinessReport(status="NOT READY" if counts else "READY", reason_codes=tuple(sorted(counts)),
        findings=tuple(Finding(code=code, count=count) for code, count in sorted(counts.items())),
        timetable_pages_by_partition=tuple(sorted(Counter(p.partition for p in bundle.pages).items())),
        source_sessions=sum(len(i.sessions) for i in bundle.inventories), verified_reference_sessions=verified_count,
        independently_audited_pages=progress["independently_audited_pages"], historical_access_records=len(bundle.history_access),
        independent_audit_basis="explicit workflow records; legacy flags ignored", input_digest=record_digest(bundle))
