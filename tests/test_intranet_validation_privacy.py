"""NFR2 request validation uses synthetic markers and never contacts a portal."""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app

class IntranetValidationPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.addCleanup(self.client.close)
    def test_malformed_password_and_student_identity_are_not_echoed(self):
        marker='SYNTHETIC-PRIVATE-INPUT-NOT-A-CREDENTIAL'
        for payload in ({'student_id':'SYNTHETIC','password':{'nested':marker}},
                        {'student_id':[marker],'password':marker}):
            with self.subTest(payload_type='malformed login'), patch('backend.main.TARUMTClient') as factory:
                response=self.client.post('/api/intranet/connect',json=payload)
                self.assertEqual(response.status_code,422)
                self.assertEqual(response.json(),{'detail':'Invalid institutional timetable request.'})
                self.assertNotIn(marker,response.text)
                self.assertNotIn('input',response.text)
                factory.assert_not_called()
    def test_invalid_session_shape_and_json_do_not_echo_private_material(self):
        marker='SYNTHETIC-SESSION-INPUT'
        for route in ('/api/intranet/timetable','/api/intranet/disconnect'):
            response=self.client.post(route,json={'token':{'nested':marker}})
            self.assertEqual(response.status_code,422)
            self.assertNotIn(marker,response.text)
        response=self.client.post('/api/intranet/connect',content=b'{"password":"SYNTHETIC-JSON-SECRET",bad',headers={'Content-Type':'application/json'})
        self.assertEqual(response.status_code,422)
        self.assertNotIn('SYNTHETIC-JSON-SECRET',response.text)
    def test_missing_values_stay_controlled_errors(self):
        self.assertEqual(self.client.post('/api/intranet/connect',json={}).status_code,422)
        self.assertEqual(self.client.post('/api/intranet/timetable',json={}).status_code,422)
    def test_other_request_validation_keeps_its_existing_contract(self):
        response=self.client.post('/api/extract?pipeline=evidence&layout_profile=auto',content=b'broken',headers={'Content-Type':'image/png','X-Filename':'bad.png'})
        self.assertEqual(response.status_code,422)
        self.assertNotEqual(response.json(),{'detail':'Invalid institutional timetable request.'})
