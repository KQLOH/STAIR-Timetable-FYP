"""Read-only trusted-bootstrap calendar context, separate from analysis projections.

The approval hash is supplied by the host operator out of band, never read from
the proposed file. It proves file consistency, not that a human audit occurred.
Context references must already exist in the registered source evidence.
"""
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator
from backend.models.evidence import ContractModel
from backend.calendar.models import CalendarContext
from backend.reconstruction.models import digest
from backend.review.models import ReviewError


class ContextRecord(ContractModel):
    source_document_id: str = Field(min_length=1, max_length=200)
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    context: CalendarContext


class ApprovedContexts(ContractModel):
    schema_version: Literal['approved-calendar-context-v1']
    origin: Literal['AUTHENTIC']
    approval_record_id: str = Field(min_length=1, max_length=200)
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: AwareDatetime
    records: tuple[ContextRecord, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        keys = [(r.source_document_id, r.source_sha256, r.evidence_digest) for r in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate context source binding')
        return self


class ApprovedContextFile:
    def __init__(self, path, approval_sha256):
        from hashlib import sha256
        self.path = Path(path).resolve()
        raw = self.path.read_bytes()
        if len(raw) > 2*1024*1024 or sha256(raw).hexdigest() != approval_sha256:
            raise ValueError('Explicit approved calendar-context hash mismatch')
        self.sha256 = approval_sha256
        self.data = ApprovedContexts.model_validate_json(raw)
        if self.data.approved_at > datetime.now(self.data.approved_at.tzinfo):
            raise ValueError('Calendar context approval cannot be in the future')

    def context_for(self, packet):
        from hashlib import sha256
        try:
            if sha256(self.path.read_bytes()).hexdigest() != self.sha256:
                raise ValueError('Changed context file')
        except (OSError, ValueError):
            raise ReviewError('Approved calendar context changed or is unavailable. Restart with reapproved input.', 409) from None
        source = packet.source
        evidence_digest = digest([s.model_dump(mode='json') for s in packet.evidence])
        record = next((r for r in self.data.records if
            (r.source_document_id, r.source_sha256, r.evidence_digest) ==
            (source.source_document_id, source.content_sha256, evidence_digest)), None)
        if record is None:
            raise ReviewError('No approved teaching-week context matches this exact source evidence.', 409)
        spans = {s.evidence_id: s for s in packet.evidence}
        c = record.context
        refs = set(c.timezone_evidence_ids) | set(c.exception_evidence_ids)
        if c.term: refs.update(c.term.evidence_ids)
        refs.update(ref for w in c.weeks for ref in w.evidence_ids)
        if c.lineage:
            raise ReviewError('Source-lineage changes require their separate approved integration.', 409)
        if not refs or any(ref not in spans or spans[ref].route != 'authority' for ref in refs):
            raise ReviewError('Approved temporal context must reference existing source authority evidence.', 409)
        return c
