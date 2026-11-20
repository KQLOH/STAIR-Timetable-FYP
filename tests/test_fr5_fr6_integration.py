"""FR5/FR6 software integration only; no registered operating threshold or Gold."""
from uuid import uuid4
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_m5_policy import fixture
from backend.models.event import CanonicalTimetable,DecisionStatus
from backend.calendar.api import router,CalendarDiagnosticInput
from backend.calendar.models import CalendarInput
from backend.calendar.eligibility import assess
from backend.review.api import _decision
from backend.review.policy import assess_run

class RoutingCalendarIntegrationTests(unittest.TestCase):
    def setUp(self):
        request,run=fixture();self.event=run.events()[0]
        self.packet=CanonicalTimetable(source=request.source_packet.source,evidence=run.graph().evidence,events=run.events())
        self.record=assess_run(run)[0]
        app=FastAPI();app.include_router(router);self.client=TestClient(app);self.addCleanup(self.client.close)
    def diagnostic(self,route='ABSTAIN',**updates):
        e=self.event
        return {'candidate_id':e.candidate_id,'event_id':str(e.identity.event_id),'revision_id':str(e.revision.revision_id),'revision_digest':self.record.revision_digest,'route':route,'policy_profile':'m5-source-review-v1','registered_accept_eligible':False,**updates}
    def body(self,*diagnostics):return {'packet':self.packet.model_dump(mode='json'),'reconstruction_status':'COMPLETE_FINITE_SEARCH','routing_diagnostics':list(diagnostics)}
    def test_current_review_and_abstain_reach_blocked_preview(self):
        for route in ('REVIEW','ABSTAIN'):
            with self.subTest(route=route):
                response=self.client.post('/api/calendar/v1/preview',json={'bundle':self.body(self.diagnostic(route))});self.assertEqual(response.status_code,200,response.text)
                result=response.json();self.assertFalse(result['write_authorized']);p=result['preview'];self.assertFalse(p['external_calendar'])
                self.assertIn('M5_'+route+'_REQUIRED',p['eligibility'][0]['reason_codes']);self.assertFalse(p['eligibility'][0]['sandbox_write_permitted'])
                self.assertTrue(all(o['action'] in {'BLOCKED','CONFLICT'} for o in p['operations']))
    def test_compact_diagnostics_are_not_full_assessments(self):
        with self.assertRaises(ValidationError):CalendarInput(packet=self.packet,assessments=(_decision(self.record),))
        result=self.client.post('/api/calendar/v1/eligibility',json=self.body(self.diagnostic('REVIEW')))
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['policy_status'],'BLOCKED')
        self.assertEqual(result.json()['events'][0]['actual_write_eligibility'],'BLOCKED')
    def test_stale_or_foreign_revision_is_not_current_routing(self):
        for updates in ({'revision_id':str(uuid4())},{'event_id':str(uuid4())},{'revision_digest':'0'*64}):
            response=self.client.post('/api/calendar/v1/eligibility',json=self.body(self.diagnostic('REVIEW',**updates)))
            self.assertEqual(response.status_code,200,response.text);codes=response.json()['events'][0]['reason_codes']
            self.assertIn('M5_ROUTING_DIAGNOSTIC_STALE',codes);self.assertNotIn('M5_REVIEW_REQUIRED',codes)
    def test_diagnostic_cannot_claim_accept_or_registration(self):
        for d in (self.diagnostic('ACCEPT'),self.diagnostic(registered_accept_eligible=True)):
            self.assertEqual(self.client.post('/api/calendar/v1/eligibility',json=self.body(d)).status_code,422)
    def test_repeated_conflicting_diagnostics_remain_blocked(self):
        b=CalendarDiagnosticInput.model_validate(self.body(self.diagnostic('REVIEW'),self.diagnostic('ABSTAIN')))
        e=assess(b,self.event,test_authorized=True);self.assertFalse(e.sandbox_write_permitted);self.assertIn('M5_ROUTING_DIAGNOSTIC_CONFLICT',e.reason_codes)
    def test_test_permission_cannot_override_review_diagnostic(self):
        for route in ('REVIEW','ABSTAIN'):
            b=CalendarDiagnosticInput.model_validate(self.body(self.diagnostic(route)))
            self.assertFalse(assess(b,self.event,test_authorized=True).sandbox_write_permitted)
    def test_full_assessment_requires_event_and_digest_binding(self):
        bad=self.record.model_copy(update={'revision_digest':'0'*64,'decision':DecisionStatus.REVIEW})
        b=CalendarInput(packet=self.packet,assessments=(bad,));e=assess(b,self.event)
        self.assertIn('M5_ASSESSMENT_BINDING_MISMATCH',e.reason_codes);self.assertFalse(e.sandbox_write_permitted)
    def test_empty_diagnostic_preserves_original_calendar_input_digest(self):
        original=CalendarInput(packet=self.packet);new=CalendarDiagnosticInput.model_validate_json(original.model_dump_json())
        self.assertEqual(new.model_dump(mode='json'),original.model_dump(mode='json'));self.assertEqual(new.model_dump_json(),original.model_dump_json())
    def test_full_unregistered_accept_record_still_cannot_enable_writes(self):
        record=self.record.model_copy(update={'decision':DecisionStatus.ACCEPT});b=CalendarInput(packet=self.packet,assessments=(record,))
        e=assess(b,self.event);self.assertFalse(e.sandbox_write_permitted);self.assertIn('UNAPPROVED_SELECTIVE_POLICY',e.reason_codes)
