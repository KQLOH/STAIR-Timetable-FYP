"""Synthetic application-policy tests; not selective research evaluation."""
from dataclasses import replace
from uuid import uuid4
import unittest
from pydantic import ValidationError

from test_m4_reconstruction import packet, SINGLE
from backend.models.event import EventIdentity, stable_session_key
from backend.reconstruction.service import reconstruct
from backend.reconstruction.models import development_settings, digest
from backend.review.binding import AssessmentInput, bind_input
from backend.review.models import development_policy, ReviewError
from backend.review.policy import assess_run, assess_event, explain
from backend.review.validation import validation_view


def fixture(*texts,mode='constrained',settings=None,context=None):
    from backend.reconstruction.models import Context
    settings=settings or development_settings(); context=context or Context()
    source=packet(*(texts or (SINGLE,)))
    result=reconstruct(source,mode,context,settings)
    request=AssessmentInput(source_packet=source,reconstruction=result,decoder_mode=mode,
                            reconstruction_context=context,reconstruction_settings=settings)
    return request,bind_input(request)


class PolicyTests(unittest.TestCase):
    def test_clear_m4_evidence_default_abstains_unresolved_identity(self):
        _,run=fixture()
        decision=assess_run(run)[0]
        self.assertEqual(decision.decision.value,'ABSTAIN')
        self.assertIn('UNRESOLVED_SESSION_IDENTITY',decision.reason_codes)
        self.assertIn('ACCEPT_DISABLED',decision.reason_codes)

    def test_bounded_review_interface_remains_application_only(self):
        from backend.models.event import CanonicalTimetable
        source=packet(SINGLE)
        source=source.model_copy(update={'source':source.source.model_copy(update={'source_family_id':'synthetic-family','timetable_id':'synthetic-table'})})
        result=reconstruct(source,'constrained')
        run=bind_input(AssessmentInput(source_packet=source,reconstruction=result))
        event=run.events()[0]; graph=run.graph()
        # Explicitly synthetic identity authority, not a real verified session.
        identity=EventIdentity(event_id=event.identity.event_id,status='CONFIRMED',source_family_id='synthetic-family',
            timetable_id='synthetic-table',stable_source_session_id='synthetic-session',
            stable_key=stable_session_key('synthetic-family','synthetic-table','synthetic-session'),evidence_ids=(graph.evidence[0].evidence_id,))
        hypothetical=event.model_copy(update={'identity':identity})
        CanonicalTimetable(source=graph.source,evidence=graph.evidence,events=(hypothetical,))
        _,findings=validation_view(run,(event,),graph.evidence,graph.context)
        decision=assess_event(run,hypothetical,findings)
        self.assertEqual(decision.decision.value,'REVIEW')
        self.assertFalse(decision.registered_routing_eligible)
        self.assertIn('APPLICATION_REVIEW_ONLY',decision.reason_codes)

    def test_accept_cannot_be_enabled(self):
        with self.assertRaises(ValidationError):
            type(development_policy()).model_validate({**development_policy().model_dump(),'accept_enabled':True})

    def test_no_threshold_or_fake_calibration(self):
        _,run=fixture(); decision=assess_run(run)[0]
        self.assertIsNone(decision.calibrated_probability)
        self.assertNotIn('threshold',development_policy().model_dump())
        self.assertTrue(all(f.parser_confidence is None for f in decision.fields))

    def test_missing_required_evidence(self):
        _,run=fixture('CS101 Lecture Time: TBA')
        decision=assess_run(run)[0]
        self.assertIn('MISSING_REQUIRED_EVIDENCE',decision.reason_codes)
        self.assertIn('OMITTED_RECONSTRUCTION',decision.reason_codes)

    def test_unknown_venue_and_tba(self):
        _,run=fixture('CS101 Monday 09:00-11:00 Room: TBA')
        decision=assess_run(run)[0]
        self.assertIn('SOURCE_AMBIGUITY',decision.reason_codes)
        self.assertEqual(next(f.state.value for f in decision.fields if f.field=='location'),'UNRESOLVED')

    def test_missing_venue_not_source_absent(self):
        _,run=fixture('CS101 Monday 09:00-11:00')
        self.assertEqual(next(f.state.value for f in assess_run(run)[0].fields if f.field=='location'),'MISSING')

    def test_contradictory_b3_time(self):
        _,run=fixture('CS101 Monday 11:00-09:00',mode='unconstrained')
        decision=assess_run(run)[0]
        self.assertIn('CONSTRAINT_VIOLATION',decision.reason_codes)
        self.assertIn('B3_DIAGNOSTIC_ONLY',decision.reason_codes)
        self.assertEqual(decision.decision.value,'ABSTAIN')

    def test_incomplete_search(self):
        _,run=fixture(settings=development_settings(max_assignments=2))
        decision=assess_run(run)[0]
        self.assertIn('INCOMPLETE_SEARCH',decision.reason_codes)
        self.assertEqual(decision.decision.value,'ABSTAIN')

    def test_execution_aborted_source_binding(self):
        source=packet(SINGLE)
        request=AssessmentInput(source_packet=source,reconstruction={'status':'EXECUTION_ABORTED','reason':'WALL_CLOCK_WATCHDOG',
            'canonical_output':None,'scorable':False,'execution_metadata':{'elapsed_seconds':30.0}})
        decision=assess_run(bind_input(request))[0]
        self.assertIsNone(decision.candidate_id)
        self.assertIn('EXECUTION_ABORTED',decision.reason_codes)
        self.assertEqual(decision.score_provenance,'NO_SCORABLE_OUTPUT')

    def test_unresolved_alternatives(self):
        _,run=fixture('CS101 or CS102 Monday 09:00-11:00')
        self.assertIn('UNRESOLVED_ALTERNATIVES',assess_run(run)[0].reason_codes)

    def test_no_verification_or_completeness_claim(self):
        _,run=fixture(); decision=assess_run(run)[0]
        self.assertFalse(decision.schedule_verified)
        self.assertFalse(decision.registered_routing_eligible)
        self.assertEqual(run.events()[0].human_verification.status,'NOT_VERIFIED')
        self.assertEqual(run.events()[0].calendar.approval_status,'NOT_APPROVED')

    def test_deterministic_explanations(self):
        _,run=fixture()
        a,b=assess_run(run)[0],assess_run(run)[0]
        self.assertEqual(a.reason_codes,b.reason_codes)
        self.assertEqual(explain(a.reason_codes),explain(b.reason_codes))
        self.assertEqual(a.policy_digest,b.policy_digest)

    def test_input_and_reconstruction_digest(self):
        request,run=fixture(); d=assess_run(run)[0]
        self.assertEqual(d.reconstruction_digest,request.reconstruction['canonical_digest'])
        self.assertEqual(d.input_digest,run.input_digest)
        self.assertEqual(d.revision_id,run.events()[0].revision.revision_id)

    def test_tampered_output_rejected(self):
        request,_=fixture(); bad=request.model_dump(mode='json')
        bad['reconstruction']['canonical_output']['packet']['events'][0]['revision']['content']['course_code']='CS999'
        with self.assertRaises(ReviewError): bind_input(AssessmentInput.model_validate(bad))

    def test_rehashed_forged_candidate_rejected(self):
        request,_=fixture(); bad=request.model_dump(mode='json')
        bad['reconstruction']['canonical_output']['packet']['events'][0]['revision']['content']['course_code']='CS999'
        bad['reconstruction']['canonical_digest']=digest(bad['reconstruction']['canonical_output'])
        with self.assertRaises(ReviewError): bind_input(AssessmentInput.model_validate(bad))

    def test_source_graph_mismatch_rejected(self):
        request,_=fixture(); bad=request.model_dump(mode='json')
        bad['source_packet']['evidence'][0]['raw_text']='CS999 Monday 09:00-11:00'
        with self.assertRaises(ReviewError): bind_input(AssessmentInput.model_validate(bad))

    def test_b3_requires_explicit_mode(self):
        request,_=fixture(mode='unconstrained')
        with self.assertRaises(ReviewError): bind_input(request.model_copy(update={'decoder_mode':'constrained'}))

    def test_registered_policy_blocked(self):
        request,_=fixture()
        with self.assertRaises(ReviewError) as exc: bind_input(request.model_copy(update={'policy_profile':'registered'}))
        self.assertEqual(exc.exception.status_code,409)

    def test_date_and_weekly_contract_binding(self):
        _,run=fixture('CS101 2026-01-01 09:00-11:00')
        self.assertTrue(run.events())
        _,run=fixture(SINGLE+' weekly',mode='unconstrained')
        self.assertIn('RECURRENCE_UNRESOLVED',assess_run(run)[0].reason_codes)

    def test_zero_candidates_run_level_abstention(self):
        _,run=fixture('Synthetic empty table')
        d=assess_run(run)[0]
        self.assertEqual(d.decision.value,'ABSTAIN')
        self.assertIsNone(d.candidate_id)
