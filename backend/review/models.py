"""Additive M5 records; M1 and M4 contracts remain intact."""
from enum import Enum
from pathlib import Path
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator, model_serializer
from backend.models.evidence import ContractModel, BoundingBox
from backend.models.event import (CanonicalEvent, DecisionStatus, FieldState, CandidateAlternative,
                                  EventContent)
from backend.reconstruction.models import Context, Finding


class PolicyConfig(ContractModel):
    profile_id: Literal['m5-development-v1']
    version: Literal['1.0']
    status: Literal['PROVISIONAL_APPLICATION_ONLY']
    accept_enabled: Literal[False]
    registered_routing_enabled: Literal[False]
    allow_bounded_application_review: bool
    review_max_fields: Literal[2]
    authority: str
    note: str


def development_policy():
    return PolicyConfig.model_validate_json((Path(__file__).resolve().parents[2]/'config/m5-development.json').read_text())


class FieldAssessment(ContractModel):
    field: str
    state: FieldState
    evidence_count: int
    alternative_count: int
    needs_review: bool
    parser_confidence: float | None = Field(default=None,ge=0,le=1)


class SourceRouting(ContractModel):
    profile: Literal['m5-source-review-v1'] = 'm5-source-review-v1'
    configuration_digest: str
    route: Literal['REVIEW','ABSTAIN']
    support: Literal['SOURCE_SUPPORTED','NEEDS_CHECK','INSUFFICIENT']
    revision_id: UUID | None
    revision_digest: str | None
    reason_codes: tuple[str, ...]
    fields_to_check: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    explanations: tuple[dict[str,str], ...]
    registered_accept_eligible: Literal[False] = False
    calibrated_probability: None = None


class PolicyAssessment(ContractModel):
    decision_id: UUID = Field(default_factory=uuid4)
    candidate_id: str | None
    event_id: UUID | None
    revision_id: UUID | None
    revision_digest: str | None
    reconstruction_profile: str
    decoder_mode: Literal['constrained','unconstrained']
    policy_profile: str
    policy_version: str
    policy_digest: str
    input_digest: str
    assessment_input_digest: str
    reconstruction_digest: str
    decision: DecisionStatus
    reason_codes: tuple[str, ...]
    constraint_codes: tuple[str, ...]
    findings: tuple[Finding, ...]
    fields: tuple[FieldAssessment, ...]
    search_status: str
    score_provenance: Literal['M4_PROVISIONAL_SUPPORT_ONLY','NO_SCORABLE_OUTPUT','M4_SCORE_NOT_REUSED_AFTER_CORRECTION','STRUCTURED_SOURCE_NO_PROBABILITY','REGISTERED_FROZEN_RANKING']
    base_score_digest: str | None
    calibrated_probability: None = None
    registered_routing_eligible: bool = False
    registration_mode: Literal['TEST_ONLY','AUTHENTIC'] | None = None
    registration_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    registration_source_digest: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    registration_evidence_digest: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    registration_signature: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    ranking_score: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    review_required: bool
    recorded_at: AwareDatetime
    schedule_verified: Literal[False] = False
    source_routing: SourceRouting | None = None


    @model_validator(mode='after')
    def registration_claim(self):
        if self.registered_routing_eligible or self.registration_mode is not None:
            if not (self.policy_profile=='m5-registered-v1' and self.registration_mode and
                    self.registration_id and self.registration_source_digest and
                    self.registration_evidence_digest and self.registration_signature):
                raise ValueError('Registered claims require a host-issued receipt')
        if self.registered_routing_eligible and self.decision!=DecisionStatus.ACCEPT:
            raise ValueError('Only ACCEPT can claim registered eligibility')
        return self

    @model_serializer(mode='wrap')
    def preserve_development_records(self,handler):
        value=handler(self)
        # Existing encrypted journal digests must retain their exact contract.
        if self.registration_mode is None:
            for key in ('registration_mode','registration_id','registration_source_digest',
                        'registration_evidence_digest','registration_signature','ranking_score'):
                value.pop(key,None)
        return value


class RegisteredAssessmentRef(ContractModel):
    decision_id: UUID
    candidate_id: str
    event_id: UUID
    revision_id: UUID
    revision_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    registration_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    registration_signature: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReviewState(str, Enum):
    PENDING = 'PENDING_REVIEW'
    ASSIGNED = 'ASSIGNED_REVIEW'
    IN_PROGRESS = 'IN_PROGRESS'
    SUBMITTED = 'SUBMITTED_CORRECTION'
    REVALIDATION_REQUIRED = 'REVALIDATION_REQUIRED'
    REVALIDATED = 'REVALIDATED'
    RESOLVED = 'RESOLVED'
    ESCALATED = 'ESCALATED'
    CLOSED = 'ABSTAINED_CLOSED'


class FieldCorrection(ContractModel):
    field_name: str
    state: FieldState
    value: Any = None
    raw_value: str | None = Field(default=None, max_length=10000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    alternatives: tuple[CandidateAlternative, ...] = ()
    justification: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def known_field(self):
        if self.field_name not in EventContent.model_fields:
            raise ValueError('Only registered event content fields can be corrected')
        return self


class HumanEvidenceInput(ContractModel):
    evidence_id: str = Field(pattern=r'^human:[A-Za-z0-9_-]{1,80}$')
    raw_text: str = Field(min_length=1, max_length=10000)
    source_evidence_ids: tuple[str, ...] = Field(min_length=1,max_length=20)
    page: int | None = Field(default=None,ge=1)
    bbox: BoundingBox | None = None
    note: str = Field(min_length=1,max_length=2000)


class CorrectionSubmission(ContractModel):
    request_id: UUID
    expected_revision_id: UUID
    expected_etag: int = Field(ge=1)
    reason: str = Field(min_length=1,max_length=2000,pattern=r'\S')
    fields: tuple[FieldCorrection, ...] = Field(max_length=19)
    human_evidence: tuple[HumanEvidenceInput, ...] = Field(default=(),max_length=20)
    relationships: Context | None = None

    @model_validator(mode='after')
    def nonempty_unique(self):
        if not self.fields and self.relationships is None:
            raise ValueError('A correction must change fields or source relationships')
        if len({f.field_name for f in self.fields}) != len(self.fields):
            raise ValueError('Duplicate field corrections')
        if len({e.evidence_id for e in self.human_evidence}) != len(self.human_evidence):
            raise ValueError('Duplicate human evidence IDs')
        return self


class AuditEntry(ContractModel):
    sequence: int
    operation: str
    actor_id: str
    recorded_at: AwareDatetime
    from_state: ReviewState
    to_state: ReviewState
    reason: str
    previous_hash: str | None
    entry_hash: str
    payload_json: str


class CorrectionRecord(ContractModel):
    request_id: UUID
    actor_id: str
    recorded_at: AwareDatetime
    reason: str
    status: Literal['SUBMITTED','INVALID']
    previous_revision_id: UUID
    new_revision_id: UUID | None
    before_json: str
    proposal_json: str
    after_json: str | None
    error_codes: tuple[str,...] = ()


class ReviewTask(ContractModel):
    task_id: UUID
    run_id: str
    state: ReviewState
    purpose: Literal['APPLICATION_REPAIR_NOT_GOLD'] = 'APPLICATION_REPAIR_NOT_GOLD'
    etag: int
    assignee: str | None
    original_prediction_digest: str | None
    current_event: CanonicalEvent | None
    decision: PolicyAssessment
    created_by: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    validation_dependency_digest: str | None


class ReviewError(ValueError):
    def __init__(self, code, status_code=409):
        super().__init__(code)
        self.code, self.status_code = code, status_code
