"""Actual isolated TestClient records from an invented native PDF, never a live host."""
from base64 import b64encode
from io import BytesIO
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from backend.review.api import router as review_router,ReviewBridge
from backend.calendar.api import router as calendar_router
from backend.review.auth import TrustedHostAuthority
from backend.review.store import ReviewStore
from backend.review.binding import AssessmentInput
from backend.services.file_extractor import extract_document
from backend.services.extraction_adapter import adapt_extraction
from backend.reconstruction.service import reconstruct


def workflow_fixture():
    raw=BytesIO();canvas=Canvas(raw,pagesize=(1100,240));canvas.setFont('Helvetica',16)
    canvas.drawString(20,150,'CS101 Monday 2033-02-07 09:00-11:00 Lecture Room A101 Group G1 [source amendment: A102]')
    canvas.save();pdf=raw.getvalue()
    packet=adapt_extraction(extract_document('S3B-SYNTHETIC.pdf','application/pdf',pdf))
    reconstruction=reconstruct(packet,'constrained')
    source=packet.source;binding=(source.source_document_id,source.content_sha256)
    authority=TrustedHostAuthority(test_only_source_bindings={binding})
    actor,token=authority.issue('SYNTHETIC-S3B-FRONTEND',{'read','queue','assign','correct','revalidate','resolve','oversee'},
        mode='TEST_ONLY',source_bindings={binding},lifetime_minutes=5)
    store=ReviewStore(authority);application=FastAPI();application.include_router(review_router);application.include_router(calendar_router)
    application.state.m5_review_bridge=ReviewBridge(store)
    request=AssessmentInput(source_packet=packet,reconstruction=reconstruction)
    ident=store.ingest(request,actor)[0];headers={'Authorization':'Bearer '+token}
    with TestClient(application,client=('127.0.0.1',43000)) as client:
        def get(path):
            r=client.get(path,headers=headers);assert r.status_code==200,r.text;return r.json()
        def post(path,body):
            r=client.post(path,json=body,headers=headers);assert r.status_code==200,r.text;return r.json()
        def snapshot():return get(f'/api/review/tasks/{ident}/snapshot')
        def preview(s):return post('/api/calendar/v1/preview',{'bundle':{'packet':s['packet'],'context':{},'assessments':[],'reconstruction_status':'NOT_PROVIDED'}})
        session=get('/api/review/session');queue=get('/api/review/queue');pending=get(f'/api/review/tasks/{ident}')
        public_policy=post('/api/policy/assess',request.model_dump(mode='json'))
        assigned=post(f'/api/review/tasks/{ident}/transition',{'expected_etag':pending['task']['etag'],'action':'claim','reason':'Explicit synthetic fixture assignment'})
        assigned_response=get(f'/api/review/tasks/{ident}');assigned_snapshot=snapshot();assigned_preview=preview(assigned_snapshot)
        started=post(f'/api/review/tasks/{ident}/transition',{'expected_etag':assigned['etag'],'action':'start','reason':'Explicit synthetic fixture review'})
        started_snapshot=snapshot();started_preview=preview(started_snapshot)
        evidence=next(e for e in started_snapshot['packet']['evidence'] if 'A102' in e['raw_text'])
        body={'request_id':str(uuid4()),'expected_revision_id':started['current_event']['revision']['revision_id'],'expected_etag':started['etag'],
            'reason':'Read the printed synthetic source amendment','fields':[{'field_name':'location','state':'PRESENT','raw_value':'A102','value':'A102','evidence_ids':[evidence['evidence_id']]}]}
        submitted=post(f'/api/review/tasks/{ident}/corrections',body);submitted_snapshot=snapshot();submitted_preview=preview(submitted_snapshot)
        submitted_history=get(f'/api/review/tasks/{ident}/history')
        revalidated=post(f'/api/review/tasks/{ident}/revalidate',{'expected_etag':submitted['etag']})
        final_snapshot=snapshot();final_preview=preview(final_snapshot);history=get(f'/api/review/tasks/{ident}/history')
        initial_bundle={'packet':reconstruction['canonical_output']['packet'],'context':{},'assessments':[],
            'reconstruction_status':reconstruction['canonical_output']['status']}
        initial_eligibility=post('/api/calendar/v1/eligibility',initial_bundle)
        closed=post(f'/api/review/tasks/{ident}/transition',{'expected_etag':revalidated['etag'],
            'action':'close','reason':'Synthetic task closure preserves abstention and unverified status'})
    # Bearer token and live authority are deliberately absent from serialized fixtures.
    return {'kind':'ISOLATED_SYNTHETIC_TESTCLIENT_RECORDS_NOT_LIVE_ENROLLMENT','id':ident,'session':session,'queue':queue,
        'assigned':assigned_response,'started':started,'submitted':submitted,'revalidated':revalidated,'closed':closed,
        'snapshots':[assigned_snapshot,started_snapshot,submitted_snapshot,final_snapshot],
        'previews':[assigned_preview,started_preview,submitted_preview,final_preview],
        'histories':[submitted_history,history],'source_packet':packet.model_dump(mode='json'),
        'reconstruction':reconstruction,'reconstruction_json':__import__('json').dumps(reconstruction),
        'pdf_base64':b64encode(pdf).decode(),'correction_body':body,'public_policy':public_policy,'eligibility':initial_eligibility}
