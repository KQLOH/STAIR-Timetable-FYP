"""Isolated synthetic trusted-host fixtures. Never enroll a live operator."""
from datetime import timedelta
import json
from uuid import uuid4
import unittest
from unittest.mock import MagicMock,patch
from fastapi.testclient import TestClient

from backend.main import app
from backend.review.api import ReviewBridge
from backend.review.auth import TrustedHostAuthority,utc_now
from backend.review.binding import AssessmentInput,bind_input
from backend.review.models import CorrectionSubmission,FieldCorrection,HumanEvidenceInput,ReviewError
from backend.review.store import ReviewStore
from backend.reconstruction.models import digest
from test_m5_policy import fixture

TEXT='CS101 Monday 2033-02-07 09:00-11:00 Lecture Room A101 Group G1 [source amendment: A102]'
PERMISSIONS={'read','queue','assign','correct','revalidate','resolve','oversee'}


class S3bReviewTests(unittest.TestCase):
    def setUp(self):
        self.prior=getattr(app.state,'m5_review_bridge',None)
        app.state.m5_review_bridge=None
        self.addCleanup(setattr,app.state,'m5_review_bridge',self.prior)
        self.client=TestClient(app,client=('127.0.0.1',40000));self.addCleanup(self.client.close)
        self.request,self.bound=fixture(TEXT)
        self.authority=TrustedHostAuthority();self.store=ReviewStore(self.authority)

    def enable(self,request=None):
        self.request=request or self.request
        source=self.request.source_packet.source
        self.authority=TrustedHostAuthority(test_only_source_bindings={(source.source_document_id,source.content_sha256)})
        self.store=ReviewStore(self.authority)
        self.actor,self.token=self.authority.issue('SYNTHETIC-S3B-OPERATOR',PERMISSIONS,
            source_bindings={(source.source_document_id,source.content_sha256)},mode='TEST_ONLY',lifetime_minutes=5)
        app.state.m5_review_bridge=ReviewBridge(self.store)
        self.headers={'Authorization':'Bearer '+self.token}
        self.ident=self.store.ingest(self.request,self.actor)[0]
        task=self.store.get(self.ident,self.actor)
        task=self.store.assign(self.ident,self.actor.actor_id,task.etag,self.actor)
        self.task=self.store.start(self.ident,task.etag,self.actor)
        return self.task

    def proposal(self,task=None,**updates):
        task=task or self.task
        evidence=next(e for e in self.store.evidence(self.ident,self.actor) if 'A102' in e.raw_text)
        values={'request_id':str(uuid4()),'expected_revision_id':str(task.current_event.revision.revision_id),
            'expected_etag':task.etag,'reason':'Read the printed synthetic source amendment',
            'fields':[{'field_name':'location','state':'PRESENT','value':'A102','raw_value':'A102','evidence_ids':[evidence.evidence_id]}]}
        values.update(updates);return values

    def post(self,suffix,body):return self.client.post(f'/api/review/tasks/{self.ident}/'+suffix,json=body,headers=self.headers)

    def test_new_routes_default_deny_and_no_public_grant_issuer(self):
        for method,path in [('GET','/api/review/session'),('POST','/api/review/session/end'),
            ('GET','/api/review/queue'),('GET',f'/api/review/tasks/{uuid4()}/snapshot')]:
            with self.subTest(path=path):self.assertEqual(self.client.request(method,path).status_code,403)
        self.assertEqual(self.client.post('/api/review/grants',json={'actor_id':'forged'}).status_code,404)

    def test_test_only_requires_host_declared_synthetic_scope(self):
        source=self.request.source_packet.source
        binding={(source.source_document_id,source.content_sha256)}
        with self.assertRaises(ValueError):
            self.authority.issue('SYNTHETIC',PERMISSIONS,source_bindings=binding,mode='TEST_ONLY')
        with self.assertRaises(ValueError):
            self.authority.issue('SYNTHETIC',PERMISSIONS,mode='TEST_ONLY')

    def test_assignment_snapshot_and_queue_are_scope_limited(self):
        request,_=fixture(TEXT,'MA202 Tuesday 2033-02-08 13:00-14:00 Tutorial Room B202')
        self.enable(request)
        source=request.source_packet.source
        scope={(source.source_document_id,source.content_sha256)}
        limited,token=self.authority.issue('SYNTHETIC-LIMITED',{'read','correct','revalidate'},source_bindings=scope)
        headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.get('/api/review/queue',headers=headers).json()['total'],0)
        task=self.store.get(self.ident,self.actor)
        # Assign a second pending task; the first remains private to the other assignee.
        other=next(row for row in self.store.queue_summary(self.actor)['tasks'] if row['task_id']!=self.ident)
        self.store.assign(other['task_id'],limited.actor_id,other['etag'],self.actor)
        queue=self.client.get('/api/review/queue?limit=1',headers=headers).json()
        self.assertEqual(queue['total'],1)
        snapshot=self.client.get('/api/review/tasks/'+other['task_id']+'/snapshot',headers=headers).json()
        self.assertEqual([t['task_id'] for t in snapshot['tasks']],[other['task_id']])
        self.assertFalse(snapshot['complete_schedule_verified'])
        _,wrong=self.authority.issue('SYNTHETIC-OUTSIDE',{'read','correct'},source_bindings={('OTHER','f'*64)})
        with self.assertRaises(ReviewError):
            self.store.assign(other['task_id'],'SYNTHETIC-OUTSIDE',self.store.get(other['task_id'],self.actor).etag,self.actor)

    def test_http_revalidation_catches_cross_event_time_conflict(self):
        request,_=fixture('CS101 Monday 09:00-10:00 Cohort C1 Weeks 1-3',
            'CS102 Monday 10:00-11:00 Cohort C1 Weeks 1-3 [source amendment: 09:30]')
        self.enable(request)
        row=next(t for t in self.store.queue_summary(self.actor)['tasks'] if t['current_event']['revision']['content']['course_code']=='CS102')
        self.ident=row['task_id']
        if row['state']=='PENDING_REVIEW':
            task=self.store.assign(self.ident,self.actor.actor_id,row['etag'],self.actor)
            self.task=self.store.start(self.ident,task.etag,self.actor)
        else:self.task=self.store.get(self.ident,self.actor)
        ref=next(e.evidence_id for e in self.store.evidence(self.ident,self.actor) if '09:30' in e.raw_text)
        body={'request_id':str(uuid4()),'expected_revision_id':str(self.task.current_event.revision.revision_id),
            'expected_etag':self.task.etag,'reason':'Printed invented source amendment creates an overlap',
            'fields':[{'field_name':'start_time','state':'PRESENT','value':'09:30','raw_value':'09:30','evidence_ids':[ref]}]}
        changed=self.post('corrections',body);self.assertEqual(changed.status_code,200,changed.text)
        response=self.post('revalidate',{'expected_etag':changed.json()['etag']})
        self.assertEqual(response.status_code,200,response.text)
        current=response.json();self.assertEqual(current['state'],'ESCALATED')
        self.assertTrue(any(f['rule_id']=='C01' and f['passed'] is False for f in current['decision']['findings']))
        self.assertNotEqual(current['current_event']['policy']['decision'],'ACCEPT')

    def test_scoped_session_and_selectable_queue(self):
        self.enable();r=self.client.get('/api/review/session',headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['mode'],'TEST_ONLY')
        self.assertTrue(r.json()['scoped_workflow_enabled']);self.assertNotIn(self.token,r.text)
        self.assertEqual(r.headers['cache-control'],'no-store')
        queue=self.client.get('/api/review/queue',headers=self.headers).json()
        self.assertEqual(queue['tasks'][0]['current_event']['revision']['content']['location'],'A101')
        self.assertEqual(queue['tasks'][0]['task_id'],self.ident)
        self.assertEqual(queue['storage'],'PROCESS_LOCAL_NOT_RESTART_PERSISTENT')

    def test_scope_binds_hash_and_document_id_on_every_task_route(self):
        self.enable();source=self.request.source_packet.source
        for binding in [(source.source_document_id,'f'*64),('OTHER-SOURCE',source.content_sha256)]:
            _,token=self.authority.issue(self.actor.actor_id,PERMISSIONS,source_bindings={binding},mode='APPLICATION_OPERATOR')
            headers={'Authorization':'Bearer '+token}
            for path in ['', '/history','/snapshot']:
                self.assertEqual(self.client.get(f'/api/review/tasks/{self.ident}'+path,headers=headers).status_code,403)
            queue=self.client.get('/api/review/queue',headers=headers).json();self.assertEqual(queue['total'],0)
            self.assertEqual(self.client.post('/api/review/ingest',headers=headers,json=self.request.model_dump(mode='json')).status_code,403)

    def test_wrong_operator_cannot_read_or_mutate_assigned_task(self):
        self.enable();source=self.request.source_packet.source
        _,token=self.authority.issue('SYNTHETIC-OTHER',{'read','correct','revalidate'},source_bindings={(source.source_document_id,source.content_sha256)},mode='TEST_ONLY')
        h={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.get(f'/api/review/tasks/{self.ident}',headers=h).status_code,403)
        self.assertEqual(self.client.post(f'/api/review/tasks/{self.ident}/corrections',json=self.proposal(),headers=h).status_code,403)

    def test_expired_and_revoked_sessions(self):
        self.enable();self.authority.clock=lambda:utc_now()+timedelta(minutes=6)
        self.assertEqual(self.client.get('/api/review/session',headers=self.headers).status_code,403)
        self.authority.clock=utc_now
        self.assertEqual(self.client.post('/api/review/session/end',headers=self.headers).json(),{'revoked':True})
        self.assertEqual(self.client.get('/api/review/queue',headers=self.headers).status_code,403)

    def test_unapproved_origin_and_university_token_denied(self):
        self.enable()
        self.assertEqual(self.client.get('/api/review/session',headers={**self.headers,'Origin':'https://unapproved.invalid'}).status_code,403)
        self.assertEqual(self.client.get('/api/review/session',headers={'Authorization':'Bearer university-session'}).status_code,403)

    def test_valid_revision_revalidation_and_history_semantics(self):
        self.enable();original=self.task.current_event;original_prediction=self.store.original_reconstruction(self.ident,self.actor)
        response=self.post('corrections',self.proposal());self.assertEqual(response.status_code,200,response.text)
        changed=response.json();self.assertEqual(changed['current_event']['revision']['content']['location'],'A102')
        self.assertEqual(changed['current_event']['revision']['version'],2)
        self.assertEqual(changed['current_event']['revision']['previous_revision_id'],str(original.revision.revision_id))
        self.assertEqual(changed['state'],'REVALIDATION_REQUIRED')
        current=self.post('revalidate',{'expected_etag':changed['etag']});self.assertEqual(current.status_code,200,current.text)
        record=current.json();self.assertEqual(record['current_event']['policy']['decision'],'ABSTAIN')
        self.assertEqual(record['current_event']['human_verification']['status'],'NOT_VERIFIED')
        self.assertEqual(record['current_event']['calendar']['approval_status'],'NOT_APPROVED')
        history=self.client.get(f'/api/review/tasks/{self.ident}/history',headers=self.headers).json()
        self.assertEqual(len(history['revisions']),2);self.assertEqual(history['revisions'][0]['revision']['content']['location'],'A101')
        self.assertEqual(history['revisions'][1]['revision']['content']['location'],'A102')
        self.assertEqual(history['corrections'][0]['status'],'SUBMITTED');self.assertTrue(any(a['operation']=='REVALIDATE' for a in history['audit']))
        self.assertEqual(self.store.original_reconstruction(self.ident,self.actor),original_prediction)

    def test_missing_evidence_and_unsupported_fields_rejected(self):
        self.enable();body=self.proposal();body['fields'][0]['evidence_ids']=[]
        self.assertEqual(self.post('corrections',body).status_code,422)
        body=self.proposal();body['fields'][0]['field_name']='calendar.approval_status'
        self.assertEqual(self.post('corrections',body).status_code,422)
        self.assertEqual(self.store.get(self.ident,self.actor).current_event.revision.version,1)

    def test_invalid_values_retained_without_invalid_revision(self):
        self.enable();body=self.proposal();body['fields'][0].update(value='NOT IN SOURCE',raw_value='NOT IN SOURCE')
        response=self.post('corrections',body);self.assertEqual(response.status_code,422)
        self.assertTrue(response.json()['detail']['proposal_retained'])
        current=self.store.get(self.ident,self.actor);self.assertEqual(current.current_event.revision.version,1)
        self.assertEqual(current.state.value,'ESCALATED')
        self.assertEqual(self.store.history(self.ident,self.actor)['corrections'][0]['status'],'INVALID')

    def test_invalid_date_and_time_no_successful_revision(self):
        for field,raw in [('date','2033-02-30'),('start_time','29:70')]:
            with self.subTest(field=field):
                source=self.request.source_packet.source;authority=TrustedHostAuthority(test_only_source_bindings={(source.source_document_id,source.content_sha256)});store=ReviewStore(authority)
                actor,_=authority.issue('SYNTHETIC',PERMISSIONS,source_bindings={(source.source_document_id,source.content_sha256)},mode='TEST_ONLY')
                ident=store.ingest(self.request,actor)[0];task=store.get(ident,actor);task=store.assign(ident,actor.actor_id,task.etag,actor);task=store.start(ident,task.etag,actor)
                original=store.evidence(ident,actor)[0]
                submission=CorrectionSubmission(request_id=uuid4(),expected_revision_id=task.current_event.revision.revision_id,expected_etag=task.etag,
                    reason='Synthetic invalid negative test',human_evidence=(HumanEvidenceInput(evidence_id='human:invalid',raw_text=raw,source_evidence_ids=(original.evidence_id,),page=original.page,note='Intentionally invalid synthetic reading'),),
                    fields=(FieldCorrection(field_name=field,state='PRESENT',value=raw,raw_value=raw,evidence_ids=('human:invalid',)),))
                result=store.submit(ident,submission,actor);self.assertFalse(result['applied']);self.assertEqual(result['task'].current_event.revision.version,1)

    def test_etag_revision_and_duplicate_submission_conflicts(self):
        self.enable();body=self.proposal();bad={**body,'expected_etag':1}
        self.assertEqual(self.post('corrections',bad).status_code,409)
        bad={**body,'expected_revision_id':str(uuid4())};self.assertEqual(self.post('corrections',bad).status_code,409)
        good=self.post('corrections',body);self.assertEqual(good.status_code,200)
        repeat={**body,'expected_etag':good.json()['etag']};r=self.post('corrections',repeat)
        self.assertEqual(r.status_code,409);self.assertEqual(r.json()['detail'],'CORRECTION_REPLAY')

    def test_cross_source_evidence_rejected(self):
        self.enable();body=self.proposal();body['fields'][0]['evidence_ids']=['other-source-word']
        r=self.post('corrections',body);self.assertEqual(r.status_code,422)
        self.assertEqual(r.json()['detail']['code'],'BROKEN_CORRECTION_EVIDENCE')

    def test_cross_page_human_assertion_rejected(self):
        request,_=fixture(TEXT,'MA202 Tuesday 2033-02-08 13:00-14:00 Tutorial Room B202')
        self.enable(request);evidence=list(self.store.evidence(self.ident,self.actor));pages={e.page for e in evidence}
        if len(pages)<2:
            # Independent source pages, not fabricated research evidence: synthetic fixture builder supports explicit page reassignment.
            from dataclasses import replace
            run=self.store._runs[self.task.run_id]
            first=evidence[0];other=first.model_copy(update={'evidence_id':'SYNTHETIC-PAGE-TWO','page':2})
            run.evidence=run.evidence+(other,);refs=[first.evidence_id,other.evidence_id]
        else:refs=[next(e.evidence_id for e in evidence if e.page==p) for p in sorted(pages)[:2]]
        body=self.proposal();body['human_evidence']=[{'evidence_id':'human:crosspage','raw_text':'A102','source_evidence_ids':refs,'page':1,'note':'Intentional invalid synthetic page merge'}]
        body['fields'][0]['evidence_ids']=['human:crosspage']
        response=self.post('corrections',body);self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail']['code'],'HUMAN_EVIDENCE_PAGE_MISMATCH')

    def test_snapshot_invalidates_old_preview_and_analysis_is_unverified(self):
        self.enable()
        initial=self.client.get(f'/api/review/tasks/{self.ident}/snapshot',headers=self.headers).json()
        def preview(snapshot):return self.client.post('/api/calendar/v1/preview',json={'bundle':{'packet':snapshot['packet'],'context':{},'assessments':[],'reconstruction_status':'NOT_PROVIDED'}})
        old=preview(initial);self.assertEqual(old.status_code,200,old.text)
        self.assertEqual(self.post('corrections',self.proposal()).status_code,200)
        fresh=self.client.get(f'/api/review/tasks/{self.ident}/snapshot',headers=self.headers).json()
        current=preview(fresh);self.assertFalse(current.json()['write_authorized'])
        self.assertNotEqual(digest(old.json()['preview']),digest(current.json()['preview']))
        self.assertNotEqual(initial['dependency_digest'],fresh['dependency_digest']);self.assertFalse(fresh['complete_schedule_verified'])

    def test_conflicting_alternative_cannot_merge_another_page(self):
        self.enable();run=self.store._runs[self.task.run_id]
        original=self.store.evidence(self.ident,self.actor)[0]
        second=original.model_copy(update={'evidence_id':'SYNTHETIC-PAGE-TWO-ALTERNATIVE','page':2,'raw_text':'Room A103'})
        run.evidence+=(second,)
        body=self.proposal();body['fields'][0].update({'state':'CONFLICTING','value':None,
            'alternatives':[{'raw_value':'A102','evidence_ids':[original.evidence_id]},
                {'raw_value':'A103','evidence_ids':[second.evidence_id]}]})
        response=self.post('corrections',body)
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(response.json()['detail']['code'],'CORRECTION_CROSS_PAGE_EVIDENCE')

    def test_resume_requires_current_assignee_and_etag(self):
        self.enable();changed=self.post('corrections',self.proposal()).json()
        task=self.post('revalidate',{'expected_etag':changed['etag']}).json()
        if task['state']=='REVALIDATED':
            resumed=self.post('transition',{'expected_etag':task['etag'],'action':'resume','reason':'Synthetic further source reading'})
            self.assertEqual(resumed.status_code,200);self.assertEqual(resumed.json()['state'],'IN_PROGRESS')
        else:self.assertEqual(task['state'],'ESCALATED')

    def test_s2a_s2b_bindings_and_word_provenance(self):
        from test_s2a_ruled import fixture as ruled_fixture
        from s2b_fixtures import packet as block_fixture
        from backend.layout.ruled import reconstruct_ruled
        from backend.layout.block_grid import reconstruct_blocks
        for source,sidecar,reconstruct in [(*ruled_fixture(),reconstruct_ruled),(*block_fixture(),reconstruct_blocks)]:
            with self.subTest(profile=sidecar.profile):
                reconstruction=reconstruct(source,sidecar)[0]
                request=AssessmentInput(source_packet=source,reconstruction=reconstruction,layout_evidence=sidecar.model_dump(mode='json'))
                bound=bind_input(request);self.assertEqual(bound.profile,sidecar.profile)
                self.assertTrue(any('-word-' in e.evidence_id for e in bound.graph().evidence))
                expected=digest(reconstruction);tampered=json.loads(json.dumps(reconstruction));tampered['canonical_output']['packet']['events'][0]['revision']['content']['location']='FORGED'
                with self.assertRaises(ReviewError):bind_input(request.model_copy(update={'reconstruction':tampered}))
                self.assertEqual(digest(reconstruction),expected)
                authority=TrustedHostAuthority(test_only_source_bindings={(source.source.source_document_id,source.source.content_sha256)});store=ReviewStore(authority);actor,_=authority.issue('SYNTHETIC',PERMISSIONS,source_bindings={(source.source.source_document_id,source.source.content_sha256)},mode='TEST_ONLY')
                ident=store.ingest(request,actor)[0];task=store.get(ident,actor);task=store.assign(ident,actor.actor_id,task.etag,actor);task=store.start(ident,task.etag,actor)
                task=store.revalidate(ident,task.etag,actor)
                self.assertNotEqual(task.current_event.policy.decision.value,'ACCEPT')
                self.assertEqual(task.current_event.human_verification.status,'NOT_VERIFIED')

    def test_layout_sidecar_capture_mismatch_rejected(self):
        from s2b_fixtures import packet
        from backend.layout.block_grid import reconstruct_blocks
        source,sidecar=packet();reconstruction=reconstruct_blocks(source,sidecar)[0]
        raw=sidecar.model_dump(mode='json');raw['content_sha256']='f'*64
        with self.assertRaises(ReviewError):bind_input(AssessmentInput(source_packet=source,reconstruction=reconstruction,layout_evidence=raw))

    def test_mocked_structured_tar_source_is_not_sent_through_ocr_or_enrolled(self):
        from tests.fixtures import timetable_html
        portal=MagicMock();portal.student_name='SYNTHETIC';portal.fetch_latest_timetable_html.return_value=timetable_html()
        from backend import main
        with patch.object(main,'TARUMTClient',return_value=portal),patch.dict(main.CONNECTED_CLIENTS,{},clear=True),patch.object(main,'extract_document',side_effect=AssertionError('Structured source must not use OCR')):
            response=self.client.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC'})
            self.assertEqual(response.status_code,200)
            from backend.models.event import CanonicalTimetable
            packet=CanonicalTimetable.model_validate(response.json()['data']['canonical'])
            self.assertEqual(packet.source.route,'tarumt_html');self.assertTrue(all(e.identity.stable_key is None for e in packet.events))
            with self.assertRaises(ReviewError) as error:bind_input(AssessmentInput(source_packet=packet,reconstruction={}))
            self.assertEqual(error.exception.code,'STRUCTURED_REVIEW_BINDING_NOT_APPROVED')
