"""Invented schedules only; no human, Gold, ACCEPT or calendar approvals."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
import unittest
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.analysis.schedule_checks import ScheduleChecks,CheckStart,InventorySubmission,VerificationSubmission,CheckAnalysis
from backend.analysis.student import student_analyze
from backend.analysis.api import router as analysis_router
from backend.review.api import router as review_router,ReviewBridge
from backend.review.auth import TrustedHostAuthority,utc_now
from backend.review.persistence import ReviewJournal
from backend.review.models import ReviewError
from test_m7_analysis import make,attest,value

class SyntheticSnapshotStore:
    # Deliberately synthetic revision repository for source-check behavior tests.
    def __init__(self,request):
        self._lock=RLock();self.authority=TrustedHostAuthority(test_only_source_bindings={(request.packet.source.source_document_id,request.packet.source.content_sha256)});self.packet=request.packet;self.correction_authors=[]
    def snapshot(self,task,principal):
        self.authority.require_source(principal,self.packet.source)
        return {'packet':self.packet.model_dump(mode='json'),'tasks':[{'task_id':'synthetic-task','state':'PENDING_REVIEW'}],'dependency_digest':'synthetic'}
    def history(self,task,principal):return {'corrections':[{'actor_id':a} for a in self.correction_authors]}

class ScheduleCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='stair-fr7-synthetic-');self.addCleanup(self.temp.cleanup)
        self.request=make();self.store=SyntheticSnapshotStore(self.request);s=self.request.packet.source
        self.bindings={(s.source_document_id,s.content_sha256)}
        self.primary,self.token=self.store.authority.issue('SYNTHETIC-PRIMARY',{'read','oversee','correct'},source_bindings=self.bindings,mode='TEST_ONLY')
        self.independent,self.itoken=self.store.authority.issue('SYNTHETIC-INDEPENDENT',{'read','oversee'},source_bindings=self.bindings,mode='TEST_ONLY')
        self.manifest={'source_document_id':s.source_document_id,'content_sha256':s.content_sha256,'page_count':1}
        self.journal=lambda:ReviewJournal(Path(self.temp.name)/'checks','SYNTHETIC-FR7-ONLY')
        self.service=ScheduleChecks(self.store,self.journal,lambda source:self.manifest)
        self.service.enrol_independent(self.independent,independent_of=(self.primary.actor_id,),expires_at=utc_now()+timedelta(minutes=5))
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
    def submission(self,**updates):
        event=self.store.packet.events[0];ref=next(o.evidence_ids[0] for o in event.revision.observations if o.evidence_ids)
        values={'expected_version':self.check['version'],'checked_pages':[1],
            'rows':[{'decision':'INCLUDE','candidate_id':event.candidate_id,'page':1,'source_session':'Synthetic first source session','evidence_ids':[ref]}],
            'source_confirmed':True,'scope_confirmed':True,'note':'SYNTHETIC behavior check, no real human verification.'}
        values.update(updates);return InventorySubmission.model_validate(values)
    def primary_check(self,**updates):
        self.check=self.service.submit(self.check['check_id'],self.submission(**updates),self.primary);return self.check
    def verified(self):
        self.primary_check()
        body=VerificationSubmission(expected_version=self.check['version'],source_confirmed=True,inventory_confirmed=True,temporal_context_confirmed=True,note='Invented independent fixture behavior only.')
        self.check=self.service.verify(self.check['check_id'],body,self.independent);return self.check
    def test_primary_self_confirmation_not_complete(self):
        self.assertEqual(self.primary_check()['status'],'AWAITING_INDEPENDENT_REVIEW')
        self.assertFalse(self.service.analyze(self.check['check_id'],CheckAnalysis(),self.primary)['completeness']['trusted_complete'])
    def test_owner_cannot_install_self_as_independent(self):
        with self.assertRaises(ReviewError):self.service.enrol_independent(self.primary,independent_of=(self.primary.actor_id,),expires_at=utc_now()+timedelta(minutes=1))
    def test_owner_cannot_verify(self):
        self.primary_check()
        with self.assertRaises(ReviewError):self.service.verify(self.check['check_id'],VerificationSubmission(expected_version=2,source_confirmed=True,inventory_confirmed=True,temporal_context_confirmed=True,note='Synthetic'),self.primary)
    def test_missing_session_blocks(self):
        body=self.submission();rows=list(body.rows)+[dict(decision='OMITTED',page=1,source_session='Uncaptured class',note='Needs extraction')]
        self.assertEqual(self.primary_check(rows=rows)['status'],'INCOMPLETE')
    def test_missing_blank_pdf_page_blocks(self):
        self.manifest['page_count']=2
        # A changed physical manifest invalidates the earlier draft.
        with self.assertRaises(ReviewError):self.service.get(self.check['check_id'],self.primary)
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
        self.assertIn('ALL_SOURCE_PAGES_MUST_BE_CHECKED',self.primary_check()['blockers'])
    def test_duplicate_session_mapping_blocks(self):
        rows=list(self.submission().rows)*2
        self.assertIn('SOURCE_SESSION_BIJECTION_REQUIRED',self.primary_check(rows=rows)['blockers'])
    def test_unknown_evidence_rejected(self):
        row=self.submission().rows[0].model_copy(update={'evidence_ids':('not-owned',)})
        with self.assertRaises(ReviewError):self.primary_check(rows=[row])
    def test_wrong_class_evidence_rejected(self):
        from backend.models.evidence import EvidenceSpan
        self.store.packet=self.store.packet.model_copy(update={'evidence':self.store.packet.evidence+(EvidenceSpan(evidence_id='wrong-class',source_document_id=self.store.packet.source.source_document_id,route='text',page=1,raw_text='Another class'),)})
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
        row=self.submission().rows[0].model_copy(update={'evidence_ids':('wrong-class',)})
        with self.assertRaises(ReviewError):self.primary_check(rows=[row])
    def test_wrong_source_rejected(self):
        other,_=self.store.authority.issue('SYNTHETIC-OTHER',{'read','oversee'},source_bindings={('another','0'*64)})
        with self.assertRaises(ReviewError):self.service.get(self.check['check_id'],other)
    def test_stale_primary_version_rejected(self):
        old=self.submission();self.primary_check()
        with self.assertRaises(ReviewError):self.service.submit(self.check['check_id'],old,self.primary)
    def test_changed_revision_invalidates_check(self):
        self.primary_check();event=self.store.packet.events[0]
        new=uuid4()
        self.store.packet=self.store.packet.model_copy(update={'events':(event.model_copy(update={'revision':event.revision.model_copy(update={'revision_id':new}),'validation':event.validation.model_copy(update={'revision_id':new})}),)})
        with self.assertRaises(ReviewError):self.service.analyze(self.check['check_id'],CheckAnalysis(),self.primary)
    def test_correction_author_cannot_independently_verify(self):
        self.primary_check();self.store.correction_authors=[self.independent.actor_id]
        with self.assertRaises(ReviewError):self.service.verify(self.check['check_id'],VerificationSubmission(expected_version=2,source_confirmed=True,inventory_confirmed=True,temporal_context_confirmed=True,note='Synthetic'),self.independent)
    def test_synthetic_independent_check_and_available_window(self):
        self.verified();result=self.service.analyze(self.check['check_id'],CheckAnalysis(availability=[{'start':'2026-01-05T08:00','end':'2026-01-05T12:00'}]),self.primary)
        self.assertTrue(result['completeness']['trusted_complete']);self.assertTrue(value(result,'verified_available_windows'))
        self.assertEqual(result['verified_workload']['class_contact_hours'],1)
        self.assertFalse(result['calendar_operations']);self.assertFalse(result['external_calls'])
    def test_no_feasible_window(self):
        self.verified();result=self.service.analyze(self.check['check_id'],CheckAnalysis(availability=[{'start':'2026-01-05T09:00','end':'2026-01-05T10:00'}]),self.primary)
        self.assertEqual(value(result,'verified_available_windows'),[])
    def test_revoked_independent_access_disables_windows(self):
        self.verified();self.store.authority.revoke(self.independent)
        self.assertFalse(self.service.analyze(self.check['check_id'],CheckAnalysis(),self.primary)['completeness']['trusted_complete'])
    def test_restart_preserves_inventory_but_not_live_authority(self):
        self.verified();fresh=ScheduleChecks(self.store,self.journal,lambda source:self.manifest)
        result=fresh.get(self.check['check_id'],self.primary)
        self.assertEqual(result['status'],'INDEPENDENTLY_CHECKED_REAUTH_REQUIRED')
        self.assertTrue(result['verification']['recorded_at']);self.assertTrue(result['primary']['rows'])
        self.assertFalse(fresh.analyze(self.check['check_id'],CheckAnalysis(),self.primary)['completeness']['trusted_complete'])
    def test_snapshot_unresolved_fields_block(self):
        from backend.models.event import FieldState
        e=self.store.packet.events[0];obs=tuple(o.model_copy(update={'state':FieldState.UNRESOLVED,'raw_value':'TBA','evidence_ids':(self.store.packet.evidence[0].evidence_id,)}) if o.field_name=='location' else o for o in e.revision.observations)
        self.store.packet=self.store.packet.model_copy(update={'events':(e.model_copy(update={'revision':e.revision.model_copy(update={'observations':obs})}),)})
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
        self.assertIn('UNRESOLVED_EVENT_FIELDS',self.primary_check()['blockers'])
    def test_journal_initialization_is_shared_by_concurrent_requests(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import Mock
        factory=Mock(wraps=self.journal)
        service=ScheduleChecks(self.store,factory,lambda source:self.manifest)
        with ThreadPoolExecutor(max_workers=4) as pool:
            journals=list(pool.map(lambda _:service._transaction(),range(8)))
        self.assertEqual(factory.call_count,1)
        self.assertTrue(all(journal is journals[0] for journal in journals))
    def test_processing_issue_cannot_be_checked_away(self):
        from backend.models.evidence import ParseIssue
        self.store.packet=self.store.packet.model_copy(update={'issues':(ParseIssue(code='SYNTHETIC_UNREADABLE_REGION'),)})
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
        self.assertIn('SOURCE_PROCESSING_ISSUES_REQUIRE_ADJUDICATION',self.primary_check()['blockers'])
    def test_latest_saved_inventory_is_chronological(self):
        later=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
        self.assertEqual(self.service.latest('synthetic-task',self.primary)['check_id'],later['check_id'])
    def test_incomplete_scope_never_gets_windows_from_availability(self):
        self.primary_check()
        result=self.service.analyze(self.check['check_id'],CheckAnalysis(availability=[{'start':'2026-01-05T08:00','end':'2026-01-05T12:00'}]),self.primary)
        self.assertIsNone(value(result,'verified_available_windows'));self.assertFalse(result['recommendations'])
    def test_unknown_timezone_prevents_independent_completion(self):
        event=self.store.packet.events[0]
        content=event.revision.content.model_copy(update={'source_timezone':None})
        obs=tuple(o.model_copy(update={'state':__import__('backend.models.event',fromlist=['FieldState']).FieldState.MISSING,'raw_value':None,'evidence_ids':()}) if o.field_name=='source_timezone' else o for o in event.revision.observations)
        self.store.packet=self.store.packet.model_copy(update={'events':(event.model_copy(update={'revision':event.revision.model_copy(update={'content':content,'observations':obs})}),)})
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.request.scope),self.primary)
        self.primary_check()
        with self.assertRaises(ReviewError):self.service.verify(self.check['check_id'],VerificationSubmission(expected_version=2,source_confirmed=True,inventory_confirmed=True,temporal_context_confirmed=True,note='Synthetic timezone cannot be invented'),self.independent)
    def test_protected_api_requires_host(self):
        app=FastAPI();app.include_router(review_router)
        with TestClient(app) as client:self.assertEqual(client.post('/api/review/schedule-checks/start',json={}).status_code,403)
    def test_protected_api_source_check(self):
        app=FastAPI();app.include_router(review_router);app.state.m5_review_bridge=ReviewBridge(self.store);app.state.m7_schedule_checks=self.service
        with TestClient(app,client=('127.0.0.1',40000)) as client:
            r=client.get('/api/review/schedule-checks/'+self.check['check_id'],headers={'Authorization':'Bearer '+self.token})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(client.post('/api/review/schedule-checks/'+self.check['check_id']+'/verify',headers={'Authorization':'Bearer '+self.token},json={'expected_version':1,'source_confirmed':True,'inventory_confirmed':True,'temporal_context_confirmed':True,'note':'Synthetic'}).status_code,403)

class StudentArithmeticTests(unittest.TestCase):
    def test_printed_hours_without_timezone_are_not_free_time(self):
        result=student_analyze(make([{'source_timezone':None}]))
        self.assertEqual(result['student_facts']['printed_class_hours'],1)
        self.assertIsNone(value(result,'verified_available_windows'))
    def test_contact_hours_and_occupancy_are_distinct(self):
        q,authority,_=attest(make([{'end_time':'11:00'},{'start_time':'10:00','end_time':'12:00'}]));r=student_analyze(q,authority)
        self.assertEqual(r['verified_workload']['class_contact_hours'],4)
        self.assertEqual(value(r,'observed_scheduled_hours'),3)
        self.assertEqual(len(value(r,'confirmed_conflicts')),1)
    def test_unknown_weeks_do_not_expand(self):
        result=student_analyze(make([{'date':None,'teaching_weeks':[1,2]}]))
        self.assertFalse(result['completeness']['trusted_complete']);self.assertIsNone(value(result,'verified_available_windows'))
    def test_bad_weekly_recurrence_does_not_enable_windows(self):
        q=make([{'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-18'}}]);r=student_analyze(q)
        self.assertIsNone(value(r,'verified_available_windows'))
    def test_anonymous_student_endpoint_drops_attestation(self):
        q,authority,_=attest(make());app=FastAPI();app.include_router(analysis_router);app.state.m7_completeness_authority=authority
        body=q.model_copy(update={'profile':'m7-student-v1'}).model_dump(mode='json')
        with TestClient(app) as client:
            r=client.post('/api/analysis/v1/student',json=body);self.assertEqual(r.status_code,200,r.text)
            self.assertFalse(r.json()['completeness']['trusted_complete'])

class OriginalSourceManifestTests(unittest.TestCase):
    def test_counts_original_pdf_blank_pages(self):
        from io import BytesIO
        from hashlib import sha256
        from reportlab.pdfgen.canvas import Canvas
        from backend.review.local_host import LocalReviewHost
        from backend.review.store import ReviewStore
        raw=BytesIO();canvas=Canvas(raw);canvas.drawString(30,700,'SYNTHETIC timetable page');canvas.showPage();canvas.showPage();canvas.save()
        with TemporaryDirectory(prefix='stair-fr7-pages-') as temporary:
            path=Path(temporary)/'synthetic-two-pages.pdf';path.write_bytes(raw.getvalue())
            binding=('synthetic-manifest',sha256(raw.getvalue()).hexdigest())
            auth=TrustedHostAuthority(test_only_source_bindings={binding});host=LocalReviewHost(ReviewStore(auth),'SYNTHETIC-OWNER',identity=lambda:'SYNTHETIC-OWNER',grant_mode='TEST_ONLY')
            _,pending=host.request_access({'source_document_id':binding[0],'content_sha256':binding[1]})
            host.authorize_from_console(pending['nonce'],path)
            self.assertEqual(host.source_manifests[binding]['page_count'],2)
    def test_review_snapshot_keeps_source_processing_issues(self):
        from dataclasses import replace
        from test_m5_policy import fixture
        from backend.models.evidence import ParseIssue
        from backend.models.event import CanonicalTimetable
        from backend.review.store import ReviewStore
        request,_=fixture();auth=TrustedHostAuthority();principal,_=auth.issue('SYNTHETIC-OWNER',{'read','queue','oversee'})
        store=ReviewStore(auth);task=store.ingest(request,principal)[0]
        run=store._runs[store.get(task,principal).run_id]
        source=CanonicalTimetable.model_validate_json(run.snapshot.source_json)
        issue=ParseIssue(code='SYNTHETIC_UNREADABLE_REGION',candidate_id=None)
        run.snapshot=replace(run.snapshot,source_json=source.model_copy(update={'issues':(issue,)}).model_dump_json())
        snapshot=store.snapshot(task,principal)
        self.assertEqual(snapshot['packet']['issues'][0]['code'],'SYNTHETIC_UNREADABLE_REGION')
        CanonicalTimetable.model_validate(snapshot['packet'])


class SourcePagePreviewTests(unittest.TestCase):
    def setUp(self):
        from io import BytesIO
        from hashlib import sha256
        from reportlab.pdfgen.canvas import Canvas
        raw=BytesIO();canvas=Canvas(raw,pagesize=(500,250));canvas.drawString(20,100,'SYNTHETIC whole source, not OCR replacement');canvas.showPage();canvas.showPage();canvas.save();self.raw=raw.getvalue()
        q=make();source=q.packet.source.model_copy(update={'content_sha256':sha256(self.raw).hexdigest(),'media_type':'application/pdf','content_hash_basis':'original_bytes'})
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'source':source})});self.store=SyntheticSnapshotStore(q)
        self.principal,_=self.store.authority.issue('SYNTHETIC-OWNER',{'read','oversee','correct'},source_bindings={(source.source_document_id,source.content_sha256)},mode='TEST_ONLY')
        self.temp=TemporaryDirectory(prefix='stair-fr7-preview-');self.addCleanup(self.temp.cleanup)
        manifest={'source_document_id':source.source_document_id,'content_sha256':source.content_sha256,'page_count':2}
        self.service=ScheduleChecks(self.store,lambda:ReviewJournal(Path(self.temp.name)/'checks','SYNTHETIC'),lambda s:manifest)
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=q.scope),self.principal)
    def test_original_page_and_blank_page_render_without_ocr(self):
        import base64
        from io import BytesIO
        from PIL import Image
        result=self.service.source_page(self.check['check_id'],1,self.raw,self.principal)
        first=base64.b64decode(result['png_base64']);second=base64.b64decode(self.service.source_page(self.check['check_id'],2,self.raw,self.principal)['png_base64'])
        self.assertTrue(first.startswith(b'\x89PNG'));self.assertNotEqual(first,second)
        image=Image.open(BytesIO(first));self.assertLessEqual(max(image.size),2000);self.assertEqual(result['page_count'],2)
    def test_changed_original_bytes_rejected(self):
        with self.assertRaises(ReviewError):self.service.source_page(self.check['check_id'],1,self.raw+b'changed',self.principal)
    def test_nonexistent_original_page_rejected(self):
        with self.assertRaises(ReviewError):self.service.source_page(self.check['check_id'],3,self.raw,self.principal)
    def test_revoked_authorization_cannot_render(self):
        self.store.authority.revoke(self.principal)
        with self.assertRaises(ReviewError):self.service.source_page(self.check['check_id'],1,self.raw,self.principal)

    def test_authenticated_binary_preview_transport(self):
        app=FastAPI();app.include_router(review_router);app.state.m5_review_bridge=ReviewBridge(self.store);app.state.m7_schedule_checks=self.service
        _,token=self.store.authority.issue('SYNTHETIC-BROWSER',{'read','oversee'},source_bindings={(self.store.packet.source.source_document_id,self.store.packet.source.content_sha256)},mode='TEST_ONLY')
        with TestClient(app,client=('127.0.0.1',40000)) as client:
            result=client.post('/api/review/schedule-checks/'+self.check['check_id']+'/source-page/1',content=self.raw,headers={'Authorization':'Bearer '+token,'Content-Type':'application/pdf'})
            self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['png_base64'])
    def test_local_host_assets_do_not_mix_cached_versions(self):
        from backend.review.local_host import LocalReviewHost,create_host_app,ORIGIN
        host=LocalReviewHost(self.store,'SYNTHETIC-OWNER',identity=lambda:'SYNTHETIC-OWNER')
        with TestClient(create_host_app(host),base_url=ORIGIN,client=('127.0.0.1',40000)) as client:
            result=client.get('/js/review.js')
            self.assertEqual(result.status_code,200);self.assertEqual(result.headers['cache-control'],'no-store')
            self.assertIn('body instanceof global.Blob',result.text)
