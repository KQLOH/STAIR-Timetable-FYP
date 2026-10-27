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
