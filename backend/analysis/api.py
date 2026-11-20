"""Bounded stateless diagnostics; no attestation issuance or calendar endpoints."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from backend.analysis.authority import CompletenessAuthority
from backend.analysis.models import AnalysisInput
from backend.analysis.service import analyze
from backend.review.api import _body, _redact

router = APIRouter()


@router.post('/api/analysis/v1/analyze')
async def schedule_analysis(request: Request):
    payload = await _body(request, AnalysisInput)
    if payload.profile == 'registered': raise HTTPException(409, 'REGISTERED_ANALYSIS_CONFIGURATION_NOT_READY')
    # Explicit trusted bootstrap may supply authority; no startup/test fixture minting.
    authority = getattr(request.app.state, 'm7_completeness_authority', None)
    if authority is not None and not isinstance(authority, CompletenessAuthority):
        raise HTTPException(503, 'COMPLETENESS_HOST_CONFIGURATION_INVALID')
    try: return _redact(await run_in_threadpool(analyze, payload, authority))
    except (ValueError, TypeError): raise HTTPException(422, 'ANALYSIS_TEMPORAL_SCOPE_UNSUPPORTED') from None


@router.post('/api/analysis/v1/student')
async def student_schedule_analysis(request: Request):
    from backend.analysis.student import student_analyze
    payload=await _body(request,AnalysisInput,max_bytes=8*1024*1024)
    if payload.profile!='m7-student-v1':raise HTTPException(422,'STUDENT_ANALYSIS_PROFILE_REQUIRED')
    # Anonymous diagnostics cannot consume protected completeness attestations.
    payload=payload.model_copy(update={'attestation_id':None})
    try:return _redact(await run_in_threadpool(student_analyze,payload))
    except (ValueError,TypeError):raise HTTPException(422,'ANALYSIS_TEMPORAL_SCOPE_UNSUPPORTED') from None


@router.post('/api/analysis/v1/observed')
async def observed_schedule_analysis(request: Request):
    from backend.analysis.models import ObservedInput
    from backend.analysis.student import observed_analyze
    payload=await _body(request,ObservedInput,max_bytes=8*1024*1024)
    return _redact(await run_in_threadpool(observed_analyze,payload))
