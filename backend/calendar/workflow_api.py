"""All S3c routes deny unless a trusted host explicitly installs the isolated bridge."""
from ipaddress import ip_address
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Request,Response,HTTPException,Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import Field
from backend.models.evidence import ContractModel
from backend.calendar.models import CalendarError
from backend.calendar.workflow import TestOnlyCalendarBridge
from backend.review.api import _body,_redact
from backend.review.models import ReviewError


def no_cache(response:Response):response.headers['Cache-Control']='no-store'
router=APIRouter(prefix='/test-only',dependencies=[Depends(no_cache)])


def bridge(request):
    host=getattr(request.app.state,'s3c_calendar_bridge',None)
    if not isinstance(host,TestOnlyCalendarBridge):raise HTTPException(403,'TEST_ONLY_CALENDAR_HOST_NOT_CONFIGURED')
    try:
        if not request.client or not ip_address(request.client.host).is_loopback:raise ValueError()
        if request.headers.get('origin') is not None and request.headers['origin'] not in host.allowed_origins:
            raise ValueError()
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer '):raise ValueError()
        actor=host.sandbox.access.host_authority.authenticate(auth[7:]);host.require(actor)
    except (ValueError,ReviewError,CalendarError):raise HTTPException(403,'TEST_ONLY_CALENDAR_UNAUTHORIZED') from None
    return host,actor


async def invoke(fn,*args):
    try:
        result=await run_in_threadpool(fn,*args)
        if hasattr(result,'model_dump'):result=result.model_dump(mode='json')
        if isinstance(result,tuple):result=[r.model_dump(mode='json') for r in result]
        return _redact(result)
    except CalendarError as exc:raise HTTPException(exc.status_code,exc.code) from None
    except RuntimeError:
        # The durable SUBMIT record must be reconciled; never claim failure before
        # or after a simulated interruption establishes the actual local state.
        raise HTTPException(409,'EXECUTION_INTERRUPTED_RECONCILE_REQUIRED') from None
    except (OSError,ValueError):raise HTTPException(409,'LOCAL_JOURNAL_UNAVAILABLE_OR_INVALID') from None


class FixtureRequest(ContractModel):fixture_id:str=Field(min_length=1,max_length=80)
class ApprovalRequest(ContractModel):
    preview_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    snapshot_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    input_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    target_sandbox_id:str=Field(max_length=200)
    operation_keys:tuple[str,...]=Field(min_length=1,max_length=100)
    owner_confirmation:Literal[True]
class ApprovalReference(ContractModel):approval_id:UUID
class ExecuteRequest(ApprovalReference):
    replay_only:bool=False
    failure_scenario:Literal['NONE','FAIL_BEFORE','TIMEOUT_BEFORE','TIMEOUT_AFTER','CRASH_AFTER_SUBMIT','TIMEOUT_AFTER_MISMATCH','PARTIAL_FAILURE']='NONE'


@router.get('/session')
async def session(request:Request):
    host,actor=bridge(request);return await invoke(host.session,actor)
@router.post('/session/end')
async def end(request:Request):
    host,actor=bridge(request);await invoke(host.sandbox.access.host_authority.revoke,actor);return {'revoked':True}
@router.post('/preview')
async def preview(request:Request):
    host,actor=bridge(request);body=await _body(request,FixtureRequest);return await invoke(host.preview,body.fixture_id,actor)
@router.post('/approve')
async def approve(request:Request):
    host,actor=bridge(request);body=await _body(request,ApprovalRequest);return await invoke(host.approve,body,actor)
@router.post('/execute')
async def execute(request:Request):
    host,actor=bridge(request);body=await _body(request,ExecuteRequest);return {'outcomes':await invoke(host.execute,body.approval_id,actor,body.failure_scenario,body.replay_only),'external_calendar':False}
@router.post('/reconcile')
async def reconcile(request:Request):
    host,actor=bridge(request);body=await _body(request,ApprovalReference);return {'outcomes':await invoke(host.reconcile,body.approval_id,actor),'external_calendar':False}
@router.post('/revoke-approval')
async def revoke(request:Request):
    host,actor=bridge(request);body=await _body(request,ApprovalReference);return await invoke(host.revoke,body.approval_id,actor)
@router.get('/history')
async def history(request:Request):
    host,actor=bridge(request);return await invoke(host.history,actor)
@router.get('/state')
async def state(request:Request):
    host,actor=bridge(request);return await invoke(host.sandbox.snapshot,actor)
