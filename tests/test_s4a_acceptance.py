"""Bounded QA harness isolation. Synthetic inputs; no live authority or portal."""
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import main
from tests.s4a_acceptance import Controls, MockPortal, qa_app, extraction_wrapper, MOCK_ID, MOCK_SECRET
from tests.test_m51_integration import synthetic_png


class AcceptanceHarnessTests(unittest.TestCase):
    def setUp(self):
        self.controls=Controls()
        self.app=qa_app(self.controls)
        self.client=TestClient(self.app);self.addCleanup(self.client.close)

    def test_qa_routes_never_added_to_production(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/__qa__/state').status_code,404)
            self.assertEqual(client.post('/__qa__/control',json={'action':'expire'}).status_code,404)
        self.assertEqual(self.client.get('/__qa__/state').status_code,200)

    def test_no_operator_or_calendar_authority(self):
        for route in ('/api/review/session','/api/review/queue','/api/calendar/v1/test-only/session'):
            self.assertEqual(self.client.get(route).status_code,403)
        self.assertFalse(self.controls.state()['review_enrolled'])
        self.assertFalse(self.controls.state()['calendar_enrolled'])

    def test_control_allowlist_cannot_mint_authority(self):
        for body in ({'action':'enroll'},{'action':'hold','path':'/api/calendar/v1/test-only/execute'}):
            self.assertEqual(self.client.post('/__qa__/control',json=body).status_code,422)

    def test_mock_lifecycle_expires_without_logging_secret(self):
        output=StringIO()
        with patch.dict(main.CONNECTED_CLIENTS,{},clear=True),patch.object(main,'TARUMTClient',lambda:MockPortal(self.controls)),redirect_stdout(output):
            login=self.client.post('/api/intranet/connect',json={'student_id':MOCK_ID,'password':MOCK_SECRET})
            self.assertEqual(login.status_code,200)
            self.assertEqual(login.json()['data']['events'][0]['subject_code'],'SYN7101')
            token=login.json()['connection_token']
            self.assertEqual(self.client.post('/__qa__/control',json={'action':'expire'}).status_code,200)
            expired=self.client.post('/api/intranet/timetable',json={'token':token})
            self.assertEqual(expired.status_code,401);self.assertNotIn(token,main.CONNECTED_CLIENTS)
            self.assertTrue(self.controls.portals[0].closed)
            self.assertNotIn(MOCK_SECRET,expired.text);self.assertNotIn('PRIVATE_ERROR_CANARY',expired.text)
        self.assertEqual(output.getvalue(),'')

    def test_missing_ocr_is_real_adapter_error_not_native_substitution(self):
        with patch.object(main,'extract_document',extraction_wrapper(main.extract_document)):
            r=self.client.post('/api/extract',content=synthetic_png(),headers={'X-Filename':'missing-runtime.png','Content-Type':'image/png'})
        self.assertEqual(r.status_code,503)
        self.assertEqual(r.json()['detail']['diagnostic_code'],'OCR_EXECUTABLE_MISSING')

    def test_diagnostic_request_inventory_does_not_store_body_or_token(self):
        self.client.post('/api/intranet/timetable',json={'token':'S4A_SYNTHETIC_TOKEN_CANARY'})
        inventory=self.controls.state()['requests']
        self.assertEqual(set(inventory[0]),{'path','filename','method'})
        self.assertNotIn('S4A_SYNTHETIC_TOKEN_CANARY',str(inventory))

    def test_production_settings_remain_unenrolled_after_qa_creation(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/api/review/session').status_code,403)
            self.assertEqual(client.get('/api/calendar/v1/test-only/session').status_code,403)
