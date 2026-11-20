"""Read-only M6 interfaces. Approval and execute functions have no HTTP route."""
from dataclasses import dataclass
from typing import Literal
from uuid import UUID
from ipaddress import ip_address
from fastapi import APIRouter,Request,HTTPException
from pydantic import Field, model_serializer

from backend.models.evidence import ContractModel
from backend.calendar.models import CalendarInput,Snapshot,CalendarError
from backend.calendar.eligibility import assess
from backend.calendar.planning import build_preview
from backend.review.api import _body,_redact
from backend.review.models import ReviewError, RegisteredAssessmentRef

router=APIRouter(prefix='/api/calendar/v1')


class RoutingDiagnostic(ContractModel):
    """Current routing trace only, never a registered eligibility assessment."""
    candidate_id:str=Field(min_length=1,max_length=200)
    event_id:UUID
    revision_id:UUID
    revision_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    route:Literal['REVIEW','ABSTAIN']
    policy_profile:Literal['m5-development-v1','m5-source-review-v1']
    registered_accept_eligible:Literal[False]=False


class CalendarDiagnosticInput(CalendarInput):
    routing_diagnostics:tuple[RoutingDiagnostic,...]=Field(default=(),max_length=100)
    registered_assessment_refs:tuple[RegisteredAssessmentRef,...]=Field(default=(),max_length=100)

    @model_serializer(mode='wrap')
    def diagnostic_record(self,handler):
        value=handler(self)
        if not self.routing_diagnostics:value.pop('routing_diagnostics',None)
        if not self.registered_assessment_refs:value.pop('registered_assessment_refs',None)
        return value


class PreviewInput(ContractModel):
    bundle:CalendarDiagnosticInput
    snapshot:Snapshot|None=None


def _registered_bundle(bundle,request):
    registry=getattr(request.app.state,'m5_operating_registry',None)
    refs=bundle.registered_assessment_refs
    if refs:
        if registry is None:raise HTTPException(409,'REGISTERED_ASSESSMENT_UNAVAILABLE')
        try:records=tuple(registry.resolve(ref) for ref in refs)
        except ValueError:raise HTTPException(409,'REGISTERED_ASSESSMENT_STALE_OR_INVALID') from None
        bundle=bundle.model_copy(update={'assessments':bundle.assessments+records})
    return bundle,registry


@router.post('/eligibility')
async def eligibility(request:Request):
    bundle=await _body(request,CalendarDiagnosticInput)
    bundle,registry=_registered_bundle(bundle,request)
    return {'policy_status':'BLOCKED','external_calendar':False,'events':[
        assess(bundle,event,registry=registry).model_dump(mode='json',exclude={'payload'}) for event in bundle.packet.events]}


@router.post('/preview')
async def preview(request:Request):
    payload=await _body(request,PreviewInput)
    snapshot=payload.snapshot or Snapshot(sandbox_id='diagnostic:no-authorized-target',scope='DEVELOPMENT_BLOCKED',calendar_version=0)
    bundle,registry=_registered_bundle(payload.bundle,request)
    plan=build_preview(bundle,snapshot,registry=registry)
    return _redact({'snapshot_trust':'CALLER_SUPPLIED_DIAGNOSTIC_ONLY','write_authorized':False,'preview':plan.model_dump(mode='json')})


@dataclass(frozen=True)
class CalendarReadBridge:
    sandbox:object


def _host(request):
    bridge=getattr(request.app.state,'m6_calendar_bridge',None)
    if not isinstance(bridge,CalendarReadBridge): raise HTTPException(403,'CALENDAR_HOST_NOT_CONFIGURED')
    try:
        if not request.client or not ip_address(request.client.host).is_loopback: raise ValueError('Nonlocal')
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer '): raise CalendarError('CALENDAR_UNAUTHORIZED',403)
        actor=bridge.sandbox.access.host_authority.authenticate(auth[7:])
        bridge.sandbox.access.require(actor,bridge.sandbox.sandbox_id,'read')
    except (ReviewError,CalendarError,ValueError): raise HTTPException(403,'CALENDAR_UNAUTHORIZED') from None
    return bridge.sandbox,actor


@router.get('/sandbox')
async def sandbox_state(request:Request):
    store,actor=_host(request)
    try: return _redact(store.snapshot(actor).model_dump(mode='json'))
    except CalendarError as exc: raise HTTPException(exc.status_code,exc.code) from None


@router.get('/history')
@router.get('/reconciliation-diagnostics')
async def history(request:Request):
    store,actor=_host(request)
    try: return _redact(store.diagnostics(actor))
    except CalendarError as exc: raise HTTPException(exc.status_code,exc.code) from None


# Separate protected application interface. Existing public/legacy URLs retain
# their read-only behavior and 404 mutation boundaries.
from backend.calendar.workflow_api import router as test_only_router
router.include_router(test_only_router)
