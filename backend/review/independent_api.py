"""Dedicated reviewer surface: read/check only, never ordinary owner corrections."""
from fastapi import APIRouter,Request,HTTPException
from backend.review.api import _body,_invoke,_redact
from backend.review.models import ReviewError
from backend.analysis.schedule_checks import VerificationSubmission,CheckAnalysis
router=APIRouter()

def access(request):
    service=getattr(request.app.state,'independent_access',None)
    record=getattr(request.state,'independent_record',None)
    if service is None or record is None or service.channels.get(record.cookie_hash) is not record:raise HTTPException(403,'INDEPENDENT_REVIEW_AUTHENTICATION_REQUIRED')
    try:service.host.store.authority.require(record.principal,'read')
    except ReviewError as error:raise HTTPException(error.status_code,error.code) from None
    return service,record

@router.get('/api/independent-review/current')
async def current(request:Request):
    service,record=access(request)
    return _redact(await _invoke(service.current,record))

@router.get('/api/independent-review/source/{page}')
async def source(page:int,request:Request):
    service,record=access(request)
    return await _invoke(service.source,record,page)

@router.post('/api/independent-review/verify')
async def verify(request:Request):
    service,record=access(request)
    return await _invoke(service.request_verification,record,await _body(request,VerificationSubmission))

@router.post('/api/independent-review/analyze')
async def analyze(request:Request):
    service,record=access(request)
    return _redact(await _invoke(service.checks.analyze,record.invitation.check_id,await _body(request,CheckAnalysis),record.principal))

@router.post('/api/independent-review/end')
async def end(request:Request):
    service,record=access(request)
    await _invoke(service.end,record)
    return {'ended':True}
