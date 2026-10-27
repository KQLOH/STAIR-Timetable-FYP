"""Source-linked evidence adapted from the baseline; see docs/m1_provenance.json."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BoundingBox(ContractModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: Literal["PDF_POINTS", "PIXELS", "NORMALIZED"]

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("Bounding box bounds must be ordered")
        if self.coordinate_space == "NORMALIZED" and not all(0 <= v <= 1 for v in (self.x0, self.y0, self.x1, self.y1)):
            raise ValueError("Normalized coordinates must lie in 0..1")
        return self


class SourceDocument(ContractModel):
    """A capture identity is separate from a logical timetable/session identity."""

    source_document_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route: Literal["tarumt_html", "native_pdf", "ocr", "text", "authority"]
    media_type: str = Field(min_length=1)
    source_family_id: str | None = Field(default=None, min_length=1)
    timetable_id: str | None = Field(default=None, min_length=1)
    predecessor_document_id: str | None = Field(default=None, min_length=1)
    content_hash_basis: Literal["original_bytes", "decoded_text_utf8", "parser_output_json"]


class EvidenceSpan(ContractModel):
    evidence_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    route: Literal["tarumt_html", "native_pdf", "ocr", "text", "authority"]
    raw_text: str
    raw_html: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    locator: str | None = None
    transformations: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)


class ParseIssue(ContractModel):
    """A retained processing failure, never a fabricated successful event."""

    code: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    candidate_id: str | None = None
    detail: str | None = None
