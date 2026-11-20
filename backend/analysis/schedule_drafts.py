"""Unvalidated encrypted recovery data. Never a source check or verification."""
from uuid import UUID
from pydantic import Field
from backend.models.evidence import ContractModel

class DraftRow(ContractModel):
    decision:str=Field(max_length=40)
    candidate_id:str|None=Field(default=None,max_length=200)
    page:str=Field(default='',max_length=32)
    source_session:str=Field(default='',max_length=4000)
    evidence_ids:tuple[str,...]=Field(default=(),max_length=100)
    note:str=Field(default='',max_length=6000)

class DraftControl(ContractModel):
    value:str=Field(default='',max_length=10000)
    checked:bool=False
    selected:tuple[str,...]=Field(default=(),max_length=100)

class ExtraDraft(ContractModel):
    temporal_weeks:int=Field(default=0,ge=0,le=100)
    controls:tuple[DraftControl,...]=Field(default=(),max_length=5000)

class InventoryDraft(ContractModel):
    rows:tuple[DraftRow,...]=Field(default=(),max_length=400)
    checked_pages:tuple[int,...]=Field(default=(),max_length=100)
    source_confirmed:bool=False
    scope_confirmed:bool=False
    note:str=Field(default='',max_length=10000)
    extra:ExtraDraft|None=None

class DraftSubmission(ContractModel):
    expected_version:int=Field(ge=1)
    client_id:UUID
    form:InventoryDraft
