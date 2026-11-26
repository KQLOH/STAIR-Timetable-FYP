"""Synthetic transport regressions; no private OCR source or research scores.

Whitespace padding reproduces the reported wire size without changing evidence.
Actual rendered-image OCR is covered by test_s2b_blocks separately.
"""
import copy
import json
import unittest

from fastapi.testclient import TestClient

from backend.main import app
from backend.layout.block_grid import reconstruct_blocks
from backend.review.api import MAX_ASSESSMENT_BYTES
from tests.s2b_fixtures import packet


class AssessmentCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source, layout = packet()
        reconstruction = reconstruct_blocks(source, layout)[0]
        cls.body = {
            'source_packet': source.model_dump(mode='json'),
            'layout_evidence': layout.model_dump(mode='json'),
            'reconstruction': reconstruction,
            'policy_profile': 'm5-source-review-v1',
        }

    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def post(self, path, body, size=None):
        wire = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if size is not None:
            self.assertLess(len(wire), size)
            wire += b' ' * (size - len(wire))
        return self.client.post(path, content=wire, headers={'Content-Type': 'application/json'})

    def test_reported_size_accepted_by_both_full_envelope_routes(self):
        for path in ['/api/policy/assess', '/api/layout/s2b/assess']:
            with self.subTest(path=path):
                response = self.post(path, self.body, size=3_027_125)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertFalse(result['registered_routing_ready'])
                self.assertTrue(result['decisions'])
                self.assertTrue(all(d['decision'] in {'REVIEW', 'ABSTAIN'} for d in result['decisions']))
                self.assertTrue(all(not d['registered_routing_eligible'] for d in result['decisions']))

    def test_compact_and_full_results_match_and_bad_digest_is_rejected(self):
        path = '/api/layout/s2b/assess'
        full = self.post(path, self.body)
        self.assertEqual(full.status_code, 200, full.text)
        compact = {**self.body, 'reconstruction': {
            'canonical_digest': self.body['reconstruction']['canonical_digest']}}
        response = self.post(path, compact)
        self.assertEqual(response.status_code, 200, response.text)
        def stable(result):
            result = result.json()
            for decision in result['decisions']:
                # Each assessment issues a new receipt and timestamp.
                decision.pop('decision_id')
                decision.pop('recorded_at')
            return result
        self.assertEqual(stable(response), stable(full))
        compact['reconstruction']['canonical_digest'] = '0' * 64
        self.assertEqual(self.post(path, compact).status_code, 422)

    def test_large_payload_still_checks_source_binding(self):
        body = copy.deepcopy(self.body)
        body['reconstruction']['canonical_digest'] = '0' * 64
        for path in ['/api/policy/assess', '/api/layout/s2b/assess']:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, body, size=3_027_125).status_code, 422)

    def test_byte_limit_enforced_at_boundary_and_without_content_length(self):
        for path in ['/api/policy/assess', '/api/layout/s2b/assess']:
            with self.subTest(path=path):
                response = self.post(path, self.body, size=MAX_ASSESSMENT_BYTES)
                self.assertEqual(response.status_code, 200, response.text)
                # A streaming sender cannot bypass the limit by omitting a length.
                chunks = iter([b' ' * MAX_ASSESSMENT_BYTES, b' '])
                response = self.client.post(path, content=chunks,
                    headers={'Content-Type': 'application/json'})
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()['detail'], 'REVIEW_REQUEST_TOO_LARGE')
