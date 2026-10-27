"""Supplementary application evidence; does not extend or replace M1 schemas."""
from typing import Literal
from pydantic import Field, model_validator
from backend.models.evidence import ContractModel, BoundingBox

PROFILE = 'ocr-ruled-grid-s2a-v1'
MAX_PIXELS = 8_000_000
MAX_TOTAL_PIXELS = 16_000_000
MAX_WORDS = 1600
MAX_CELLS = 160
MAX_PAGES = 8
MAX_GROUPS = 40


class Word(ContractModel):
    evidence_id: str
    text: str
    bbox: BoundingBox
    confidence: float | None = Field(default=None, ge=0, le=1)
    parent_line_id: str
    block: int
    paragraph: int
    line: int
    reading_order: int = Field(ge=0)


class RuledPage(ContractModel):
    page: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    route: str
    transform: dict
    runtime: dict
    words: tuple[Word, ...] = Field(max_length=MAX_WORDS)
    # Pixel-derived separator coordinates/coverage; never textual source values.
    grid: dict

    @model_validator(mode='after')
    def bounded(self):
        if self.width*self.height > MAX_PIXELS:
            raise ValueError('S2A_PIXEL_CAP')
        if len(self.grid.get('xs', [])) > 32 or len(self.grid.get('ys', [])) > 32:
            raise ValueError('S2A_SEPARATOR_CAP')
        coverage = self.grid.get('vertical_coverage', [])
        if len(coverage) > 32 or any(len(row)>32 for row in coverage):
            raise ValueError('S2A_SEPARATOR_CAP')
        for w in self.words:
            b=w.bbox
            if b.coordinate_space!='PIXELS' or not (0<=b.x0<b.x1<=self.width and 0<=b.y0<b.y1<=self.height):
                raise ValueError('S2A_WORD_COORDINATES')
        return self


class RuledEvidence(ContractModel):
    profile: Literal['ocr-ruled-grid-s2a-v1'] = PROFILE
    schema_version: Literal['s2a.1'] = 's2a.1'
    source_document_id: str
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    pages: tuple[RuledPage, ...] = Field(min_length=1, max_length=MAX_PAGES)

    @model_validator(mode='after')
    def bounded(self):
        if sum(p.width*p.height for p in self.pages)>MAX_TOTAL_PIXELS:
            raise ValueError('S2A_TOTAL_PIXEL_CAP')
        if len({p.page for p in self.pages})!=len(self.pages):
            raise ValueError('S2A_DUPLICATE_PAGE')
        if len({w.evidence_id for p in self.pages for w in p.words})!=sum(len(p.words) for p in self.pages):
            raise ValueError('S2A_DUPLICATE_WORD')
        return self
