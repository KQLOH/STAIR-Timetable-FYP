"""Stateless development assessment; no trusted-host review or mutation routes."""
from typing import Literal, Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import Field
from backend.models.evidence import ContractModel
from backend.models.event import CanonicalTimetable
from backend.reconstruction.models import Context
from backend.review.api import _body, _decision
from backend.layout.service import assess_layout
from backend.layout.ruled_models import RuledEvidence
from backend.layout.block_models import BlockEvidence

router=APIRouter()


class LayoutAssessmentInput(ContractModel):
    source_packet:CanonicalTimetable
    reconstruction:dict[str,Any]
    decoder_mode:Literal['constrained','unconstrained']='constrained'
    reconstruction_context:Context=Field(default_factory=Context)
    policy_profile:Literal['m5-development-v1']='m5-development-v1'


class RuledAssessmentInput(LayoutAssessmentInput):
    layout_evidence:RuledEvidence


class BlockAssessmentInput(LayoutAssessmentInput):
    layout_evidence:BlockEvidence


@router.post('/api/layout/s2b/assess')
async def block_assessment(request:Request):
    from backend.layout.block_grid import assess_blocks
    payload=await _body(request,BlockAssessmentInput)
    if payload.reconstruction_context!=Context():raise HTTPException(422,'S2B_CONTEXT_UNSUPPORTED')
    try:decisions=await run_in_threadpool(assess_blocks,payload.source_packet,payload.layout_evidence,payload.reconstruction,payload.decoder_mode)
    except ValueError as error:raise HTTPException(422,str(error)) from None
    return {'profile':'m5-development-v1','input_profile':'ocr-block-grid-s2b-v1',
            'registered_routing_ready':False,'provenance_trust':'CALLER_SUPPLIED_RECOMPUTED_DEVELOPMENT_ONLY',
            'decisions':[_decision(d) for d in decisions]}


@router.post('/api/layout/s2a/assess')
async def ruled_assessment(request:Request):
    from backend.layout.ruled import assess_ruled
    payload=await _body(request,RuledAssessmentInput)
    if payload.reconstruction_context!=Context():raise HTTPException(422,'S2A_CONTEXT_UNSUPPORTED')
    try:decisions=await run_in_threadpool(assess_ruled,payload.source_packet,payload.layout_evidence,payload.reconstruction,payload.decoder_mode)
    except ValueError as error:raise HTTPException(422,str(error)) from None
    return {'profile':'m5-development-v1','input_profile':'ocr-ruled-grid-s2a-v1',
            'registered_routing_ready':False,'provenance_trust':'CALLER_SUPPLIED_RECOMPUTED_DEVELOPMENT_ONLY',
            'decisions':[_decision(d) for d in decisions]}


@router.post('/api/layout/assess')
async def layout_assessment(request:Request):
    payload=await _body(request,LayoutAssessmentInput)
    if payload.reconstruction_context!=Context():raise HTTPException(422,'LAYOUT_CONTEXT_UNSUPPORTED')
    try:decisions=await run_in_threadpool(assess_layout,payload.source_packet,payload.reconstruction,payload.decoder_mode)
    except ValueError as error:raise HTTPException(422,str(error)) from None
    return {'profile':'m5-development-v1','input_profile':'ocr-layout-m52-v1','registered_routing_ready':False,
            'provenance_trust':'CALLER_SUPPLIED_RECOMPUTED_DEVELOPMENT_ONLY','decisions':[_decision(d) for d in decisions]}
