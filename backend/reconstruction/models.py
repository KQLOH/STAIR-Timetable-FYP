"""Additive M4 contracts. M1 schemas remain unchanged."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal
import unicodedata

from pydantic import Field, model_validator
from backend.models.evidence import ContractModel, EvidenceSpan, SourceDocument, ParseIssue
from backend.models.event import CanonicalTimetable, FieldState
from backend.layout.ruled_models import RuledEvidence
from backend.layout.block_models import BlockEvidence


def canonical_bytes(value):
    def nfc(item):
        if isinstance(item, str):
            return unicodedata.normalize('NFC', item)
        if isinstance(item, dict):
            return {nfc(k): nfc(v) for k, v in item.items()}
        if isinstance(item, (tuple, list)):
            return [nfc(v) for v in item]
        return item
    return (json.dumps(nfc(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def digest(value):
    return sha256(canonical_bytes(value)).hexdigest()


class Settings(ContractModel):
    profile_id: str
    status: Literal['PROVISIONAL_APPLICATION_ONLY']
    algorithm: str
    base_weights: dict[str, int]
    route_support_tables: dict[str, tuple[tuple[int, int], ...]] = {}
    omission_cost_units: int = Field(ge=0)
    soft_weights: dict[str, int]
    provenance_min_support_units: int | None = Field(default=None, ge=0, le=10000)
    typical_relation_min_units: int | None = Field(default=None, ge=0, le=10000)
    max_field_spans: int = Field(ge=1, le=12)
    max_field_values: int = Field(ge=1, le=8)
    max_edges_per_group: int = Field(ge=1, le=64)
    max_component_groups: int = Field(ge=1, le=12)
    max_assignments: int = Field(ge=1, le=250000)
    max_operations: int = Field(ge=1, le=2000000)
    watchdog_seconds: float = Field(gt=0, le=30)
    header_max_gap: float = Field(ge=0)
    header_gap_unit: str
    trace_limit: int = Field(ge=0, le=256)
    registered_blockers: tuple[str, ...]
    score_note: str

    @model_validator(mode='after')
    def scoring_valid(self):
        if set(self.base_weights) != {'extraction','relations','completeness'} or sum(self.base_weights.values()) != 10000 or any(w < 0 or w % 500 for w in self.base_weights.values()):
            raise ValueError('Weights must sum to 10000 on the documented 0.05 grid')
        if set(self.soft_weights) != {'P01','P02'} or any(w < 0 for w in self.soft_weights.values()):
            raise ValueError('Nonnegative P01/P02 weights required')
        if self.base_weights['extraction'] and not self.route_support_tables:
            raise ValueError('Extraction weighting requires explicit route support tables')
        for table in self.route_support_tables.values():
            if not table or any(not 0 <= x <= 10000 or not 0 <= y <= 10000 for x,y in table) or any(a[0] >= b[0] or a[1] > b[1] for a,b in zip(table,table[1:])):
                raise ValueError('Route support must be an ordered monotone integer table')
        if self.soft_weights['P01'] and self.provenance_min_support_units is None or self.soft_weights['P02'] and self.typical_relation_min_units is None:
            raise ValueError('Positive soft weights require explicit criteria')
        return self


def development_settings(**overrides):
    values = json.loads((Path(__file__).resolve().parents[2]/'config/m4-development.json').read_text())
    return Settings.model_validate({**values, **overrides})


class PageCoordinates(ContractModel):
    page: int = Field(ge=1)
    dpi: float | None = Field(default=None, gt=0)
    rotation_normalized: bool = False


class EvidenceLink(ContractModel):
    source_evidence_id: str
    target_evidence_id: str
    kind: Literal['shared_context','event_continuation']
    reason_evidence_ids: tuple[str, ...] = Field(min_length=1)


class TermAuthority(ContractModel):
    start: date
    end: date
    authority_record_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def ordered(self):
        if self.start > self.end:
            raise ValueError('Teaching-term bounds must be ordered')
        return self


class Context(ContractModel):
    coordinates: tuple[PageCoordinates, ...] = ()
    links: tuple[EvidenceLink, ...] = ()
    term: TermAuthority | None = None
    timezone_authority_ids: tuple[str, ...] = ()
    alternative_group_evidence_ids: tuple[str, ...] = ()


class FieldNode(ContractModel):
    candidate_id: str
    node_id: str
    field: str
    value: Any = None
    state: FieldState
    raw: str | None = None
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    ownership_units: tuple[str, ...] = ()
    shared: bool = False
    transformations: tuple[str, ...] = ()


class Relation(ContractModel):
    left: str
    right: str
    kind: Literal['same_source_region','same_source_group','header_scope','explicit_continuation']
    compatible: bool


class EventGroup(ContractModel):
    group_id: str
    evidence_ids: tuple[str, ...]
    domains: dict[str, tuple[FieldNode, ...]]
    relations: tuple[Relation, ...] = ()
    diagnostics: tuple[str, ...] = ()


class Graph(ContractModel):
    source: SourceDocument
    evidence: tuple[EvidenceSpan, ...]
    groups: tuple[EventGroup, ...]
    context: Context
    settings_digest: str
    diagnostics: tuple[str, ...] = ()
    capture_aliases: dict[str, str] = Field(default_factory=dict, exclude=True)
    input_issues: tuple[ParseIssue, ...] = ()
    input_revision_provenance: tuple[dict[str, Any], ...] = Field(default=(), exclude=True)


class Finding(ContractModel):
    rule_id: str
    passed: bool | None
    severity: Literal['HARD','SOFT','INFO']
    reason: str
    group_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    magnitude_units: int = Field(default=0, ge=0, le=10000)


class ReconstructionRequest(ContractModel):
    packet: CanonicalTimetable
    decoder_mode: Literal['constrained','unconstrained']
    profile: Literal['m4-development-v1','ocr-layout-m52-v1','ocr-ruled-grid-s2a-v1','ocr-block-grid-s2b-v1','registered']
    context: Context = Field(default_factory=Context)
    layout_evidence: RuledEvidence | BlockEvidence | None = None

    @model_validator(mode='after')
    def bounded(self):
        if self.profile=='ocr-ruled-grid-s2a-v1':
            if not isinstance(self.layout_evidence,RuledEvidence):raise ValueError('S2a evidence required')
        elif self.profile=='ocr-block-grid-s2b-v1':
            if not isinstance(self.layout_evidence,BlockEvidence):raise ValueError('S2b evidence required')
        elif self.layout_evidence is not None:
            raise ValueError('Supplementary evidence requires its explicit versioned profile')
        if len(self.packet.evidence) > 512 or sum(len(s.raw_text) for s in self.packet.evidence) > 500000:
            raise ValueError('Evidence packet exceeds M4 development limits')
        return self
