"""Synthetic capacity regression; retains ownership, body and write boundaries."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.calendar.api import router
from backend.calendar.models import CalendarInput, MAX_DIAGNOSTIC_EVIDENCE_SPANS
from tests import test_m6_calendar as calendar_tests

class CalendarEvidenceCapacityTests(unittest.TestCase):
    def setUp(self):
        calendar_tests.CalendarTests.setUp(self)

    def expanded(self, count):
        packet=self.bundle.packet
        extra=tuple(packet.evidence[0].model_copy(update={'evidence_id':f'synthetic:capacity:{i}','raw_text':'Synthetic unused source detail'}) for i in range(count-len(packet.evidence)))
        return self.bundle.model_copy(update={'packet':packet.model_copy(update={'evidence':packet.evidence+extra})})

    def client(self):
        app=FastAPI();app.include_router(router)
        return TestClient(app)

    def test_full_source_graph_above_old_limit_gets_blocked_preview(self):
        bundle=self.expanded(600)
        with self.client() as client:
            r=client.post('/api/calendar/v1/preview',json={'bundle':bundle.model_dump(mode='json')})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['write_authorized'])
        p=r.json()['preview'];self.assertEqual(p['scope'],'DEVELOPMENT_BLOCKED')
        self.assertFalse(p['external_calendar'])
        self.assertTrue(p['operations']);self.assertTrue(all(o['action']=='BLOCKED' for o in p['operations']))
        self.assertEqual(bundle.packet.evidence[:len(self.bundle.packet.evidence)],self.bundle.packet.evidence)

    def test_exact_capacity_is_valid(self):
        b=self.expanded(MAX_DIAGNOSTIC_EVIDENCE_SPANS)
        self.assertEqual(len(CalendarInput.model_validate_json(b.model_dump_json()).packet.evidence),MAX_DIAGNOSTIC_EVIDENCE_SPANS)

    def test_excess_spans_still_rejected(self):
        with self.assertRaises(ValidationError):
            CalendarInput.model_validate_json(self.expanded(MAX_DIAGNOSTIC_EVIDENCE_SPANS+1).model_dump_json())

    def test_text_limit_still_rejected(self):
        body=self.bundle.model_dump(mode='json');body['packet']['evidence'][0]['raw_text']='x'*500001
        with self.assertRaises(ValidationError):CalendarInput.model_validate(body)

    def test_foreign_capture_evidence_still_rejected(self):
        body=self.expanded(600).model_dump(mode='json');body['packet']['evidence'][-1]['source_document_id']='foreign:synthetic'
        with self.assertRaises(ValidationError):CalendarInput.model_validate(body)

    def test_http_body_limit_still_rejected(self):
        with self.client() as client:
            r=client.post('/api/calendar/v1/preview',content=' '* (2*1024*1024+1),headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)

if __name__=='__main__':unittest.main()
