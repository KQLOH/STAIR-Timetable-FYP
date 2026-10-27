"""Canonical M1 records. Model validity does not establish source correctness.

The adapter emits candidates, not a research decision ledger. A null policy
decision means the selective controller has not run. Reuse details are recorded
in docs/m1_provenance.json; no baseline runtime modules are imported.
"""

from datetime import date as Date
from enum import Enum
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from backend.models.evidence import ContractModel, EvidenceSpan, ParseIssue, SourceDocument


class FieldState(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    SOURCE_ABSENT = "SOURCE_ABSENT"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DecisionStatus(str, Enum):
    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    ABSTAIN = "ABSTAIN"


class ExtractionStatus(str, Enum):
    EXTRACTED = "EXTRACTED"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICTING = "CONFLICTING"


class ConstraintStatus(str, Enum):
    NOT_ASSESSED = "NOT_ASSESSED"
    VALID = "VALID"
    WARNING = "WARNING"
    FAILED = "FAILED"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class CandidateAlternative(ContractModel):
    raw_value: str
    normalized_value: str | None = None
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)


class FieldObservation(ContractModel):
    field_name: str
    state: FieldState = FieldState.MISSING
    raw_value: str | None = None
    origin: Literal["source", "derived", "human", "authority"] = "source"
    evidence_ids: tuple[str, ...] = ()
    alternatives: tuple[CandidateAlternative, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    justification: str | None = None
    transformations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def supported_state(self):
        if self.state == FieldState.PRESENT and not self.evidence_ids:
            raise ValueError("PRESENT values require evidence references")
        if self.state in {FieldState.SOURCE_ABSENT, FieldState.NOT_APPLICABLE}:
            if not self.justification or not self.evidence_ids:
                raise ValueError("Absence/applicability needs source evidence and justification")
        if self.state == FieldState.MISSING and (self.raw_value is not None or self.alternatives):
            raise ValueError("Observed unresolved content must not be labelled MISSING")
        if self.state == FieldState.UNRESOLVED and self.raw_value is None and not self.alternatives:
            raise ValueError("UNRESOLVED needs retained raw content or alternatives")
        if self.state == FieldState.CONFLICTING and len(self.alternatives) < 2:
            raise ValueError("CONFLICTING needs at least two retained alternatives")
        if self.state == FieldState.CONFLICTING and len({a.raw_value for a in self.alternatives}) < 2:
            raise ValueError("Conflicting alternatives must differ")
        if any(not alternative.evidence_ids for alternative in self.alternatives):
            raise ValueError("Candidate alternatives require evidence references")
        return self


class RecurrenceRule(ContractModel):
    """Represent supplied recurrence only; unknown boundaries remain null."""

    frequency: Literal["ONCE", "WEEKLY"]
    interval: int | None = Field(default=None, ge=1)
    by_day: tuple[int, ...] | None = None
    term_start: Date | None = None
    term_end: Date | None = None
    until: Date | None = None
    count: int | None = Field(default=None, ge=1)
    included_dates: tuple[Date, ...] | None = None
    excluded_dates: tuple[Date, ...] | None = None
    replacement_dates: tuple[Date, ...] | None = None
    academic_calendar_id: str | None = None
    source_rule: str | None = None

    @model_validator(mode="after")
    def represent_weekdays(self):
        if self.by_day is not None and any(day not in range(1, 8) for day in self.by_day):
            raise ValueError("Recurrence weekdays use ISO 1..7")
        return self


class EventContent(ContractModel):
    course_code: str | None = Field(default=None, min_length=1)
    course_name: str | None = Field(default=None, min_length=1)
    session_type: Literal["Lecture", "Tutorial", "Practical"] | None = None
    section: str | None = None
    cohort_id: str | None = None
    lecturer: str | None = None
    location: str | None = None
    location_detail: str | None = None
    day_of_week: int | None = Field(default=None, ge=1, le=7)
    date: Date | None = None
    start_time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end_time: str | None = Field(default=None, pattern=r"^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)$")
    end_day_offset: Literal[0, 1] | None = None
    source_timezone: str | None = None
    teaching_weeks: tuple[int, ...] | None = None
    excluded_weeks: tuple[int, ...] | None = None
    recurrence: RecurrenceRule | None = None
    source_class_status: str | None = None

    @model_validator(mode="after")
    def represent_weeks(self):
        for weeks in (self.teaching_weeks, self.excluded_weeks):
            if weeks is not None and any(week < 1 for week in weeks):
                raise ValueError("Teaching week numbers must be positive")
        return self


def stable_session_key(source_family_id: str, timetable_id: str, session_id: str) -> str:
    """Unambiguous serialization of the report's three immutable identity parts."""
    parts = (source_family_id, timetable_id, session_id)
    if any(not part or not part.strip() for part in parts):
        raise ValueError("A stable session key requires all three confirmed identifiers")
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class EventIdentity(ContractModel):
    event_id: UUID = Field(default_factory=uuid4)
    status: Literal["UNRESOLVED", "CONFIRMED", "AMBIGUOUS"] = "UNRESOLVED"
    source_family_id: str | None = Field(default=None, min_length=1)
    timetable_id: str | None = Field(default=None, min_length=1)
    stable_source_session_id: str | None = Field(default=None, min_length=1)
    stable_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def confirmed_identity_only(self):
        if self.status == "CONFIRMED":
            if not all((self.source_family_id, self.timetable_id, self.stable_source_session_id, self.evidence_ids)):
                raise ValueError("Confirmed identity requires immutable IDs and source evidence")
            expected = stable_session_key(self.source_family_id, self.timetable_id, self.stable_source_session_id)
            if self.stable_key != expected:
                raise ValueError("Stable key must match immutable source/session identity")
        elif self.stable_key is not None:
            raise ValueError("Unresolved/ambiguous identity cannot carry a stable calendar key")
        return self


class EventRevision(ContractModel):
    revision_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    previous_revision_id: UUID | None = None
    source_document_id: str
    method_id: str = Field(default="tarumt_parser_adapter_m1", min_length=1)
    contract_version: Literal["m1.0"] = "m1.0"
    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: EventContent
    observations: tuple[FieldObservation, ...]

    @model_validator(mode="after")
    def content_states(self):
        if (self.version == 1) != (self.previous_revision_id is None):
            raise ValueError("Only the first revision has no predecessor")
        if self.previous_revision_id == self.revision_id:
            raise ValueError("A revision cannot be its own predecessor")
        names = [item.field_name for item in self.observations]
        if len(names) != len(set(names)) or set(names) != set(EventContent.model_fields):
            raise ValueError("Every registered content field needs exactly one observation")
        for item in self.observations:
            value = getattr(self.content, item.field_name)
            if (item.state == FieldState.PRESENT) != (value is not None):
                raise ValueError("Only PRESENT fields carry a normalized value")
            if item.field_name in {"start_time", "end_time"} and item.state in {FieldState.SOURCE_ABSENT, FieldState.NOT_APPLICABLE}:
                raise ValueError("Mandatory time fields cannot be absent/not applicable")
        for pair in (("course_code", "course_name"), ("day_of_week", "date")):
            states = {item.field_name: item.state for item in self.observations}
            if all(states[name] in {FieldState.SOURCE_ABSENT, FieldState.NOT_APPLICABLE} for name in pair):
                raise ValueError("Mandatory identity/day groups cannot be declared inapplicable")
        return self

    @property
    def core_fields_present(self) -> bool:
        """Presence only; does not establish correctness, validity or completeness."""
        c = self.content
        return bool((c.course_code or c.course_name) and (c.day_of_week or c.date) and c.start_time and c.end_time)


class RuleTrace(ContractModel):
    code: str
    passed: bool | None
    severity: Literal["INFO", "SOFT", "HARD"]
    field: str | None = None
    evidence_ids: tuple[str, ...] = ()


class ConstraintValidation(ContractModel):
    status: ConstraintStatus = ConstraintStatus.NOT_ASSESSED
    revision_id: UUID | None = None
    validator_version: str | None = None
    traces: tuple[RuleTrace, ...] = ()

    @model_validator(mode="after")
    def recorded_assessment(self):
        if self.status != ConstraintStatus.NOT_ASSESSED and not all((self.revision_id, self.validator_version)):
            raise ValueError("An assessment requires its revision and validator version")
        return self


class PolicyDecision(ContractModel):
    decision: DecisionStatus | None = None
    revision_id: UUID | None = None
    policy_version: str | None = None
    reliability_rank: float | None = Field(default=None, ge=0, le=1)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def recorded_decision(self):
        if self.decision is not None and not all((self.revision_id, self.policy_version)):
            raise ValueError("A policy decision requires its revision and policy version")
        return self


class HumanReview(ContractModel):
    status: Literal["NOT_REVIEWED", "NEEDS_REVIEW", "CORRECTED", "APPROVED", "REJECTED"] = "NOT_REVIEWED"
    revision_id: UUID | None = None
    actor_id: str | None = None
    recorded_at: AwareDatetime | None = None
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def recorded_review(self):
        if self.status != "NOT_REVIEWED" and not all((self.revision_id, self.actor_id, self.recorded_at)):
            raise ValueError("A review requires a dated actor and revision")
        return self


class HumanVerification(ContractModel):
    status: Literal["NOT_VERIFIED", "MANUALLY_VERIFIED_REFERENCE", "INDEPENDENTLY_VERIFIED", "DISPUTED"] = "NOT_VERIFIED"
    revision_id: UUID | None = None
    actor_id: str | None = None
    recorded_at: AwareDatetime | None = None
    evidence_record_id: str | None = None

    @model_validator(mode="after")
    def recorded_verification(self):
        if self.status in {"MANUALLY_VERIFIED_REFERENCE", "INDEPENDENTLY_VERIFIED"}:
            if not all((self.revision_id, self.actor_id, self.recorded_at, self.evidence_record_id)):
                raise ValueError("Verification status requires a dated actor/evidence record")
        return self


class CalendarApproval(ContractModel):
    revision_id: UUID
    actor_id: str = Field(min_length=1, pattern=r"\S")
    recorded_at: AwareDatetime
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_calendar_id: str = Field(min_length=1, pattern=r"\S")
    remote_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteConfirmation(ContractModel):
    revision_id: UUID
    external_event_id: str = Field(min_length=1, pattern=r"\S")
    observed_at: AwareDatetime
    remote_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_record_id: str = Field(min_length=1, pattern=r"\S")


class CalendarState(ContractModel):
    eligibility: Literal["NOT_ASSESSED", "ELIGIBLE", "BLOCKED"] = "NOT_ASSESSED"
    eligibility_revision_id: UUID | None = None
    eligibility_policy_version: str | None = None
    eligibility_reason_codes: tuple[str, ...] = ()
    approval_status: Literal["NOT_APPROVED", "CALENDAR_APPROVED", "REVOKED", "STALE"] = "NOT_APPROVED"
    approval: CalendarApproval | None = None
    sync_status: Literal[
        "NOT_SUBMITTED", "DISCOVERED", "PREVIEW_CREATE", "PREVIEW_UPDATE", "PREVIEW_REQUIRED",
        "APPROVED", "REJECTED", "PENDING", "CONFIRMED", "FAILED_CONFIRMED", "OUTCOME_UNKNOWN",
        "RECONCILIATION_REQUIRED", "REFUSED", "NO_ACTION", "MISSING_OBSERVATION",
    ] = "NOT_SUBMITTED"
    confirmation: RemoteConfirmation | None = None

    @model_validator(mode="after")
    def separate_calendar_evidence(self):
        if self.eligibility != "NOT_ASSESSED" and not all((self.eligibility_revision_id, self.eligibility_policy_version)):
            raise ValueError("Eligibility assessment requires its revision and policy version")
        if self.approval_status == "CALENDAR_APPROVED" and self.approval is None:
            raise ValueError("Calendar approval requires its bound approval record")
        if self.sync_status == "APPROVED" and self.approval_status != "CALENDAR_APPROVED":
            raise ValueError("An approved calendar operation requires explicit calendar approval")
        if self.sync_status == "CONFIRMED" and self.confirmation is None:
            raise ValueError("Remote confirmation cannot be inferred from HTTP success")
        return self


class CanonicalEvent(ContractModel):
    schema_version: Literal["m1.0"] = "m1.0"
    candidate_id: str
    identity: EventIdentity
    revision: EventRevision
    extraction_status: ExtractionStatus
    validation: ConstraintValidation = Field(default_factory=ConstraintValidation)
    policy: PolicyDecision = Field(default_factory=PolicyDecision)
    human_review: HumanReview = Field(default_factory=HumanReview)
    human_verification: HumanVerification = Field(default_factory=HumanVerification)
    calendar: CalendarState = Field(default_factory=CalendarState)

    @model_validator(mode="after")
    def bind_status_records(self):
        if self.extraction_status == ExtractionStatus.EXTRACTED and not self.revision.core_fields_present:
            raise ValueError("EXTRACTED requires the core source fields to be present")
        if self.calendar.eligibility_revision_id is not None and self.calendar.eligibility_revision_id != self.revision.revision_id:
            raise ValueError("Calendar eligibility must refer to this content revision")
        records = (self.validation, self.policy, self.human_review, self.human_verification,
                   self.calendar.approval, self.calendar.confirmation)
        if any(record is not None and record.revision_id is not None and record.revision_id != self.revision.revision_id for record in records):
            raise ValueError("Status records must refer to this content revision")
        return self


class CompleteScheduleVerification(ContractModel):
    """Separate schedule-level attestation; M1 never computes or grants it."""

    status: Literal["NOT_VERIFIED", "COMPLETE_SCHEDULE_VERIFIED", "DISPUTED", "REVOKED"] = "NOT_VERIFIED"
    timetable_id: str | None = None
    actor_id: str | None = None
    recorded_at: AwareDatetime | None = None
    source_inventory_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_set_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_record_id: str | None = None

    @model_validator(mode="after")
    def require_completeness_record(self):
        if self.status == "COMPLETE_SCHEDULE_VERIFIED" and not all((
            self.timetable_id, self.actor_id, self.recorded_at,
            self.source_inventory_digest, self.event_set_digest, self.evidence_record_id,
        )):
            raise ValueError("Complete schedule status requires its own inventory/evidence record")
        return self


class CanonicalTimetable(ContractModel):
    schema_version: Literal["m1.0"] = "m1.0"
    source: SourceDocument
    evidence: tuple[EvidenceSpan, ...] = ()
    events: tuple[CanonicalEvent, ...] = ()
    issues: tuple[ParseIssue, ...] = ()
    complete_schedule_verification: CompleteScheduleVerification = Field(default_factory=CompleteScheduleVerification)

    @model_validator(mode="after")
    def evidence_referential_integrity(self):
        ids = [span.evidence_id for span in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence IDs must be unique")
        known = set(ids)
        if any(span.source_document_id != self.source.source_document_id for span in self.evidence):
            raise ValueError("Evidence must identify this source capture")
        if len({event.candidate_id for event in self.events}) != len(self.events):
            raise ValueError("Candidate IDs must be unique")
        if len({event.identity.event_id for event in self.events}) != len(self.events):
            raise ValueError("Event record IDs must be unique")
        if len({event.revision.revision_id for event in self.events}) != len(self.events):
            raise ValueError("Revision IDs must be unique")
        for event in self.events:
            if event.revision.source_document_id != self.source.source_document_id:
                raise ValueError("Event revision must identify this source capture")
            for name in ("source_family_id", "timetable_id"):
                value = getattr(event.identity, name)
                if value is not None and value != getattr(self.source, name):
                    raise ValueError("Identity must belong to this source family/timetable")
            references = list(event.identity.evidence_ids) + list(event.human_review.evidence_ids)
            for observation in event.revision.observations:
                references.extend(observation.evidence_ids)
                for alternative in observation.alternatives:
                    references.extend(alternative.evidence_ids)
            for trace in event.validation.traces:
                references.extend(trace.evidence_ids)
            if set(references) - known:
                raise ValueError("Event contains an unknown evidence reference")
        for issue in self.issues:
            if set(issue.evidence_ids) - known:
                raise ValueError("Issue contains an unknown evidence reference")
            if issue.candidate_id is not None and issue.candidate_id not in {e.candidate_id for e in self.events}:
                raise ValueError("Issue contains an unknown candidate reference")
        return self
