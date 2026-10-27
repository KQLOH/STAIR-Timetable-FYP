"""Stateless public assessment; stateful review routes disabled unless host-bound.

The optional bridge is explicitly loopback-only and capability-protected. It is
not a deployed identity provider or the TAR UMT session authentication scheme.
"""
from dataclasses import dataclass
from ipaddress import ip_address
import re
from uuid import UUID

from fastapi import APIRouter, Request, Response, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import Field, ValidationError
from backend.models.evidence import ContractModel
from backend.models.event import EventContent, FieldState
from backend.review.binding import AssessmentInput, bind_input
from backend.review.models import CorrectionSubmission, ReviewError
from backend.review.policy import assess_run, explain

def _no_cache(response:Response):
    response.headers['Cache-Control']='no-store'
    response.headers['Pragma']='no-cache'


router=APIRouter(dependencies=[Depends(_no_cache)])
MAX_BODY_BYTES=2*1024*1024


@dataclass(frozen=True)
class ReviewBridge:
    store: object
    allowed_origins: tuple[str,...] = ('http://127.0.0.1:5500','http://localhost:5500')


def _bridge(request):
    bridge=getattr(request.app.state,'m5_review_bridge',None)
    if not isinstance(bridge,ReviewBridge):
        raise HTTPException(403,'REVIEW_HOST_BOUNDARY_NOT_CONFIGURED')
    try:
        if not request.client or not ip_address(request.client.host).is_loopback:
            raise ValueError('Nonlocal peer')
    except ValueError:
        raise HTTPException(403,'LOCAL_TRUSTED_HOST_REQUIRED') from None
    origin=request.headers.get('origin')
    if origin is not None and origin not in bridge.allowed_origins:
        raise HTTPException(403,'REVIEW_ORIGIN_DENIED')
    auth=request.headers.get('authorization','')
    if not auth.startswith('Bearer '): raise HTTPException(403,'UNAUTHORIZED_REVIEW')
    try:
        principal=bridge.store.authority.authenticate(auth[7:])
        bridge.store.authority.require(principal,'read')
    except ReviewError as exc:
        raise HTTPException(exc.status_code,exc.code) from None
    return bridge.store,principal


async def _body(request,model):
    if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
        raise HTTPException(415,'JSON_BODY_REQUIRED')
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>MAX_BODY_BYTES: raise HTTPException(413,'REVIEW_REQUEST_TOO_LARGE')
    try: return model.model_validate_json(body)
    except (ValidationError,ValueError): raise HTTPException(422,'INVALID_REVIEW_CONTRACT') from None


def _redact(value):
    if isinstance(value,str):
        return re.sub(r'(?:[A-Za-z]:[\\/]|\\\\)[^\s"<>]+','[LOCAL_PATH_REDACTED]',value)
    if isinstance(value,dict): return {k:_redact(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [_redact(v) for v in value]
    return value


def _decision(record):
    # Public assessment never returns source text, input provenance paths or actor information.
    result=record.model_dump(mode='json')
    result['findings']=[{'rule_id':f.rule_id,'passed':f.passed,'severity':f.severity} for f in record.findings]
    result['explanations']=explain(record.reason_codes)
    return result


async def _invoke(function,*args,**kwargs):
    try: return await run_in_threadpool(function,*args,**kwargs)
    except ReviewError as exc: raise HTTPException(exc.status_code,exc.code) from None
    except ValueError: raise HTTPException(422,'INVALID_REVIEW_OPERATION') from None


@router.post('/api/policy/assess')
async def policy_assess(request:Request):
    payload=await _body(request,AssessmentInput)
    run=await _invoke(bind_input,payload)
    decisions=await _invoke(assess_run,run)
    return {'profile':'m5-development-v1','provenance_trust':'CALLER_SUPPLIED_INTEGRITY_CHECKED',
            'registered_routing_ready':False,'decisions':[_decision(d) for d in decisions]}


@router.post('/api/review/ingest')
async def ingest(request:Request):
    store,principal=_bridge(request)
    payload=await _body(request,AssessmentInput)
    ids=await _invoke(store.ingest,payload,principal)
    return {'task_ids':ids}


@router.get('/api/review/queue')
async def queue(request:Request,offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    store,principal=_bridge(request)
    return _redact(await _invoke(store.queue_summary,principal,offset,limit))


@router.get('/api/review/session')
async def session(request:Request):
    store,principal=_bridge(request)
    return {'actor_reference':principal.actor_id,'expires_at':principal.expires_at.isoformat(),
        'permissions':sorted(principal.permissions),'mode':principal.mode,
        'source_bindings':sorted(principal.source_bindings) if principal.source_bindings is not None else None,
        'scoped_workflow_enabled':principal.source_bindings is not None,
        'storage':'PROCESS_LOCAL_NOT_RESTART_PERSISTENT','purpose':'APPLICATION_REPAIR_NOT_GOLD',
        'correction_contract':{'fields':list(EventContent.model_fields),'states':[s.value for s in FieldState],
            'max_fields':19,'max_evidence_ids':20,'max_raw_characters':10000,'max_reason_characters':2000,
            'human_assertions':'EXPLICIT_LINKED_SOURCE_READING_NOT_ORIGINAL_OCR_NOT_GOLD'}}


@router.post('/api/review/session/end')
async def end_session(request:Request):
    store,principal=_bridge(request)
    await _invoke(store.authority.revoke,principal)
    return {'revoked':True}


@router.get('/api/review/tasks/{task_id}')
async def task(task_id:UUID,request:Request):
    store,principal=_bridge(request)
    view=await _invoke(store.get,task_id,principal)
    spans=await _invoke(store.evidence,task_id,principal)
    source=await _invoke(store.source,task_id,principal)
    return _redact({'task':view.model_dump(mode='json'),'source':source.model_dump(mode='json'),'evidence':[{'evidence_id':s.evidence_id,'source_document_id':s.source_document_id,
        'page':s.page,'bbox':s.bbox.model_dump(mode='json') if s.bbox else None,'raw_text':s.raw_text,'confidence':s.confidence,
        'route':s.route,'transformations':s.transformations,
        'kind':'HUMAN_ASSERTION_NOT_GOLD' if s.evidence_id.startswith('human:') else 'SOURCE_EXTRACTION'} for s in spans]})


@router.get('/api/review/tasks/{task_id}/snapshot')
async def snapshot(task_id:UUID,request:Request):
    store,principal=_bridge(request)
    return _redact(await _invoke(store.snapshot,task_id,principal))


@router.get('/api/review/tasks/{task_id}/history')
async def history(task_id:UUID,request:Request):
    store,principal=_bridge(request)
    return _redact(await _invoke(store.history,task_id,principal))


class TransitionInput(ContractModel):
    expected_etag:int=Field(ge=1)
    action:str
    reason:str=Field(min_length=1,max_length=2000)
    assignee:str|None=None


@router.post('/api/review/tasks/{task_id}/transition')
async def transition(task_id:UUID,request:Request):
    store,principal=_bridge(request)
    payload=await _body(request,TransitionInput)
    if payload.action=='claim':
        result=await _invoke(store.assign,task_id,principal.actor_id,payload.expected_etag,principal)
    elif payload.action=='assign':
        if not payload.assignee: raise HTTPException(422,'ASSIGNEE_REQUIRED')
        result=await _invoke(store.assign,task_id,payload.assignee,payload.expected_etag,principal)
    elif payload.action=='start': result=await _invoke(store.start,task_id,payload.expected_etag,principal)
    elif payload.action=='resume': result=await _invoke(store.resume,task_id,payload.expected_etag,principal)
    else: result=await _invoke(store.finish,task_id,payload.expected_etag,principal,action=payload.action,reason=payload.reason)
    return _redact(result.model_dump(mode='json'))


@router.post('/api/review/tasks/{task_id}/corrections')
async def correction(task_id:UUID,request:Request):
    store,principal=_bridge(request)
    payload=await _body(request,CorrectionSubmission)
    result=await _invoke(store.submit,task_id,payload,principal)
    if not result['applied']:
        raise HTTPException(422,{'code':result['error_code'],'proposal_retained':True})
    return _redact(result['task'].model_dump(mode='json'))


class RevalidationInput(ContractModel):
    expected_etag:int=Field(ge=1)


@router.post('/api/review/tasks/{task_id}/revalidate')
async def revalidate(task_id:UUID,request:Request):
    store,principal=_bridge(request)
    payload=await _body(request,RevalidationInput)
    return _redact((await _invoke(store.revalidate,task_id,payload.expected_etag,principal)).model_dump(mode='json'))
