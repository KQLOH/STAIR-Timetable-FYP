"""Independent portable-s4b-v1 security checks; never a live acceptance run.

The 18 held files are not edited or substituted. Original legacy tests remain
separately runnable with their original resources. This suite reads none of them.
"""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import builtins
import io
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests import portable_s4b_v1 as portable
from tests.s4b_transport import COOKIE, Denied

BASE = {'Origin': portable.ORIGIN, 'Referer': portable.ORIGIN + '/s4b/',
        'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'}


class PortableS4BV1Tests(unittest.TestCase):
    def setUp(self):
        parent = portable.ROOT / 'data/temp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = TemporaryDirectory(prefix='m6-test-g3-portable-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.events = []
        # Deny historical data reads, while allowing only this newly allocated
        # synthetic journal. Reads of protected proposal/source files also fail.
        original_builtin, original_io = builtins.open, io.open
        fresh = Path(self.temp.name).resolve()
        def check(file, mode):
            if not isinstance(file, (str, bytes, Path)) or not ('r' in mode or '+' in mode):
                return
            path = Path(file).resolve()
            if path.is_relative_to(portable.ROOT/'data') and not path.is_relative_to(fresh) and not path.is_relative_to(Path(sys.prefix)):
                raise AssertionError('PORTABLE_TEST_ACCESSED_NONSHARED_DATA')
            if path.is_relative_to(portable.ROOT/'docs/system-completion'):
                raise AssertionError('PORTABLE_TEST_ACCESSED_HISTORICAL_DOCUMENT')
            if path.is_relative_to(portable.ROOT/'sources'):
                raise AssertionError('PORTABLE_TEST_ACCESSED_SOURCE_ARCHIVE')
        def guarded_builtin(file, mode='r', *args, **kwargs):
            check(file, mode)
            return original_builtin(file, mode, *args, **kwargs)
        def guarded_io(file, mode='r', *args, **kwargs):
            check(file, mode)
            return original_io(file, mode, *args, **kwargs)
        self.addCleanup(patch.stopall)
        patch('builtins.open', guarded_builtin).start()
        patch('io.open', guarded_io).start()
        self.app, self.broker = portable.build_host(fresh/'calendar', portable.TARGET, audit=self.events.append)
        self.addCleanup(self.broker.revoke_all)
        self.client = TestClient(self.app, base_url=portable.ORIGIN, client=('127.0.0.1', 46601))
        self.addCleanup(self.client.close)

    def pending(self, lane='review'):
        response = self.client.post('/__s4b_host__/pending/'+lane, json={}, headers=BASE)
        self.assertEqual(response.status_code, 200)
        return response

    def pair(self, lane='review'):
        data = self.pending(lane).json()
        # TestClient-only issuer; this is not confirmation for a live browser.
        self.broker.confirm_from_console(lane, data['pairing_nonce'])
        return data

    def headers(self, lane, data):
        return {**BASE, 'X-STAIR-Demo-Transport': lane, 'X-STAIR-Demo-Binding': data['transport_binding']}

    def session(self, lane, data, headers=None):
        path = '/api/review/session' if lane=='review' else '/api/calendar/v1/test-only/session'
        return self.client.get(path, headers=self.headers(lane, data) if headers is None else headers)

    def test_01_frozen_resources_and_deterministic_pdf(self):
        self.assertEqual(portable.verify_approved_inputs()['version'], 'portable-s4b-v1')
        self.assertEqual(portable.review_pdf(), portable.review_pdf())
        self.assertEqual(len(portable.review_assessment(self.app)['reconstruction']['canonical_output']['packet']['events']), 2)

    def test_02_resource_tampering_is_rejected(self):
        original = Path.read_bytes
        def changed(path):
            value = original(path)
            return value+b' ' if path.name=='portable_s4b_spec_v1.json' else value
        with patch.object(Path, 'read_bytes', changed):
            with self.assertRaisesRegex(RuntimeError, 'PORTABLE_RESOURCE_DRIFT'):
                portable.verify_approved_inputs()

    def test_03_historical_or_existing_target_is_rejected(self):
        with self.assertRaises(ValueError):
            portable.build_host(Path(self.temp.name)/'other', 'test:unapproved-target')
        with self.assertRaisesRegex(ValueError, 'FRESH_DIRECTORY'):
            portable.build_host(Path(self.temp.name)/'calendar', portable.TARGET)

    def test_04_normal_application_is_unenrolled(self):
        from backend.main import app
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/review/session').status_code, 403)
            self.assertEqual(client.get('/api/calendar/v1/test-only/session').status_code, 403)
            self.assertEqual(client.post('/__s4b_host__/pending/review', json={}).status_code, 404)

    def test_05_pending_is_not_a_grant(self):
        data = self.pending().json()
        self.assertFalse(data['authorized'])
        self.assertEqual(self.session('review', data).status_code, 403)

    def test_06_review_pairing_has_exact_scope(self):
        data = self.session('review', self.pair()).json()
        self.assertEqual(data['actor_reference'], portable.REVIEW_ACTOR)
        self.assertEqual(set(data['permissions']), set(portable.REVIEW_PERMISSIONS))
        self.assertNotIn('resolve', data['permissions'])
        self.assertEqual(data['mode'], 'TEST_ONLY')

    def test_07_calendar_uses_separate_issuer_and_permissions(self):
        data = self.session('calendar', self.pair('calendar')).json()
        self.assertEqual(set(data['permissions']), set(portable.CALENDAR_PERMISSIONS))
        self.assertEqual(self.broker.capabilities['calendar'].principal.permissions, frozenset({'read'}))
        self.assertIsNot(self.broker.capabilities['review'].authority, self.broker.capabilities['calendar'].authority)

    def test_08_http_cannot_issue_or_confirm(self):
        for endpoint in ('pair/review', 'issue', 'approve'):
            self.assertEqual(self.client.post('/__s4b_host__/'+endpoint, json={'approved': True}, headers=BASE).status_code, 404)
        self.assertEqual(self.client.post('/__s4b_host__/pending/review',
            json={'permissions': ['resolve']}, headers=BASE).status_code, 403)

    def test_09_pending_cookie_is_private_and_not_cached(self):
        response = self.pending()
        cookie = response.headers['set-cookie'].lower()
        for required in ('httponly', 'samesite=strict', 'path=/api/review'):
            self.assertIn(required, cookie)
        for forbidden in ('domain=', 'max-age=', 'expires='):
            self.assertNotIn(forbidden, cookie)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_10_nonce_is_workflow_bound(self):
        data = self.pending().json()
        with self.assertRaises(Denied):
            self.broker.confirm_from_console('calendar', data['pairing_nonce'])

    def test_11_nonce_is_single_use(self):
        data = self.pair()
        with self.assertRaises(Denied):
            self.broker.confirm_from_console('review', data['pairing_nonce'])

    def test_12_nonce_expires(self):
        data = self.pending().json()
        clock = self.broker.clock
        self.broker.clock = lambda: clock()+121
        with self.assertRaises(Denied):
            self.broker.confirm_from_console('review', data['pairing_nonce'])

    def test_13_cookie_is_required(self):
        data = self.pair()
        self.client.cookies.clear()
        self.assertEqual(self.session('review', data).status_code, 403)

    def test_14_memory_binding_is_required(self):
        data = self.pair()
        data['transport_binding'] = ''
        self.assertEqual(self.session('review', data).status_code, 403)

    def test_15_wrong_memory_binding_is_denied(self):
        data = self.pair()
        data['transport_binding'] = 'X'*43
        self.assertEqual(self.session('review', data).status_code, 403)

    def test_16_cross_workflow_binding_is_denied(self):
        review = self.pair()
        self.pair('calendar')
        self.assertEqual(self.session('calendar', review).status_code, 403)

    def test_17_cookie_only_cross_port_replay_is_denied(self):
        self.pair()
        self.assertEqual(self.client.get('/api/review/session',
            headers={**BASE, 'X-STAIR-Demo-Transport': 'review'}).status_code, 403)

    def test_18_host_origin_fetch_and_forwarding_boundaries(self):
        data = self.pair()
        for bad in ({'Host':'127.0.0.1:8000'}, {'Origin':'http://localhost:8017'},
                    {'Sec-Fetch-Site':'cross-site'}, {'Sec-Fetch-Mode':'navigate'},
                    {'Sec-Fetch-Dest':'document'}, {'Forwarded':'for=127.0.0.1'},
                    {'X-Forwarded-For':'127.0.0.1'}):
            with self.subTest(header=next(iter(bad))):
                self.assertEqual(self.session('review', data, {**self.headers('review',data), **bad}).status_code, 403)

    def test_19_mutation_requires_origin(self):
        data = self.pair(); headers = self.headers('review', data); headers.pop('Origin')
        self.assertEqual(self.client.post('/api/review/session/end', json={}, headers=headers).status_code, 403)

    def test_20_originless_get_requires_exact_referer(self):
        data = self.pair(); headers = self.headers('review', data); headers.pop('Origin')
        self.assertEqual(self.session('review', data, headers).status_code, 200)
        headers['Referer'] = 'http://127.0.0.1:8000/s4b/'
        self.assertEqual(self.session('review', data, headers).status_code, 403)

    def test_21_actual_peer_must_be_loopback(self):
        with TestClient(self.app, base_url=portable.ORIGIN, client=('192.0.2.7', 1234)) as client:
            self.assertEqual(client.post('/__s4b_host__/pending/review', json={}, headers=BASE).status_code, 403)

    def test_22_caller_cannot_supply_a_bearer(self):
        data = self.pair()
        self.assertEqual(self.session('review', data,
            {**self.headers('review',data), 'Authorization':'Bearer synthetic-forgery'}).status_code, 403)

    def test_23_other_issuer_bearer_is_denied(self):
        data = self.pair(); capability = self.broker.capabilities['review']; token = capability.bearer
        try:
            capability.bearer = self.broker.capabilities['calendar'].bearer
            self.assertEqual(self.session('review', data).status_code, 403)
        finally:
            capability.bearer = token

    def test_24_expired_grant_is_denied(self):
        data = self.pair(); capability = self.broker.capabilities['review']
        capability.authority.clock = lambda: capability.principal.expires_at+timedelta(seconds=1)
        self.assertEqual(self.session('review', data).status_code, 403)

    def test_25_revoked_grant_is_denied(self):
        data = self.pair(); capability = self.broker.capabilities['review']
        capability.authority.revoke(capability.principal)
        self.assertEqual(self.session('review', data).status_code, 403)

    def test_26_ending_one_workflow_preserves_the_other(self):
        review, calendar = self.pair(), self.pair('calendar')
        response = self.client.post('/api/review/session/end', json={}, headers=self.headers('review',review))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Max-Age=0', response.headers['set-cookie'])
        self.assertEqual(self.session('review', review).status_code, 403)
        self.assertEqual(self.session('calendar', calendar).status_code, 200)

    def test_27_replacement_requires_new_console_confirmation(self):
        first = self.pair(); cookie = self.client.cookies.get(COOKIE['review'])
        second = self.pending().json()
        self.assertEqual(self.session('review',second).status_code, 403)
        self.broker.confirm_from_console('review',second['pairing_nonce'])
        self.assertEqual(self.session('review',second).status_code, 200)
        with self.assertRaises(Denied):
            self.broker.authenticate('review',cookie,first['transport_binding'])

    def test_28_secrets_do_not_enter_safe_responses_or_audit(self):
        data = self.pair()
        safe = self.session('review',data).text+repr(self.events)+repr(self.broker)
        secrets = [data['transport_binding'],self.client.cookies.get(COOKIE['review'])]
        secrets += [cap.bearer for cap in self.broker.capabilities.values()]
        self.assertTrue(all(value not in safe for value in secrets), 'Credential redaction failed')

    def test_29_route_query_and_duplicate_header_denials(self):
        data = self.pair(); headers = self.headers('review',data)
        for path in ('/api/review/grants','/api/review/session?token=forged','/api/review/session/'):
            self.assertEqual(self.client.get(path,headers=headers,follow_redirects=False).status_code,403)
        duplicate = list(headers.items())+[('Origin',portable.ORIGIN)]
        self.assertEqual(self.client.get('/api/review/session',headers=duplicate).status_code,403)

    def test_30_upload_is_bound_to_frozen_pdf(self):
        headers = {**BASE,'Content-Type':'application/pdf','X-Filename':'S4B-G01-review.pdf'}
        self.assertEqual(self.client.post('/api/extract',content=portable.review_pdf(),headers=headers).status_code,200)
        self.assertEqual(self.client.post('/api/extract',content=b'other bytes',headers=headers).status_code,403)
        self.assertEqual(self.client.post('/api/intranet/connect',json={},headers=BASE).status_code,403)

    def test_31_review_source_hash_cannot_be_substituted(self):
        data = self.pair(); body = portable.review_assessment(self.app)
        body['source_packet']['source']['content_sha256'] = '0'*64
        self.assertEqual(self.client.post('/api/review/ingest',json=body,
            headers=self.headers('review',data)).status_code,403)

    def test_32_calendar_catalogue_cannot_be_substituted(self):
        data = self.pair('calendar')
        self.assertEqual(self.client.post('/api/calendar/v1/test-only/preview',
            json={'fixture_id':'ordinary-upload'},headers=self.headers('calendar',data)).status_code,404)

    def test_33_failure_injection_requires_exact_fixture(self):
        from backend.calendar.workflow_api import ApprovalRequest
        from backend.calendar.models import CalendarError
        actor = self.broker.capabilities['calendar'].principal
        bridge = self.app.state.s3c_calendar_bridge
        plan = bridge.preview('base-v1',actor)
        body = {key:getattr(plan,key) for key in ('preview_digest','input_digest','snapshot_digest','target_sandbox_id')}
        body.update(operation_keys=tuple(o.operation_key for o in plan.operations
            if o.action in {'CREATE','UPDATE','NO_OP'}),owner_confirmation=True)
        approval = bridge.approve(ApprovalRequest(**body),actor)
        with self.assertRaises(CalendarError) as error:
            bridge.execute(approval.approval_id,actor,'TIMEOUT_BEFORE')
        self.assertEqual(error.exception.code,'S4B_FAILURE_FIXTURE_REQUIRED')

    def test_34_local_execution_replay_timeout_and_reconciliation(self):
        from backend.calendar.workflow_api import ApprovalRequest
        actor = self.broker.capabilities['calendar'].principal
        bridge = self.app.state.s3c_calendar_bridge
        def approve(fixture):
            plan = bridge.preview(fixture,actor)
            body = {key:getattr(plan,key) for key in ('preview_digest','input_digest','snapshot_digest','target_sandbox_id')}
            body.update(operation_keys=tuple(o.operation_key for o in plan.operations
                if o.action in {'CREATE','UPDATE','NO_OP'}),owner_confirmation=True)
            return bridge.approve(ApprovalRequest(**body),actor)
        approval = approve('base-v1')
        bridge.execute(approval.approval_id,actor)
        bridge.execute(approval.approval_id,actor)
        self.assertEqual(len(bridge.sandbox.snapshot(actor).entries),2)
        update = approve('update-v2')
        bridge.execute(update.approval_id,actor)
        failure = approve('failure-v3')
        outcome = bridge.execute(failure.approval_id,actor,'TIMEOUT_BEFORE')
        self.assertIn('OUTCOME_UNKNOWN', {item.status for item in outcome})
        reconciled = bridge.reconcile(failure.approval_id,actor)
        self.assertNotIn('OUTCOME_UNKNOWN', {item.status for item in reconciled})
        self.assertEqual(len(bridge.sandbox.snapshot(actor).entries),2)
