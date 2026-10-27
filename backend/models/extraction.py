from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class EvidenceRegion(BaseModel):
    evidence_id: str
    page: int = Field(ge=1)
    line: int | None = Field(default=None, ge=1)
    group: str | None = None
    text: str
    bbox: BoundingBox | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    adapter: str
    continuation_of: str | None = None


class EvidencePage(BaseModel):
    page: int = Field(ge=1)
    width: float | None = None
    height: float | None = None
    text: str = ""
    regions: list[EvidenceRegion] = Field(default_factory=list)
    runtime: dict = Field(default_factory=dict)


class DocumentEvidence(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    source_id: str
    route_type: Literal["upload_text", "upload_pdf", "upload_ocr", "upload_image", "intranet", "sample"]
    original_label: str
    media_type: str
    checksum: str
    parser_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pages: list[EvidencePage]
    warnings: list[str] = Field(default_factory=list)

    @property
    def regions(self) -> list[EvidenceRegion]:
        return [region for page in self.pages for region in page.regions]


class EvidenceReference(BaseModel):
    evidence_id: str
    source_id: str
    page: int = Field(ge=1)
    line: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    excerpt: str = ""


class CandidateSpan(BaseModel):
    span_id: str
    field_type: Literal[
        "subject", "subject_name", "day", "date", "start_time", "end_time",
        "venue", "class_type", "recurrence"
    ]
    raw_text: str
    normalized_value: str | None = None
    extraction_confidence: float = Field(ge=0, le=1)
    evidence: EvidenceReference


class EventFieldNode(BaseModel):
    node_id: str
    node_type: str
    value: str | None
    span: CandidateSpan


class EventRelation(BaseModel):
    relation_id: str
    source_node_id: str
    target_node_id: str
    relation_type: Literal["SAME_EVENT", "RECURRENCE_OF"] = "SAME_EVENT"
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
