"""Small additive endpoint. No credentials, file I/O, evaluation or calendar operations."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from backend.reconstruction.models import ReconstructionRequest
from backend.reconstruction.service import reconstruct

router=APIRouter()
MAX_BODY_BYTES=1024*1024


@router.post('/api/reconstruct')
async def reconstruct_endpoint(request: Request):
    if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
        raise HTTPException(415,'JSON evidence packet required')
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>MAX_BODY_BYTES:
            raise HTTPException(413,'M4 evidence request exceeds 1 MiB')
    try:
        payload=ReconstructionRequest.model_validate_json(body)
    except (ValidationError, ValueError):
        raise HTTPException(422,'Invalid M4 evidence contract; explicit decoder_mode and profile required') from None
    if payload.profile=='registered':
        raise HTTPException(409,'Registered reconstruction blocked: PRE-01/PRE-02 and scoring/configuration freeze outstanding')
    try:
        if payload.profile=='ocr-block-grid-s2b-v1':
            from backend.layout.block_grid import reconstruct_blocks
            from backend.reconstruction.models import Context
            if payload.context!=Context():raise ValueError('S2B_CONTEXT_UNSUPPORTED')
            result,_,_=await run_in_threadpool(reconstruct_blocks,payload.packet,payload.layout_evidence,payload.decoder_mode)
            return result
        if payload.profile=='ocr-ruled-grid-s2a-v1':
            from backend.layout.ruled import reconstruct_ruled
            from backend.reconstruction.models import Context
            if payload.context!=Context():raise ValueError('S2A_CONTEXT_UNSUPPORTED')
            result,_,_=await run_in_threadpool(reconstruct_ruled,payload.packet,payload.layout_evidence,payload.decoder_mode)
            return result
        if payload.profile=='ocr-layout-m52-v1':
            from backend.layout.service import reconstruct_layout
            from backend.reconstruction.models import Context
            if payload.context!=Context():raise ValueError('LAYOUT_CONTEXT_UNSUPPORTED')
            result,_,_=await run_in_threadpool(reconstruct_layout,payload.packet,payload.decoder_mode)
            return result
        return await run_in_threadpool(reconstruct,payload.packet,payload.decoder_mode,payload.context)
    except ValueError as error:
        if payload.profile in {'ocr-layout-m52-v1','ocr-ruled-grid-s2a-v1','ocr-block-grid-s2b-v1'}:raise HTTPException(422,str(error)) from None
        raise HTTPException(422,'Invalid reconstruction context or settings') from None
