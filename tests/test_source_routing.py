"""FR5 generated behavior tests. No calibration, human Gold or accuracy claims."""
from dataclasses import replace
import json,os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.main import app
from backend.review.binding import bind_input, AssessmentInput
from backend.review.policy import assess_run, assess_event
from backend.review.source_routing import PROFILE, report, assess_source
from backend.reconstruction.models import development_settings
from test_m5_policy import fixture

GOOD='CS101 Monday 2033-02-07 09:00-10:00 Lecture Room: A101'

def decisions(*texts,**kwargs):
    request,_=fixture(*(texts or (GOOD,)),**kwargs)
    request=request.model_copy(update={'policy_profile':PROFILE})
    run=bind_input(request)
    return request,run,assess_run(run)

class SourceRoutingTests(unittest.TestCase):
    def test_legacy_policy_is_preserved_but_source_profile_reviews_clear_candidate(self):
        request,legacy=fixture(GOOD)
        self.assertEqual(assess_run(legacy)[0].decision.value,'ABSTAIN')
        request,run,records=decisions(GOOD);d=records[0]
        self.assertEqual(d.decision.value,'REVIEW');self.assertEqual(d.source_routing.support,'SOURCE_SUPPORTED')
        self.assertFalse(d.registered_routing_eligible);self.assertIsNone(d.calibrated_probability)
        self.assertNotEqual(run.input_digest,legacy.input_digest)
        self.assertEqual(run.reconstruction_digest,legacy.reconstruction_digest)
    def test_missing_core_identity_or_time_abstains(self):
        for text in ('CS404 Wednesday Time: TBA','CS101 Monday 09:00-?'):
            with self.subTest(text=text):
                _,_,records=decisions(text)
                self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
    def test_optional_venue_unknown_requests_human_review(self):
        _,_,records=decisions('CS101 Monday 2033-02-07 09:00-10:00 Lecture Room: TBA')
        self.assertEqual(records[0].decision.value,'REVIEW')
        self.assertIn('location',records[0].source_routing.fields_to_check)
    def test_missing_calendar_date_is_explicit_review_not_calendar_eligibility(self):
        _,_,records=decisions('CS101 Monday 09:00-10:00 Lecture Room A101')
        self.assertEqual(records[0].decision.value,'REVIEW')
        self.assertIn('CALENDAR_DATE_REQUIRED',records[0].reason_codes)
    def test_competing_course_identity_abstains(self):
        _,_,records=decisions('CS101 or CS102 Monday 09:00-10:00 Lecture')
        self.assertEqual(records[0].decision.value,'ABSTAIN')
        self.assertIn('CORE_READING_AMBIGUOUS',records[0].reason_codes)
    def test_reversed_times_abstain_without_repairing_prediction(self):
        request,_,records=decisions('CS101 Monday 11:00-09:00 Lecture')
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
        self.assertFalse(request.reconstruction['canonical_output']['packet']['events'])
    def test_b3_remains_diagnostic_only(self):
        _,_,records=decisions(GOOD,mode='unconstrained')
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
    def test_duplicate_candidates_are_not_silently_accepted(self):
        _,_,records=decisions(GOOD,GOOD)
        self.assertTrue(any('DUPLICATE_REQUIRES_RESOLUTION' in d.reason_codes for d in records))
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
    def test_proven_same_cohort_overlap_abstains(self):
        _,_,records=decisions('CS101 Monday 09:00-11:00 Cohort C1 Weeks 1-3','CS102 Monday 10:00-12:00 Cohort C1 Weeks 1-3')
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
    def test_unknown_cohort_remains_review_and_cannot_verify_schedule(self):
        _,_,records=decisions(GOOD,'MA202 Monday 2033-02-07 11:00-12:00 Tutorial Room B202')
        self.assertTrue(all(d.decision.value=='REVIEW' for d in records))
        self.assertTrue(any('CONFLICT_SCOPE_UNCONFIRMED' in d.reason_codes for d in records))
        self.assertTrue(all(d.schedule_verified is False for d in records))
    def test_unbounded_recurrence_abstains(self):
        _,_,records=decisions(GOOD+' weekly')
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
        self.assertTrue(any('RECURRENCE_EVIDENCE_INCOMPLETE' in d.reason_codes for d in records))
    def test_day_date_disagreement_requires_checking(self):
        _,_,records=decisions('CS101 Tuesday 2033-02-07 09:00-10:00 Lecture Room A101')
        self.assertIn('SOURCE_DAY_DATE_CONFLICT',records[0].reason_codes)
        self.assertNotEqual(records[0].decision.value,'ACCEPT')
    def test_absent_confidence_is_not_fabricated_or_used_as_zero_probability(self):
        _,_,records=decisions(GOOD)
        self.assertEqual(records[0].decision.value,'REVIEW')
        self.assertIsNone(records[0].source_routing.calibrated_probability)
        self.assertTrue(all(f.parser_confidence is None for f in records[0].fields))
    def test_incomplete_search_abstains(self):
        _,_,records=decisions(GOOD,settings=development_settings(max_assignments=2))
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in records))
    def test_evidence_from_another_capture_is_rejected(self):
        _,run,records=decisions(GOOD);graph=run.graph()
        evidence=tuple(s.model_copy(update={'source_document_id':'wrong-source'}) for s in graph.evidence)
        routing=assess_source(run,run.events()[0],records[0].findings,source_evidence=evidence)
        self.assertEqual(routing.route,'ABSTAIN');self.assertIn('UNOWNED_FIELD_EVIDENCE',routing.reason_codes)
    def test_pending_correction_invalidates_old_routing(self):
        _,run,records=decisions(GOOD)
        d=assess_event(run,run.events()[0],records[0].findings,revalidation_required=True)
        self.assertEqual(d.decision.value,'ABSTAIN');self.assertIn('REVALIDATION_REQUIRED',d.reason_codes)
    def test_report_includes_entire_candidate_population_without_accuracy_claim(self):
        _,_,a=decisions(GOOD);_,_,b=decisions('CS404 Wednesday Time: TBA')
        r=report(a+b);self.assertEqual(r['candidate_count'],2);self.assertEqual(r['review_count'],1);self.assertEqual(r['abstain_count'],1)
        self.assertEqual(r['automatic_coverage'],0);self.assertEqual(r['review_demand'],.5);self.assertEqual(r['abstention_rate'],.5)
        self.assertIsNone(r['supported_event_accuracy']);self.assertIsNone(r['unsafe_acceptance']);self.assertEqual(r['selective_effectiveness'],'NOT_ESTABLISHED')
    def test_empty_source_has_no_fake_rate_denominator(self):
        _,_,records=decisions('No timetable classes here')
        r=report(records);self.assertEqual(r['candidate_count'],0);self.assertIsNone(r['automatic_coverage']);self.assertEqual(r['run_level_abstentions'],1)
    def test_api_source_policy_and_registered_policy_barriers(self):
        request,_,_=decisions(GOOD)
        with TestClient(app) as client:
            response=client.post('/api/policy/assess',json=request.model_dump(mode='json'))
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['profile'],PROFILE);self.assertEqual(response.json()['decisions'][0]['decision'],'REVIEW')
            self.assertFalse(response.json()['registered_routing_ready'])
            bad=request.model_dump(mode='json');bad['policy_profile']='registered'
            self.assertEqual(client.post('/api/policy/assess',json=bad).status_code,409)
            bad=request.model_dump(mode='json');bad['accept_enabled']=True
            self.assertEqual(client.post('/api/policy/assess',json=bad).status_code,422)
    def test_source_review_does_not_open_calendar_eligibility(self):
        request,run,records=decisions(GOOD)
        from backend.models.event import CanonicalTimetable
        packet=CanonicalTimetable(source=run.graph().source,evidence=run.graph().evidence,events=run.events())
        with TestClient(app) as client:
            r=client.post('/api/calendar/v1/eligibility',json={'packet':packet.model_dump(mode='json'),'assessments':[d.model_dump(mode='json') for d in records]})
            self.assertEqual(r.status_code,200,r.text)
            self.assertTrue(all(e['status']=='BLOCKED' and not e['sandbox_write_permitted'] for e in r.json()['events']))
            self.assertEqual(client.post('/api/calendar/v1/sync',json={}).status_code,404)

@unittest.skipUnless(os.name=='nt','Real DPAPI synthetic review regression')
class SourceRoutingCorrectionTests(unittest.TestCase):
    def test_human_correction_revalidation_restart_and_stale_proposal(self):
        from backend.review.auth import TrustedHostAuthority
        from backend.review.persistence import ReviewJournal,PersistentReviewStore
        from backend.review.models import CorrectionSubmission,FieldCorrection,HumanEvidenceInput,ReviewError
        request,_,_=decisions('CS101 Monday 2033-02-07 09:00-10:00 Lecture Room TBA; source amendment Z018')
        source=request.source_packet.source;actor='SYNTHETIC-SOURCE-ROUTING-ONLY'
        auth=TrustedHostAuthority(test_only_source_bindings={(source.source_document_id,source.content_sha256)})
        principal,_=auth.issue(actor,{'read','queue','assign','correct','revalidate','oversee'},mode='TEST_ONLY',source_bindings={(source.source_document_id,source.content_sha256)})
        with TemporaryDirectory(prefix='stair-source-review-SYNTHETIC-') as directory:
            store=PersistentReviewStore(auth,ReviewJournal(Path(directory),actor));task_id=store.ingest(request,principal)[0]
            task=store.get(task_id,principal);old=task.decision
            self.assertEqual(old.policy_profile,PROFILE);self.assertEqual(old.decision.value,'REVIEW')
            task=store.assign(task_id,actor,task.etag,principal);task=store.start(task_id,task.etag,principal)
            span=next(s for s in store.evidence(task_id,principal) if 'source amendment' in s.raw_text)
            evidence='human:synthetic-source-review'
            proposal=CorrectionSubmission(request_id=uuid4(),expected_etag=task.etag,expected_revision_id=task.current_event.revision.revision_id,reason='Invented source amendment; not a real user correction',human_evidence=(HumanEvidenceInput(evidence_id=evidence,raw_text='Z018',source_evidence_ids=(span.evidence_id,),page=span.page,note='SYNTHETIC ONLY'),),fields=(FieldCorrection(field_name='location',state='PRESENT',raw_value='Z018',value='Z018',evidence_ids=(evidence,)),))
            changed=store.submit(task_id,proposal,principal)['task'];self.assertEqual(changed.decision.decision.value,'ABSTAIN')
            checked=store.revalidate(task_id,changed.etag,principal)
            self.assertEqual(checked.decision.decision.value,'REVIEW')
            self.assertEqual(checked.decision.source_routing.revision_id,checked.current_event.revision.revision_id)
            self.assertNotIn('UNOWNED_FIELD_EVIDENCE',checked.decision.reason_codes)
            self.assertEqual(checked.current_event.human_verification.status,'NOT_VERIFIED');self.assertEqual(checked.current_event.calendar.approval_status,'NOT_APPROVED')
            history=store.history(task_id,principal)
            restored=PersistentReviewStore(auth,ReviewJournal(Path(directory),actor))
            self.assertEqual(restored.get(task_id,principal),checked);self.assertEqual(restored.history(task_id,principal),history)
            with self.assertRaises(ReviewError):restored.submit(task_id,proposal.model_copy(update={'request_id':uuid4()}),principal)
