"""Synthetic host grants and corrections, never real human/gold actions."""
from dataclasses import replace
from datetime import timedelta
import json
from uuid import uuid4
import unittest

from test_m5_policy import fixture
from backend.models.event import FieldState
from backend.review.auth import TrustedHostAuthority, utc_now
from backend.review.store import ReviewStore
from backend.review.models import CorrectionSubmission, FieldCorrection, HumanEvidenceInput, ReviewError, ReviewState
from backend.reconstruction.models import digest


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.auth=TrustedHostAuthority()
        self.admin,self.admin_token=self.auth.issue('synthetic-host-admin',{'read','queue','assign','correct','revalidate','resolve','oversee'})
        self.reviewer,self.reviewer_token=self.auth.issue('synthetic-reviewer',{'read','correct','revalidate','resolve'})
        self.other,_=self.auth.issue('synthetic-other',{'read','correct','revalidate','resolve'})
        self.store=ReviewStore(self.auth)
        self.request,self.run=fixture()
        self.ids=self.store.ingest(self.request,self.admin)
        self.id=self.ids[0]

    def begin(self,task_id=None):
        task_id=task_id or self.id
        v=self.store.get(task_id,self.admin)
        v=self.store.assign(task_id,self.reviewer.actor_id,v.etag,self.admin)
        return self.store.start(task_id,v.etag,self.reviewer)

    def proposal(self,task=None,*,field='location',raw='A2',value='A2',state=FieldState.PRESENT,with_human=True):
        task=task or self.store.get(self.id,self.reviewer)
        source_id=self.store.evidence(task.task_id,self.reviewer)[0].evidence_id
        evidence=(HumanEvidenceInput(evidence_id='human:'+uuid4().hex,raw_text=raw or 'Synthetic source absence assertion',
                                    source_evidence_ids=(source_id,),page=1,note='Synthetic reviewer-supplied annotation'),) if with_human else ()
        refs=(evidence[0].evidence_id,) if evidence else (source_id,)
        return CorrectionSubmission(request_id=uuid4(),expected_revision_id=task.current_event.revision.revision_id,
            expected_etag=task.etag,reason='Synthetic evidence-based correction',human_evidence=evidence,
            fields=(FieldCorrection(field_name=field,state=state,value=value,raw_value=raw,evidence_ids=refs),))

    def test_queue_creation_and_summary(self):
        v=self.store.get(self.id,self.admin)
        self.assertEqual(v.state,ReviewState.PENDING)
        self.assertEqual(self.store.queue_summary(self.admin)['total'],1)
        self.assertEqual(v.purpose,'APPLICATION_REPAIR_NOT_GOLD')

    def test_assignment_start(self):
        v=self.begin()
        self.assertEqual(v.state,ReviewState.IN_PROGRESS)
        self.assertEqual(v.assignee,'synthetic-reviewer')

    def test_unassigned_access_denied(self):
        with self.assertRaises(ReviewError): self.store.get(self.id,self.reviewer)

    def test_assignment_requires_host_authorized_actor(self):
        v=self.store.get(self.id,self.admin)
        with self.assertRaises(ReviewError): self.store.assign(self.id,'forged-actor',v.etag,self.admin)

    def test_forged_principal_rejected(self):
        with self.assertRaises(ReviewError): self.store.get(self.id,replace(self.admin,actor_id='forged-actor'))
        with self.assertRaises(ReviewError): self.store.get(self.id,{'actor_id':self.admin.actor_id})

    def test_revoked_grant_rejected(self):
        self.auth.revoke(self.admin)
        with self.assertRaises(ReviewError): self.store.get(self.id,self.admin)

    def test_expired_grant_rejected(self):
        self.auth.clock=lambda:utc_now()+timedelta(hours=2)
        with self.assertRaises(ReviewError): self.store.get(self.id,self.admin)

    def test_other_reviewer_cannot_mutate(self):
        v=self.begin()
        with self.assertRaises(ReviewError): self.store.submit(self.id,self.proposal(v),self.other)

    def test_read_only_grant_cannot_correct(self):
        v=self.begin(); reader,_=self.auth.issue(self.reviewer.actor_id,{'read'})
        with self.assertRaises(ReviewError): self.store.submit(self.id,self.proposal(v),reader)

    def test_valid_correction_new_revision_and_revalidation_required(self):
        v=self.begin(); original=v.current_event
        result=self.store.submit(self.id,self.proposal(v),self.reviewer)
        updated=result['task']
        self.assertTrue(result['applied'])
        self.assertEqual(updated.state,ReviewState.REVALIDATION_REQUIRED)
        self.assertEqual(updated.current_event.revision.version,2)
        self.assertEqual(updated.current_event.revision.previous_revision_id,original.revision.revision_id)
        self.assertEqual(updated.current_event.candidate_id,original.candidate_id)
        self.assertEqual(updated.current_event.identity.event_id,original.identity.event_id)
        self.assertEqual(updated.current_event.validation.status.value,'NOT_ASSESSED')
        self.assertIn('REVALIDATION_REQUIRED',updated.decision.reason_codes)

    def test_correction_revalidated_without_automatic_acceptance(self):
        v=self.begin(); v=self.store.submit(self.id,self.proposal(v),self.reviewer)['task']
        v=self.store.revalidate(self.id,v.etag,self.reviewer)
        self.assertEqual(v.state,ReviewState.REVALIDATED)
        self.assertEqual(v.current_event.revision.content.location,'A2')
        self.assertNotEqual(v.current_event.validation.status.value,'NOT_ASSESSED')
        self.assertEqual(v.decision.decision.value,'ABSTAIN')
        self.assertEqual(v.decision.score_provenance,'M4_SCORE_NOT_REUSED_AFTER_CORRECTION')
        self.assertIsNone(v.current_event.policy.reliability_rank)

    def test_resolve_revalidated_task_not_gold(self):
        v=self.begin(); v=self.store.submit(self.id,self.proposal(v),self.reviewer)['task']
        v=self.store.revalidate(self.id,v.etag,self.reviewer)
        v=self.store.finish(self.id,v.etag,self.reviewer,action='resolve',reason='Synthetic application review complete')
        self.assertEqual(v.state,ReviewState.RESOLVED)
        self.assertEqual(v.current_event.human_review.status,'APPROVED')
        self.assertEqual(v.current_event.human_verification.status,'NOT_VERIFIED')
        self.assertEqual(v.current_event.calendar.approval_status,'NOT_APPROVED')
        self.assertEqual(v.current_event.calendar.sync_status,'NOT_SUBMITTED')
        self.assertFalse(v.decision.schedule_verified)

    def test_no_resolution_without_validation(self):
        v=self.begin()
        with self.assertRaises(ReviewError): self.store.finish(self.id,v.etag,self.reviewer,action='resolve',reason='Synthetic')

    def test_invalid_correction_preserved_not_applied(self):
        v=self.begin(); original=v.current_event
        result=self.store.submit(self.id,self.proposal(v,field='start_time',raw='25:99',value='25:99'),self.reviewer)
        self.assertFalse(result['applied'])
        self.assertEqual(result['task'].state,ReviewState.ESCALATED)
        self.assertEqual(result['task'].current_event.revision,original.revision)
        self.assertEqual(result['task'].current_event.identity,original.identity)
        history=self.store.history(self.id,self.reviewer)
        self.assertEqual(history['corrections'][0]['status'],'INVALID')
        self.assertEqual(len(history['revisions']),1)

    def test_unsupported_value_rejected(self):
        v=self.begin()
        result=self.store.submit(self.id,self.proposal(v,raw='A2',value='A2',with_human=False),self.reviewer)
        self.assertEqual(result['error_code'],'UNSUPPORTED_CORRECTION_VALUE')

    def test_broken_evidence_reference_rejected(self):
        v=self.begin(); p=self.proposal(v)
        p=p.model_copy(update={'fields':(p.fields[0].model_copy(update={'evidence_ids':('missing',)}),)})
        self.assertEqual(self.store.submit(self.id,p,self.reviewer)['error_code'],'BROKEN_CORRECTION_EVIDENCE')

    def test_stale_etag_and_revision(self):
        v=self.begin(); p=self.proposal(v)
        with self.assertRaises(ReviewError): self.store.submit(self.id,p.model_copy(update={'expected_etag':v.etag-1}),self.reviewer)
        with self.assertRaises(ReviewError): self.store.submit(self.id,p.model_copy(update={'expected_revision_id':uuid4()}),self.reviewer)

    def test_replay_rejected_even_with_updated_etag(self):
        v=self.begin(); p=self.proposal(v); result=self.store.submit(self.id,p,self.reviewer)
        now=result['task']; p=p.model_copy(update={'expected_etag':now.etag,'expected_revision_id':now.current_event.revision.revision_id})
        with self.assertRaises(ReviewError) as exc: self.store.submit(self.id,p,self.reviewer)
        self.assertEqual(exc.exception.code,'CORRECTION_REPLAY')

    def test_immutable_original_and_detached_history(self):
        original=self.store.original_reconstruction(self.id,self.admin)
        v=self.begin(); self.store.submit(self.id,self.proposal(v),self.reviewer)
        history=self.store.history(self.id,self.reviewer)
        self.assertEqual(history['revisions'][0]['revision']['content']['location'],'A1')
        history['revisions'][0]['revision']['content']['location']='MUTATED'
        self.assertEqual(self.store.history(self.id,self.reviewer)['revisions'][0]['revision']['content']['location'],'A1')
        self.assertEqual(self.store.original_reconstruction(self.id,self.admin),original)
        self.assertEqual(json.loads(original)['canonical_digest'],self.request.reconstruction['canonical_digest'])

    def test_audit_hash_chain_and_before_after_values(self):
        v=self.begin(); self.store.submit(self.id,self.proposal(v),self.reviewer)
        history=self.store.history(self.id,self.reviewer)
        previous=None
        for item in history['audit']:
            self.assertEqual(item['previous_hash'],previous)
            self.assertEqual(item['entry_hash'],digest({k:v for k,v in item.items() if k!='entry_hash'}))
            previous=item['entry_hash']
        correction=history['corrections'][0]
        self.assertEqual(json.loads(correction['before_json'])['revision']['content']['location'],'A1')
        self.assertEqual(json.loads(correction['after_json'])['revision']['content']['location'],'A2')

    def test_hard_invalid_human_time_not_accepted(self):
        v=self.begin(); v=self.store.submit(self.id,self.proposal(v,field='start_time',raw='12:00',value='12:00'),self.reviewer)['task']
        v=self.store.revalidate(self.id,v.etag,self.reviewer)
        self.assertEqual(v.state,ReviewState.ESCALATED)
        self.assertEqual(v.current_event.validation.status.value,'FAILED')
        self.assertEqual(v.decision.decision.value,'ABSTAIN')

    def test_source_absence_requires_justification(self):
        v=self.begin(); p=self.proposal(v,raw=None,value=None,state=FieldState.SOURCE_ABSENT)
        self.assertFalse(self.store.submit(self.id,p,self.reviewer)['applied'])

    def test_unknown_correction_keeps_unresolved_state(self):
        v=self.begin(); p=self.proposal(v,raw='TBA',value=None,state=FieldState.UNRESOLVED)
        v=self.store.submit(self.id,p,self.reviewer)['task']; v=self.store.revalidate(self.id,v.etag,self.reviewer)
        self.assertIsNone(v.current_event.revision.content.location)
        self.assertIn('SOURCE_AMBIGUITY',v.decision.reason_codes)

    def test_close_abstained_with_reason(self):
        v=self.store.get(self.id,self.admin)
        v=self.store.finish(self.id,v.etag,self.admin,action='close',reason='Synthetic insufficient source')
        self.assertEqual(v.state,ReviewState.CLOSED)
        with self.assertRaises(ReviewError): self.store.finish(self.id,v.etag,self.admin,action='resolve',reason='Synthetic')

    def test_duplicate_ingestion_rejected(self):
        with self.assertRaises(ReviewError): self.store.ingest(self.request,self.admin)

    def test_original_evidence_coordinates_and_null_human_confidence(self):
        v=self.begin(); v=self.store.submit(self.id,self.proposal(v),self.reviewer)['task']
        evidence=self.store.evidence(self.id,self.reviewer)
        self.assertEqual(evidence[0].bbox,self.run.graph().evidence[0].bbox)
        self.assertIsNone(evidence[-1].confidence)
        obs=next(o for o in v.current_event.revision.observations if o.field_name=='location')
        self.assertEqual(obs.origin,'human')
        self.assertIsNone(obs.confidence)

    def test_sibling_correction_invalidates_resolved_assessment(self):
        request,_=fixture('CS101 Monday 09:00-11:00','CS102 Tuesday 12:00-13:00')
        ids=self.store.ingest(request,self.admin)
        first=self.begin(ids[0]); first=self.store.submit(ids[0],self.proposal(first),self.reviewer)['task']
        first=self.store.revalidate(ids[0],first.etag,self.reviewer)
        # Unknown cohort relationship prevents resolution; revalidated assessments are still invalidated.
        second=self.begin(ids[1]); self.store.submit(ids[1],self.proposal(second),self.reviewer)
        first=self.store.get(ids[0],self.reviewer)
        self.assertEqual(first.state,ReviewState.REVALIDATION_REQUIRED)
        self.assertIn('REVALIDATION_REQUIRED',first.decision.reason_codes)
        self.assertIsNone(first.validation_dependency_digest)

    def test_schema_prevents_actor_and_status_injection(self):
        from pydantic import ValidationError
        v=self.begin(); p=self.proposal(v).model_dump(mode='json'); p['actor_id']='forged-actor'
        with self.assertRaises(ValidationError): CorrectionSubmission.model_validate(p)

    def test_supported_source_backed_fields(self):
        cases=(('course_code','CS202','CS202'),('course_name','Synthetic Computing','Synthetic Computing'),
               ('session_type','Lab','Practical'),('day_of_week','Friday',5),('date','2026-01-01','2026-01-01'),
               ('teaching_weeks','1-3',[1,2,3]),('section','T2','T2'))
        for field,raw,value in cases:
            with self.subTest(field=field):
                store=ReviewStore(self.auth); task_id=store.ingest(self.request,self.admin)[0]
                v=store.get(task_id,self.admin); v=store.assign(task_id,self.reviewer.actor_id,v.etag,self.admin)
                v=store.start(task_id,v.etag,self.reviewer)
                original=store.evidence(task_id,self.reviewer)[0].evidence_id
                p=CorrectionSubmission(request_id=uuid4(),expected_etag=v.etag,expected_revision_id=v.current_event.revision.revision_id,
                    reason='Synthetic field coverage',human_evidence=(HumanEvidenceInput(evidence_id='human:field',raw_text=raw,
                    source_evidence_ids=(original,),page=1,note='Synthetic assertion'),),
                    fields=(FieldCorrection(field_name=field,state='PRESENT',value=value,raw_value=raw,evidence_ids=('human:field',)),))
                result=store.submit(task_id,p,self.reviewer)
                self.assertTrue(result['applied'])
                v=store.revalidate(task_id,result['task'].etag,self.reviewer)
                self.assertEqual(v.current_event.revision.content.model_dump(mode='json')[field],value)

    def test_relationship_correction_is_new_revision(self):
        from backend.reconstruction.models import Context,EvidenceLink
        v=self.begin(); ref=self.store.evidence(self.id,self.reviewer)[0].evidence_id
        relationships=Context(links=(EvidenceLink(source_evidence_id=ref,target_evidence_id=ref,kind='shared_context',reason_evidence_ids=(ref,)),))
        p=CorrectionSubmission(request_id=uuid4(),expected_revision_id=v.current_event.revision.revision_id,expected_etag=v.etag,
            reason='Synthetic relationship annotation',fields=(),relationships=relationships)
        result=self.store.submit(self.id,p,self.reviewer)
        self.assertTrue(result['applied'])
        self.assertEqual(result['task'].current_event.revision.version,2)
        self.assertEqual(result['task'].current_event.revision.content,v.current_event.revision.content)
        v=self.store.revalidate(self.id,result['task'].etag,self.reviewer)
        self.assertEqual(v.state,ReviewState.REVALIDATED)

    def test_broken_relationship_reference_retained_invalid(self):
        from backend.reconstruction.models import Context,EvidenceLink
        v=self.begin(); ref=self.store.evidence(self.id,self.reviewer)[0].evidence_id
        relationships=Context(links=(EvidenceLink(source_evidence_id='unknown',target_evidence_id=ref,kind='shared_context',reason_evidence_ids=(ref,)),))
        p=CorrectionSubmission(request_id=uuid4(),expected_revision_id=v.current_event.revision.revision_id,expected_etag=v.etag,
            reason='Synthetic invalid relationship',fields=(),relationships=relationships)
        self.assertFalse(self.store.submit(self.id,p,self.reviewer)['applied'])

    def test_partial_skeleton_can_be_repaired_without_gold(self):
        request,_=fixture('CS101 Monday Lecture Time: TBA')
        task_id=self.store.ingest(request,self.admin)[0]
        v=self.begin(task_id); ref=self.store.evidence(task_id,self.reviewer)[0].evidence_id
        human=HumanEvidenceInput(evidence_id='human:times',raw_text='09:00-11:00',source_evidence_ids=(ref,),page=1,note='Synthetic source reading')
        fields=(FieldCorrection(field_name='course_code',state='PRESENT',value='CS101',raw_value='CS101',evidence_ids=(ref,)),
                FieldCorrection(field_name='day_of_week',state='PRESENT',value=1,raw_value='Monday',evidence_ids=(ref,)),
                FieldCorrection(field_name='start_time',state='PRESENT',value='09:00',raw_value='09:00',evidence_ids=('human:times',)),
                FieldCorrection(field_name='end_time',state='PRESENT',value='11:00',raw_value='11:00',evidence_ids=('human:times',)))
        p=CorrectionSubmission(request_id=uuid4(),expected_etag=v.etag,expected_revision_id=v.current_event.revision.revision_id,
            reason='Synthetic manual repair, not registered bounded research REVIEW',fields=fields,human_evidence=(human,))
        result=self.store.submit(task_id,p,self.reviewer)
        self.assertTrue(result['applied'])
        v=self.store.revalidate(task_id,result['task'].etag,self.reviewer)
        self.assertTrue(v.current_event.revision.core_fields_present)
        self.assertEqual(v.decision.decision.value,'ABSTAIN')
        self.assertEqual(v.current_event.human_verification.status,'NOT_VERIFIED')

    def test_state_change_and_valid_source_absence_assertion(self):
        v=self.begin(); p=self.proposal(v,raw=None,value=None,state=FieldState.SOURCE_ABSENT)
        change=p.fields[0].model_copy(update={'justification':'Synthetic reviewer confirms source does not supply a location'})
        p=p.model_copy(update={'fields':(change,)})
        result=self.store.submit(self.id,p,self.reviewer)
        self.assertTrue(result['applied'])
        record=self.store.history(self.id,self.reviewer)['corrections'][0]
        before=json.loads(record['before_json']); after=json.loads(record['after_json'])
        self.assertEqual(next(o['state'] for o in before['revision']['observations'] if o['field_name']=='location'),'PRESENT')
        self.assertEqual(next(o['state'] for o in after['revision']['observations'] if o['field_name']=='location'),'SOURCE_ABSENT')

    def test_invalid_replay_does_not_create_duplicate_record(self):
        v=self.begin(); p=self.proposal(v,with_human=False)
        result=self.store.submit(self.id,p,self.reviewer)
        updated=result['task']; p=p.model_copy(update={'expected_etag':updated.etag})
        with self.assertRaises(ReviewError): self.store.submit(self.id,p,self.reviewer)
        self.assertEqual(len(self.store.history(self.id,self.reviewer)['corrections']),1)

    def test_timetable_revalidation_catches_human_introduced_overlap(self):
        request,_=fixture('CS101 Monday 09:00-10:00 Cohort C1 Weeks 1-3','CS102 Monday 10:00-11:00 Cohort C1 Weeks 1-3')
        ids=self.store.ingest(request,self.admin)
        task_id=next(i for i in ids if self.store.get(i,self.admin).current_event.revision.content.course_code=='CS102')
        v=self.begin(task_id)
        p=self.proposal(v,field='start_time',raw='09:30',value='09:30')
        result=self.store.submit(task_id,p,self.reviewer)
        self.assertTrue(result['applied'])
        v=self.store.revalidate(task_id,result['task'].etag,self.reviewer)
        self.assertEqual(v.state,ReviewState.ESCALATED)
        self.assertTrue(any(f.rule_id=='C01' and f.passed is False for f in v.decision.findings))

    def test_decision_view_matches_m1_policy_record(self):
        v=self.begin(); self.assertEqual(v.current_event.policy.decision,v.decision.decision)
        v=self.store.submit(self.id,self.proposal(v),self.reviewer)['task']
        self.assertEqual(v.current_event.policy.decision,v.decision.decision)
        self.assertEqual(v.current_event.policy.reason_codes,v.decision.reason_codes)

    def test_cannot_set_calendar_or_gold_field(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError): FieldCorrection(field_name='human_verification',state='PRESENT',value='GOLD',raw_value='GOLD',evidence_ids=('x',))
