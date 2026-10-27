"""Typed research preparation records; null metadata is unknown, never guessed.

Records are attestations, not proof of authenticated access. Services validate
transitions and evidence links; an authenticated trusted caller is still needed.
"""

from datetime import date
from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from backend.models.evidence import ContractModel, EvidenceSpan
from backend.models.event import EventRevision

Identifier = Annotated[str, Field(min_length=1, pattern=r"\S")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Origin = Literal["AUTHENTIC", "SYNTHETIC"]
Partition = Literal["development", "validation", "test", "excluded"]


class Artifact(ContractModel):
    """Explicit file evidence; no file is created by constructing this record."""
    artifact_id: Identifier
    relative_path: Identifier
    sha256: Digest
    kind: Identifier
    origin: Origin


class Attestation(ContractModel):
    actor_id: Identifier
    recorded_at: AwareDatetime
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    origin: Origin


class SourceTimetable(ContractModel):
    source_id: Identifier | None = None
    source_document_id: Identifier | None = None
    institution: str | None = None
    permitted_source_category: str | None = None
    programme: str | None = None
    semester: str | None = None
    academic_year: str | None = None
    original_filename: str | None = None
    source_path: str | None = None
    original_sha256: Digest | None = None
    source_format: str | None = None
    page_count: int | None = Field(default=None, ge=1)
    template_family: str | None = None
    captured_on: date | None = None
    acquisition_channel: str | None = None
    source_timezone: str | None = None
    timezone_evidence_ids: tuple[Identifier, ...] = ()
    timezone_conversion_scope: Literal["NOT_ASSESSED", "AUTHORISED", "DISABLED_UNRESOLVED"] = "NOT_ASSESSED"
    usage_permission: Literal["UNKNOWN", "RESTRICTED_ACADEMIC", "PERMITTED", "DENIED"] = "UNKNOWN"
    permission_evidence_ids: tuple[Identifier, ...] = ()
    partition: Partition | Literal["mixed"] | None = None
    dependency_group: str | None = None
    annotation_status: Literal["UNKNOWN", "PROVISIONAL", "IN_PROGRESS", "RECONCILED"] = "UNKNOWN"
    audit_status: Literal["NOT_AUDITED", "IN_PROGRESS", "INDEPENDENTLY_AUDITED"] = "NOT_AUDITED"
    exclusion_reason: str | None = None
    metadata_evidence_ids: tuple[Identifier, ...] = ()


class SourcePage(ContractModel):
    page_id: Identifier
    source_id: Identifier
    source_document_id: Identifier | None = None
    page_number: int | None = Field(default=None, ge=1)
    partition: Partition
    timetable_bearing: bool | None = None
    dependency_group: Identifier | None = None
    timetable_id: Identifier | None = None
    programme_family: str | None = None
    complexity: str | None = None
    high_risk_categories: tuple[Identifier, ...] | None = None
    related_page_ids: tuple[Identifier, ...] = ()
    relationship: Literal["CONTINUATION", "REVISION", "CROP", "DERIVATIVE", "REPEATED", "NONE"] = "NONE"
    exclusion_reason: str | None = None
    metadata_evidence_ids: tuple[Identifier, ...] = ()


class SourceSession(ContractModel):
    session_id: Identifier
    page_id: Identifier
    region_id: Identifier
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    ambiguity_note: str | None = None
    disposition: Literal["IN_SCOPE", "EXCLUDED_AMBIGUOUS"] = "IN_SCOPE"


class SourceRegion(ContractModel):
    region_id: Identifier
    kind: Literal["SESSION", "MULTI_SESSION", "MERGED_CELL", "CONTINUATION", "UNKNOWN", "UNREADABLE", "NON_EVENT", "EXCLUDED_AMBIGUOUS"]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    session_ids: tuple[Identifier, ...] = ()
    continuation_page_ids: tuple[Identifier, ...] = ()
    note: str | None = None

    @model_validator(mode="after")
    def region_shape(self):
        if self.kind == "MULTI_SESSION" and len(self.session_ids) < 2:
            raise ValueError("MULTI_SESSION requires distinct session inventory entries")
        if self.kind == "SESSION" and len(self.session_ids) != 1:
            raise ValueError("SESSION requires one session entry")
        if self.kind == "NON_EVENT" and self.session_ids:
            raise ValueError("NON_EVENT cannot silently contain sessions")
        if self.kind in {"UNKNOWN", "UNREADABLE", "MERGED_CELL", "CONTINUATION", "EXCLUDED_AMBIGUOUS"} and not self.note:
            raise ValueError("Complex/unknown regions require notes")
        if self.kind == "CONTINUATION" and not self.continuation_page_ids:
            raise ValueError("Continuation region requires page links")
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("Region session IDs must be unique")
        return self


class PageInventory(ContractModel):
    page_id: Identifier
    source_sha256: Digest
    basis: Literal["SOURCE_ONLY"] = "SOURCE_ONLY"
    regions: tuple[SourceRegion, ...]
    sessions: tuple[SourceSession, ...]
    evidence: tuple[EvidenceSpan, ...]
    recorded_by: Attestation
    coverage_attestation: Attestation | None = None

    @model_validator(mode="after")
    def inventory_links(self):
        region_ids = [r.region_id for r in self.regions]
        session_ids = [s.session_id for s in self.sessions]
        evidence_ids = [e.evidence_id for e in self.evidence]
        if any(len(ids) != len(set(ids)) for ids in (region_ids, session_ids, evidence_ids)):
            raise ValueError("Inventory IDs must be unique")
        refs = {s.session_id for r in self.regions for s in self.sessions if s.session_id in r.session_ids and s.region_id == r.region_id}
        if refs != set(session_ids) or set(s for r in self.regions for s in r.session_ids) != set(session_ids):
            raise ValueError("Every source session must belong to its declared region")
        session_map = {s.session_id: s for s in self.sessions}
        if any(session_map[s].region_id != r.region_id for r in self.regions for s in r.session_ids):
            raise ValueError("A session cannot be attributed to a different source region")
        if any(s.page_id != self.page_id for s in self.sessions):
            raise ValueError("Session page must match inventory page")
        for item in (*self.regions, *self.sessions):
            if set(item.evidence_ids) - set(evidence_ids):
                raise ValueError("Unknown inventory source evidence")
        if any(e.page is None or not e.locator and e.bbox is None for e in self.evidence):
            raise ValueError("Research inventory requires page and region/locator evidence")
        return self


class ProvisionalAnnotation(ContractModel):
    annotation_id: Identifier
    page_id: Identifier
    original_artifact_id: Identifier
    legacy_status: str | None = None
    revision: EventRevision | None = None


class ReconciliationLink(ContractModel):
    session_ids: tuple[Identifier, ...] = ()
    annotation_ids: tuple[Identifier, ...] = ()
    disposition: Literal["CORRESPONDENCE", "SPLIT_CELL", "MERGED_ROWS", "MISSING_ANNOTATION", "EXTRA_ANNOTATION", "EXCLUDED_AMBIGUOUS"]
    recorded_by: Attestation
    reason: Identifier
    exclusion_region_ids: tuple[Identifier, ...] = ()


class Person(ContractModel):
    actor_id: Identifier
    identity_evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    contributed_to_implementation: bool | None = None
    origin: Origin


class Assignment(ContractModel):
    role: Literal["PRIMARY", "INDEPENDENT", "ADJUDICATOR"]
    actor_id: Identifier
    approved_by: Attestation


class SessionLabel(ContractModel):
    label_id: Identifier
    session_id: Identifier
    revision: EventRevision


class Submission(ContractModel):
    submission_id: Identifier
    role: Literal["PRIMARY", "INDEPENDENT"]
    inventory: PageInventory
    labels: tuple[SessionLabel, ...]
    submitted_by: Attestation
    previous_submission_id: Identifier | None = None
    correction_reason: str | None = None

    @model_validator(mode="after")
    def label_links(self):
        ids = [label.label_id for label in self.labels]
        sessions = [label.session_id for label in self.labels]
        if len(set(ids)) != len(ids) or len(set(sessions)) != len(sessions):
            raise ValueError("Label/session IDs must be unique within a submission")
        if set(sessions) != {s.session_id for s in self.inventory.sessions}:
            raise ValueError("Every inventoried session needs exactly one label, including unresolved labels")
        if self.submitted_by.actor_id != self.inventory.recorded_by.actor_id:
            raise ValueError("Submission must retain its source inventory author's identity")
        if self.submitted_by.recorded_at < self.inventory.recorded_by.recorded_at or self.submitted_by.origin != self.inventory.recorded_by.origin:
            raise ValueError("Submission timestamp/origin must agree with its inventory")
        if self.previous_submission_id and not self.correction_reason:
            raise ValueError("Corrections need a reason and retained predecessor")
        known = {e.evidence_id for e in self.inventory.evidence}
        for label in self.labels:
            if label.revision.method_id not in {"source_first_human_annotation_m2", "source_backed_human_correction_m2"}:
                raise ValueError("Research submissions require human source-first labels, not parser/prediction output")
            if label.revision.source_document_id not in {e.source_document_id for e in self.inventory.evidence}:
                raise ValueError("Label must identify its source evidence document")
            refs = {i for o in label.revision.observations for i in o.evidence_ids}
            refs.update(i for o in label.revision.observations for a in o.alternatives for i in a.evidence_ids)
            if refs - known:
                raise ValueError("Label references unknown source evidence")
        return self


class Comparison(ContractModel):
    primary_submission_id: Identifier
    independent_submission_id: Identifier
    # Human correspondence, established only after both blind submissions.
    label_pairs: tuple[tuple[Identifier, Identifier], ...]
    compared_by: Attestation


class Disagreement(ContractModel):
    disagreement_id: Identifier
    primary_label_id: Identifier | None = None
    independent_label_id: Identifier | None = None
    fields: tuple[Identifier, ...]


class Adjudication(ContractModel):
    disagreement_id: Identifier
    decision: Literal["USE_PRIMARY", "USE_INDEPENDENT", "CORRECTED", "EXCLUDED_AMBIGUOUS"]
    recorded_by: Attestation
    reason: Identifier
    corrected_label: SessionLabel | None = None
    exclusion_region_ids: tuple[Identifier, ...] = ()


class WorkflowState(str, Enum):
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    PRIMARY_SUBMITTED = "PRIMARY_SUBMITTED"
    INDEPENDENT_SUBMITTED = "INDEPENDENT_SUBMITTED"
    LABELS_SUBMITTED = "LABELS_SUBMITTED"
    DISPUTED = "DISPUTED"
    RECONCILED = "RECONCILED"
    MANUALLY_VERIFIED_REFERENCE = "MANUALLY_VERIFIED_REFERENCE"
    INDEPENDENTLY_VERIFIED = "INDEPENDENTLY_VERIFIED"


class HistoryEntry(ContractModel):
    sequence: int = Field(ge=1)
    action: Identifier
    from_state: WorkflowState
    to_state: WorkflowState
    recorded_by: Attestation
    payload_digest: Digest
    payload_json: str
    predecessor_digest: Digest | None = None
    entry_digest: Digest


class PageWorkflow(ContractModel):
    page_id: Identifier
    source_id: Identifier | None = None
    source_sha256: Digest
    origin: Origin
    people: tuple[Person, ...] = ()
    assignments: tuple[Assignment, ...] = ()
    submissions: tuple[Submission, ...] = ()
    comparison: Comparison | None = None
    disagreements: tuple[Disagreement, ...] = ()
    adjudications: tuple[Adjudication, ...] = ()
    state: WorkflowState = WorkflowState.UNASSIGNED
    verification: Attestation | None = None
    final_inventory: PageInventory | None = None
    label_session_bindings: tuple[tuple[Identifier, Identifier], ...] = ()
    history: tuple[HistoryEntry, ...] = ()


class AuditPolicy(ContractModel):
    version: Identifier
    target_pages: int = Field(default=29, ge=1)
    seed: int = 20260902
    algorithm: Literal["dependency_stratified_backtracking_v1"] = "dependency_stratified_backtracking_v1"
    # An explicit approved quota is required; do not infer it from 45/30/93.
    strata_quotas: tuple[tuple[Identifier, Identifier, int], ...] = ()
    high_risk_categories: tuple[Identifier, ...] = ()
    registered_spec_artifact_id: Identifier | None = None
    approval: Attestation | None = None


class AuditSample(ContractModel):
    version: Identifier
    policy_digest: Digest
    frame_digest: Digest
    seed: int
    page_ids: tuple[Identifier, ...]
    dependency_groups: tuple[Identifier, ...]
    finalized_by: Attestation
    predecessor_sample_digest: Digest | None = None


class FreezeRecord(ContractModel):
    version: Identifier
    artifacts: tuple[Artifact, ...] = Field(min_length=1)
    dataset_digest: Digest
    frozen_by: Attestation


class HistoricalAccess(ContractModel):
    record_id: Identifier
    partition: Partition
    purpose: Identifier
    observed_at: AwareDatetime | None = None
    artifact_id: Identifier
    label_access: Literal["YES", "NO", "UNKNOWN"] = "UNKNOWN"
    predictions_opened: Literal["YES", "NO", "UNKNOWN"] = "UNKNOWN"
    resolution: Attestation | None = None


class ResearchBundle(ContractModel):
    schema_version: Literal["m2.0"] = "m2.0"
    origin: Origin
    sources: tuple[SourceTimetable, ...] = ()
    pages: tuple[SourcePage, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    inventories: tuple[PageInventory, ...] = ()
    provisional_annotations: tuple[ProvisionalAnnotation, ...] = ()
    reconciliation: tuple[ReconciliationLink, ...] = ()
    workflows: tuple[PageWorkflow, ...] = ()
    audit_policy: AuditPolicy | None = None
    audit_sample: AuditSample | None = None
    history_access: tuple[HistoricalAccess, ...] = ()
    access_history_review: Attestation | None = None
    gold_freeze: FreezeRecord | None = None
    # External records, required and hash-checked, never generated here.
    guide_artifact_id: Identifier | None = None
    field_registry_artifact_id: Identifier | None = None
    split_generator_artifact_id: Identifier | None = None
    split_manifest_artifact_id: Identifier | None = None
    split_generation_record_artifact_id: Identifier | None = None
    simulation_artifact_id: Identifier | None = None
    agreement_artifact_id: Identifier | None = None
    closure_attestation: Attestation | None = None
    ambiguities: tuple[Identifier, ...] = ()


class Finding(ContractModel):
    code: Identifier
    count: int = Field(default=1, ge=0)
    scope: str | None = None


class ReadinessReport(ContractModel):
    status: Literal["READY", "NOT READY"]
    reason_codes: tuple[Identifier, ...]
    findings: tuple[Finding, ...]
    timetable_pages_by_partition: tuple[tuple[str, int], ...]
    source_sessions: int
    verified_reference_sessions: int
    independently_audited_pages: int
    audit_target: int = 29
    independent_audit_basis: str
    historical_access_records: int
    input_digest: Digest
