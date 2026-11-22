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
# Full bound S2b graphs include source, word and derived evidence. Only the
# authenticated queue-ingestion route accepts this larger bounded envelope.
MAX_REVIEW_INGEST_BYTES=8*1024*1024


@dataclass(frozen=True)
class ReviewBridge:
    store: object
    allowed_origins: tuple[str,...] = ('http://127.0.0.1:5500','http://localhost:5500')


def _bridge(request):
    # Only the account host middleware may install this request-local bridge.
    # Never change app.state to switch users: concurrent requests must stay isolated.
    bridge=getattr(request.state,'student_review_bridge',None) or getattr(request.app.state,'m5_review_bridge',None)
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
    student_principal=getattr(request.state,'student_review_principal',None)
    if student_principal is not None:
        try: bridge.store.authority.require(student_principal,'read')
        except ReviewError as exc: raise HTTPException(exc.status_code,exc.code) from None
        return bridge.store,student_principal
    auth=request.headers.get('authorization','')
    if not auth.startswith('Bearer '): raise HTTPException(403,'UNAUTHORIZED_REVIEW')
    try:
        principal=bridge.store.authority.authenticate(auth[7:])
        bridge.store.authority.require(principal,'read')
    except ReviewError as exc:
        raise HTTPException(exc.status_code,exc.code) from None
    return bridge.store,principal


async def _body(request,model,*,max_bytes=MAX_BODY_BYTES,field_errors=False):
    if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
        raise HTTPException(415,'JSON_BODY_REQUIRED')
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>max_bytes: raise HTTPException(413,'REVIEW_REQUEST_TOO_LARGE')
    try: return model.model_validate_json(body)
    except ValidationError as error:
        if not field_errors:raise HTTPException(422,'INVALID_REVIEW_CONTRACT') from None
        allowed={'expected_version','rows','decision','candidate_id','page','source_session','evidence_ids','note','checked_pages','source_confirmed','scope_confirmed'}
        fields=[]
        for item in error.errors(include_input=False,include_url=False,include_context=False):
            kind=item['type'];path=[p if isinstance(p,int) or p in allowed else 'field' for p in item['loc']]
            message=('This field is required.' if kind in {'missing','string_too_short'} else 'Use at most '+str({'source_session':200,'note':2000,'candidate_id':200}.get(path[-1], 'the allowed number of'))+' characters.' if kind=='string_too_long' else 'Enter a whole page number within the source.' if kind.startswith(('int','greater','less')) else 'Choose a supported review status.' if path[-1:] == ['decision'] else 'Check this field; its value is invalid.')
            fields.append({'path':path,'code':kind,'message':message})
        raise HTTPException(422,{'code':'INVALID_SOURCE_CHECK_FORM','field_errors':fields}) from None
    except ValueError:raise HTTPException(422,'INVALID_REVIEW_CONTRACT') from None


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
    except ReviewError as exc:
        detail={'code':exc.code,'field_errors':exc.field_errors} if getattr(exc,'field_errors',None) else exc.code
        raise HTTPException(exc.status_code,detail) from None
    except ValueError: raise HTTPException(422,'INVALID_REVIEW_OPERATION') from None


@router.post('/api/policy/assess')
async def policy_assess(request:Request):
    payload=await _body(request,AssessmentInput)
    run=await _invoke(bind_input,payload)
    registry=getattr(request.app.state,'m5_operating_registry',None)
    if registry is not None and registry.mode!='TEST_ONLY':registry=None
    decisions=await _invoke(assess_run,run,registry=registry)
    from backend.review.source_routing import report
    registered=any(d.registration_mode for d in decisions)
    return {'profile':'m5-registered-v1' if registered else payload.policy_profile,'provenance_trust':'CALLER_SUPPLIED_INTEGRITY_CHECKED',
            'registered_routing_ready':registered,'routing_report':report(decisions),'decisions':[_decision(d) for d in decisions]}


@router.post('/api/review/ingest')
async def ingest(request:Request):
    store,principal=_bridge(request)
    await _invoke(store.authority.require,principal,'queue')
    payload=await _body(request,AssessmentInput,max_bytes=MAX_REVIEW_INGEST_BYTES)
    ids=await _invoke(store.ingest,payload,principal)
    return {'task_ids':ids}


@router.post('/api/review/connect-run')
async def connect_run(request:Request):
    store,principal=_bridge(request)
    payload=await _body(request,AssessmentInput,max_bytes=MAX_REVIEW_INGEST_BYTES)
    return await _invoke(store.connect_run,payload,principal)


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
        'storage':store.storage_kind,'purpose':'APPLICATION_REPAIR_NOT_GOLD','source_check_recovery_version':1,
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


# Same source-scoped host transport as field review; no HTTP verifier enrolment.
from backend.analysis.schedule_checks import CheckStart, InventorySubmission, VerificationSubmission, CheckAnalysis


def _checks(request):
    store,principal=_bridge(request)
    service=getattr(request.app.state,'m7_schedule_checks',None)
    if service is None or service.store is not store:raise HTTPException(403,'SCHEDULE_CHECK_HOST_REQUIRED')
    return service,principal


@router.post('/api/review/schedule-checks/start')
async def start_schedule_check(request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.start,await _body(request,CheckStart),principal))


@router.get('/api/review/schedule-checks/{check_id}')
async def get_schedule_check(check_id:UUID,request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.get,str(check_id),principal))


@router.post('/api/review/schedule-checks/{check_id}/primary')
async def primary_schedule_check(check_id:UUID,request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.submit,str(check_id),await _body(request,InventorySubmission,field_errors=True),principal))


@router.post('/api/review/schedule-checks/{check_id}/verify')
async def verify_schedule_check(check_id:UUID,request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.verify,str(check_id),await _body(request,VerificationSubmission),principal))


@router.post('/api/review/schedule-checks/{check_id}/analyze')
async def analyze_schedule_check(check_id:UUID,request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.analyze,str(check_id),await _body(request,CheckAnalysis),principal))


@router.post('/api/review/schedule-checks/{check_id}/grounded')
async def grounded_schedule_check(check_id:UUID,request:Request):
    from backend.analysis.schedule_checks import CheckGrounded
    from backend.analysis.grounded import GroundedAnalyser
    from backend.analysis.models import binding
    service,principal=_checks(request)
    body=await _body(request,CheckGrounded)
    analysis,meta=await _invoke(service.prepare_analysis,str(check_id),body,principal)
    engine=getattr(request.app.state,'m7_grounded_service',None) or GroundedAnalyser()
    if not isinstance(engine,GroundedAnalyser):raise HTTPException(503,'GROUNDED_HOST_CONFIGURATION_INVALID')
    result=await engine.run(analysis,service.authority,allow_provider=body.allow_provider)
    current,current_meta=await _invoke(service.prepare_analysis,str(check_id),body,principal)
    if binding(current)!=binding(analysis):
        # Source/check state changed during a call: never expose the old free-time output.
        result=await GroundedAnalyser().run(current,service.authority)
        result['grounded']['reason']='SOURCE_CHANGED_DURING_GENERATION'
    result['whole_schedule_check']=current_meta
    return _redact(result)


@router.get('/api/review/schedule-checks/for-task/{task_id}')
async def latest_schedule_check(task_id:UUID,request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.latest,str(task_id),principal))


@router.post('/api/review/schedule-checks/{check_id}/source-page/{page}')
async def source_schedule_page(check_id:UUID,page:int,request:Request):
    service,principal=_checks(request)
    if request.headers.get('content-type','').split(';')[0]!='application/pdf':raise HTTPException(415,'PDF_BODY_REQUIRED')
    raw=bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw)>20*1024*1024:raise HTTPException(413,'SOURCE_PREVIEW_LIMIT')
    return await _invoke(service.source_page,str(check_id),page,bytes(raw),principal)


@router.post('/api/review/schedule-checks/{check_id}/temporal')
async def temporal_schedule_check(check_id:UUID,request:Request):
    from backend.analysis.temporal_scope import TemporalSubmission
    service,principal=_checks(request)
    return _redact(await _invoke(service.save_temporal,str(check_id),await _body(request,TemporalSubmission),principal))


@router.post('/api/review/schedule-checks/{check_id}/draft')
async def save_schedule_draft(check_id:UUID,request:Request):
    from backend.analysis.schedule_drafts import DraftSubmission
    service,principal=_checks(request)
    # Only the primary author can retrieve their own entered text, verbatim.
    return await _invoke(service.save_draft,str(check_id),await _body(request,DraftSubmission),principal)


@router.get('/api/review/schedule-checks/{check_id}/draft')
async def get_schedule_drafts(check_id:UUID,request:Request):
    service,principal=_checks(request)
    return await _invoke(service.recovery_drafts,str(check_id),principal)


@router.get('/api/review/schedule-checks/for-task/{task_id}/available')
async def available_schedule_checks(task_id:UUID,request:Request):
    service,principal=_checks(request)
    return _redact(await _invoke(service.available_checks,str(task_id),principal))
