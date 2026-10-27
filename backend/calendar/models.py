"""Versioned local sandbox records, separate from M1 remote confirmation."""
from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import Field, AwareDatetime, model_validator

from backend.models.evidence import ContractModel
from backend.models.event import CanonicalTimetable, RecurrenceRule
from backend.reconstruction.models import TermAuthority, digest
from backend.review.models import PolicyAssessment


class CalendarError(ValueError):
    def __init__(self,code,status_code=409):
        super().__init__(code)
        self.code,self.status_code=code,status_code


class WeekAuthority(ContractModel):
    week:int=Field(ge=1)
    start:date
    end:date
    evidence_ids:tuple[str,...]=Field(min_length=1)

    @model_validator(mode='after')
    def range_valid(self):
        if not 0<=(self.end-self.start).days<=6: raise ValueError('A mapped teaching week spans at most seven dates')
        return self


class Lineage(ContractModel):
    predecessor_document_id:str
    successor_document_id:str
    evidence_ids:tuple[str,...]=Field(min_length=1)


class CalendarContext(ContractModel):
    timezone_evidence_ids:tuple[str,...]=()
    term:TermAuthority|None=None
    weeks:tuple[WeekAuthority,...]=Field(default=(),max_length=512)
    exception_evidence_ids:tuple[str,...]=()
    lineage:tuple[Lineage,...]=()


class CalendarInput(ContractModel):
    packet:CanonicalTimetable
    reconstruction_status:Literal['COMPLETE_FINITE_SEARCH','SEARCH_INCOMPLETE','EXECUTION_ABORTED','NOT_PROVIDED']='NOT_PROVIDED'
    assessments:tuple[PolicyAssessment,...]=()
    context:CalendarContext=Field(default_factory=CalendarContext)

    @model_validator(mode='after')
    def bounded(self):
        if len(self.packet.events)>100 or len(self.packet.evidence)>512 or sum(len(s.raw_text) for s in self.packet.evidence)>500000:
            raise ValueError('Calendar development input limits exceeded')
        return self


class MappingEvidence(ContractModel):
    calendar_field:str
    source_fields:tuple[str,...]
    evidence_ids:tuple[str,...]
    transformation:str


class Occurrence(ContractModel):
    source_local_date:date
    start:AwareDatetime
    end:AwareDatetime


class CalendarPayload(ContractModel):
    schema_version:Literal['m6-local-v1']='m6-local-v1'
    stable_key:str=Field(pattern=r'^[0-9a-f]{64}$')
    source_family_id:str
    timetable_id:str
    source_session_id:str
    candidate_id:str
    revision_id:UUID
    revision_digest:str
    source_document_id:str
    source_content_sha256:str
    title:str
    source_timezone:str
    location:str|None
    course_code:str|None
    course_name:str|None
    session_type:str|None
    section:str|None
    cohort_id:str|None
    lecturer:str|None=None
    location_detail:str|None=None
    source_class_status:str|None=None
    recurrence:RecurrenceRule|None
    teaching_weeks:tuple[int,...]|None
    excluded_weeks:tuple[int,...]|None
    occurrences:tuple[Occurrence,...]=Field(min_length=1,max_length=512)
    excluded_dates:tuple[date,...]=()
    mapping_evidence:tuple[MappingEvidence,...]


class Eligibility(ContractModel):
    candidate_id:str
    revision_id:UUID
    revision_digest:str
    status:Literal['BLOCKED','TEST_ELIGIBLE']
    actual_write_eligibility:Literal['BLOCKED']='BLOCKED'
    sandbox_write_permitted:bool=False
    authority_kind:Literal['APPLICATION_BLOCKED','ISOLATED_SYNTHETIC_TEST']
    reason_codes:tuple[str,...]
    payload:CalendarPayload|None=None


class Entry(ContractModel):
    entry_id:str
    managed:bool=True
    payload:CalendarPayload


class Snapshot(ContractModel):
    sandbox_id:str
    scope:Literal['DEVELOPMENT_BLOCKED','TEST_ONLY']
    calendar_version:int=Field(ge=0)
    entries:tuple[Entry,...]=()

    def checksum(self):
        return digest(self.model_dump(mode='json'))


class SourceBinding(ContractModel):
    stable_key:str
    candidate_id:str
    revision_id:UUID
    revision_digest:str
    source_document_id:str
    bundle_digest:str
    observation_version:int=Field(default=0,ge=0)


class Operation(ContractModel):
    operation_key:str
    action:Literal['CREATE','UPDATE','NO_OP','BLOCKED','CONFLICT','MISSING_OBSERVATION']
    candidate_id:str|None
    stable_key:str|None
    entry_id:str|None
    before:CalendarPayload|None
    after:CalendarPayload|None
    changed_fields:tuple[str,...]
    reason_codes:tuple[str,...]


class Preview(ContractModel):
    target_sandbox_id:str
    scope:Literal['DEVELOPMENT_BLOCKED','TEST_ONLY']
    snapshot_version:int
    snapshot_digest:str
    input_digest:str
    source_bindings:tuple[SourceBinding,...]
    operations:tuple[Operation,...]
    eligibility:tuple[Eligibility,...]
    generated_at:AwareDatetime
    preview_digest:str
    external_calendar:Literal[False]=False

    def checksum(self):
        return digest(self.model_dump(mode='json',exclude={'generated_at','preview_digest'}))


class Approval(ContractModel):
    approval_id:UUID
    actor_id:str
    target_sandbox_id:str
    preview_digest:str
    snapshot_version:int
    snapshot_digest:str
    source_bindings:tuple[SourceBinding,...]
    operation_keys:tuple[str,...]
    decision:Literal['APPROVE','REJECT']
    recorded_at:AwareDatetime
    expires_at:AwareDatetime
    authority_kind:Literal['ISOLATED_SYNTHETIC_TEST']


class Outcome(ContractModel):
    operation_key:str
    approval_id:UUID
    status:Literal['PENDING','CONFIRMED','FAILED_CONFIRMED','OUTCOME_UNKNOWN','PREVIEW_REQUIRED','RECONCILIATION_REQUIRED']
    reason_code:str
    entry_id:str|None
    observed_digest:str|None=None
    duplicate_submission:bool=False
    confirmation_kind:Literal['LOCAL_SANDBOX_OBSERVATION']='LOCAL_SANDBOX_OBSERVATION'
    external_calendar:Literal[False]=False
