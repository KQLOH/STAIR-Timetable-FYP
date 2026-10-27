"""S2b application evidence, separate from M1, S2a and registered research DTOs."""
from typing import Literal
from pydantic import Field, model_validator
from backend.models.evidence import ContractModel, BoundingBox
from backend.layout.ruled_models import Word

PROFILE = 'ocr-block-grid-s2b-v1'
MAX_PIXELS = 8_000_000
MAX_TOTAL_PIXELS = 16_000_000
MAX_PAGES = 4
MAX_REGIONS = 24
MAX_DOCUMENT_REGIONS = 32
MAX_WORDS = 1600
MAX_DOCUMENT_WORDS = 3200
MAX_OPERATIONS = 400_000


class BlockWord(Word):
    region_id: str | None = None
    capture_route: str
    transform: dict


class ColorRegion(ContractModel):
    region_id: str
    bbox: BoundingBox
    pixel_color_key: int = Field(ge=0)
    fill_coverage: float = Field(ge=0, le=1)
    boundary_status: Literal['RECTANGULAR','AMBIGUOUS_COMPONENT'] = 'RECTANGULAR'
    inner_margin_pixels: int = Field(default=0,ge=0)
    geometry_method: Literal['quantized-color-components-s2b-v1'] = 'quantized-color-components-s2b-v1'


class BlockPage(ContractModel):
    page: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    route: str
    transform: dict
    runtime: dict
    words: tuple[BlockWord, ...] = Field(max_length=MAX_WORDS)
    regions: tuple[ColorRegion, ...] = Field(max_length=MAX_REGIONS)
    vertical_rules: tuple[float, ...] = Field(default=(), max_length=128)
    horizontal_rules: tuple[float, ...] = Field(default=(), max_length=64)
    diagnostics: tuple[str, ...] = ()
    detection_operations: int = Field(ge=0, le=MAX_OPERATIONS)

    @model_validator(mode='after')
    def bounds(self):
        if self.width*self.height>MAX_PIXELS:raise ValueError('S2B_PIXEL_CAP')
        ids={r.region_id for r in self.regions}
        if len(ids)!=len(self.regions):raise ValueError('S2B_DUPLICATE_REGION')
        for item in (*self.words,*self.regions):
            b=item.bbox
            if b.coordinate_space!='PIXELS' or not (0<=b.x0<b.x1<=self.width and 0<=b.y0<b.y1<=self.height):
                raise ValueError('S2B_PIXEL_COORDINATES')
        if any(w.region_id is not None and w.region_id not in ids for w in self.words):
            raise ValueError('S2B_UNKNOWN_WORD_REGION')
        if any(not 0<=v<self.width for v in self.vertical_rules) or any(not 0<=v<self.height for v in self.horizontal_rules):
            raise ValueError('S2B_RULE_COORDINATES')
        return self


class BlockEvidence(ContractModel):
    profile: Literal['ocr-block-grid-s2b-v1'] = PROFILE
    schema_version: Literal['s2b.1'] = 's2b.1'
    source_document_id: str
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    pages: tuple[BlockPage, ...] = Field(min_length=1, max_length=MAX_PAGES)

    @model_validator(mode='after')
    def bounded(self):
        if sum(p.width*p.height for p in self.pages)>MAX_TOTAL_PIXELS:raise ValueError('S2B_TOTAL_PIXEL_CAP')
        if sum(len(p.words) for p in self.pages)>MAX_DOCUMENT_WORDS:raise ValueError('S2B_DOCUMENT_WORD_CAP')
        if sum(len(p.regions) for p in self.pages)>MAX_DOCUMENT_REGIONS:raise ValueError('S2B_DOCUMENT_REGION_CAP')
        if len({p.page for p in self.pages})!=len(self.pages):raise ValueError('S2B_DUPLICATE_PAGE')
        ids=[w.evidence_id for p in self.pages for w in p.words]
        if len(set(ids))!=len(ids):raise ValueError('S2B_DUPLICATE_WORD')
        return self
