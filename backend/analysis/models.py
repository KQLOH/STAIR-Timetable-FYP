"""Explicit bounded analysis scope. No implicit horizon or availability."""
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, AwareDatetime, model_validator
from backend.models.evidence import ContractModel
from backend.models.event import CanonicalTimetable
from backend.calendar.models import CalendarContext
from backend.reconstruction.models import digest


class Scope(ContractModel):
    scope_id: str = Field(min_length=1, max_length=100)
    timetable_id: str = Field(min_length=1, max_length=100)
    start: date
    end: date
    timezone: str = Field(min_length=1, max_length=100)
    cohort_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def bounded(self):
        if self.end == date.max or not 0 <= (self.end-self.start).days <= 366:
            raise ValueError('Explicit scope is limited to 367 dates')
        try: ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError): raise ValueError('Unsupported scope timezone') from None
        return self


class Availability(ContractModel):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode='after')
    def ordered(self):
        if self.start >= self.end: raise ValueError('Availability must be ordered')
        return self


class AnalysisInput(ContractModel):
    event_set_id: str = Field(min_length=1, max_length=100)
    profile: Literal['m7-development-v1', 'registered']
    packet: CanonicalTimetable
    scope: Scope
    context: CalendarContext = Field(default_factory=CalendarContext)
    declared_kind: Literal['UNVERIFIED_CANDIDATES', 'EXPLICITLY_INCOMPLETE',
                           'HUMAN_REVIEWED_APPLICATION', 'SYNTHETIC_DEMO']
    attestation_id: str | None = Field(default=None, max_length=100)
    availability: tuple[Availability, ...] = Field(default=(), max_length=100)
    long_block_minutes: int | None = Field(default=None, ge=1, le=1440)

    @model_validator(mode='after')
    def limits(self):
        if len(self.packet.events)>100 or len(self.packet.evidence)>512 or sum(len(s.raw_text) for s in self.packet.evidence)>500000:
            raise ValueError('Analysis input limits exceeded')
        return self


def binding(request):
    """Attestation binds every source/status/revision, scope and temporal authority."""
    return digest({'event_set_id': request.event_set_id, 'packet': request.packet.model_dump(mode='json'),
                   'scope': request.scope.model_dump(mode='json'), 'context': request.context.model_dump(mode='json'),
                   'declared_kind':request.declared_kind})
