"""Synthetic bridge and API checks, NOT authentic ACCEPT or research results.

Positive bridge cases mock the host verifier explicitly. Cryptographic receipt
validation is covered separately by test_registered_routing; no genuine approval
or independently checked reference is fabricated by this test module.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import create_autospec, patch
import unittest

from backend.calendar.testing import SyntheticFixtureAuthority
from backend.models.event import DecisionStatus
from backend.reconstruction.models import digest
from backend.review.models import ReviewError, ReviewTask
from backend.review.registration import OperatingRegistry
from backend.student.accounts import COOKIE
from backend.student.registered_calendar import bind_registered_rows, authentic_registry_available
from backend.student import workflow
import test_student_accounts as base


class RegisteredCalendarBridgeTests(unittest.TestCase):
    def setUp(self):
        base.StudentAccountTests.setUp(self)
        self.login = lambda: base.StudentAccountTests.login(self)
        self.login()
        task, _ = base.StudentAccountTests.enroll(self)
        self.task = ReviewTask.model_validate(task)
        authority = SyntheticFixtureAuthority(SimpleNamespace(snapshot_scope='TEST_ONLY'))
        self.bundle = authority.make(changes={'recurrence': {'frequency': 'ONCE', 'source_rule': 'invented one-off'}})
        event = self.bundle.packet.events[0]
        decision = self.task.decision.model_copy(update={
            'candidate_id': event.candidate_id, 'event_id': event.identity.event_id,
            'revision_id': event.revision.revision_id, 'revision_digest': digest(event.revision.model_dump(mode='json')),
            'decision': DecisionStatus.ACCEPT, 'registered_routing_eligible': True,
            'policy_profile': 'm5-registered-v1',
            'registration_source_digest': digest(self.bundle.packet.source.model_dump(mode='json')),
            'registration_evidence_digest': digest([s.model_dump(mode='json') for s in self.bundle.packet.evidence]),
            'registration_mode': 'AUTHENTIC', 'registration_id': '1'*64, 'registration_signature': '2'*64})
        self.task = self.task.model_copy(update={'current_event': event, 'decision': decision})
        self.snapshot = {'packet': self.bundle.packet.model_dump(mode='json'),
                         'tasks': [self.task.model_dump(mode='json')],
                         'reconstruction_status': 'COMPLETE_FINITE_SEARCH',
                         'reconstruction_context': {'timezone_authority_ids': self.bundle.context.timezone_evidence_ids}}
        self.store = SimpleNamespace(snapshot=lambda *args: deepcopy(self.snapshot))
        self.registry = create_autospec(OperatingRegistry, instance=True)
        self.registry.active = True
        self.registry.mode = 'AUTHENTIC'
        self.registry.verifies.return_value = True
        self.session = self.accounts.authenticate(self.client.cookies.get(COOKIE))
        schedule = workflow.Schedule(task_id=str(self.task.task_id), revision_id=str(event.revision.revision_id),
            frequency='ONCE', first_date=event.revision.content.date,
            timezone=event.revision.content.source_timezone, checked_original=True)
        self.rows = workflow.occurrences(self.task, schedule, self.session.user_id)

    def bind(self):
        return bind_registered_rows(self.registry, self.store, self.session, self.task, self.rows)

    def test_current_receipt_and_source_backed_dates_are_bound_to_rows(self):
        result = self.bind()
        self.assertEqual(result[0]['registered_accept']['decision_id'], str(self.task.decision.decision_id))
        self.assertEqual(result[0]['registered_accept']['revision_digest'], self.task.decision.revision_digest)
        self.registry.verifies.assert_called_once()
        self.assertNotIn('registered_accept', self.rows[0])

    def test_missing_inactive_and_synthetic_registry_cannot_authorize_output(self):
        for registry in (None, object()):
            with self.assertRaises(ReviewError):
                bind_registered_rows(registry, self.store, self.session, self.task, self.rows)
        for active, mode in ((False, 'AUTHENTIC'), (True, 'TEST_ONLY'), (True, 'NONE')):
            self.registry.active, self.registry.mode = active, mode
            self.assertFalse(authentic_registry_available(self.registry))
            with self.assertRaises(ReviewError): self.bind()

    def test_review_and_abstain_are_not_accept_even_with_a_valid_receipt(self):
        for route in ('REVIEW', 'ABSTAIN'):
            self.snapshot['tasks'][0]['decision']['decision'] = route
            self.snapshot['tasks'][0]['decision']['registered_routing_eligible'] = False
            with self.assertRaises(ReviewError): self.bind()

    def test_stale_or_revoked_receipt_fails_closed(self):
        self.registry.verifies.return_value = False
        with self.assertRaises(ReviewError): self.bind()

    def test_test_only_receipt_cannot_be_promoted_by_host_mode(self):
        self.snapshot['tasks'][0]['decision']['registration_mode'] = 'TEST_ONLY'
        with self.assertRaises(ReviewError): self.bind()

    def test_incomplete_search_cannot_reach_output(self):
        self.snapshot['reconstruction_status'] = 'SEARCH_INCOMPLETE'
        with self.assertRaises(ReviewError): self.bind()

    def test_missing_timezone_authority_cannot_be_replaced_by_personal_confirmation(self):
        self.snapshot['reconstruction_context']['timezone_authority_ids'] = ()
        with self.assertRaisesRegex(ReviewError, 'TIMEZONE_UNRESOLVED'): self.bind()

    def test_recurring_date_outside_the_accepted_source_is_blocked(self):
        self.rows[0]['body']['start']['dateTime'] = '2026-01-12T09:00:00+00:00'
        self.rows[0]['body']['end']['dateTime'] = '2026-01-12T10:00:00+00:00'
        with self.assertRaisesRegex(ReviewError, 'exceed'): self.bind()

    def test_revision_mismatch_and_failed_constraints_are_blocked(self):
        self.rows[0]['revision_id'] = 'old-invented-revision'
        with self.assertRaises(ReviewError): self.bind()
        self.rows[0]['revision_id'] = str(self.task.current_event.revision.revision_id)
        self.snapshot['tasks'][0]['current_event']['validation']['status'] = 'FAILED'
        with self.assertRaises(ReviewError): self.bind()


class RegisteredCalendarLifecycleTests(unittest.TestCase):
    setUp = base.StudentAccountTests.setUp
    login = base.StudentAccountTests.login
    enroll = base.StudentAccountTests.enroll
    schedule = base.StudentAccountTests.schedule
    plan = base.StudentAccountTests.plan
    approve = base.StudentAccountTests.approve
    execute = base.StudentAccountTests.execute

    def test_strict_api_requires_real_evidence_and_does_not_save_entries(self):
        self.accounts.calendar_policy = 'registered-accept'
        self.login(); task, _ = self.enroll()
        self.assertEqual(self.schedule(task).status_code, 200)
        result = self.client.post('/api/student/calendar/preview', headers=self.headers,
            json={'task_ids': [task['task_id']], 'target': 'local'})
        self.assertEqual(result.status_code, 409)
        self.assertIn('authentic approved', result.json()['detail'])
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})
        info = self.client.get('/api/student/session').json()
        self.assertEqual(info['calendar_policy'], 'registered-accept')
        self.assertFalse(info['research_accept_available'])

    def test_policy_change_invalidates_earlier_personal_preview(self):
        self.login(); task, _ = self.enroll(); plan = self.plan(task)
        self.accounts.calendar_policy = 'registered-accept'
        self.assertEqual(self.approve(plan).status_code, 409)
        self.assertEqual(self.execute(plan).status_code, 409)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})

    def test_receipt_is_rechecked_at_preview_approval_and_execution(self):
        self.login(); task, _ = self.enroll()
        self.accounts.calendar_policy = 'registered-accept'
        # Explicit mock of the bridge, not an authentic operating point.
        def bind(registry, store, session, current, rows, **options):
            return [{**row, 'registered_accept': {'mock_receipt': 'invented-only'}} for row in rows]
        with patch('backend.student.registered_calendar.bind_registered_rows', side_effect=bind) as gate:
            plan = self.plan(task)
            self.assertEqual(self.approve(plan).status_code, 200)
            gate.side_effect = ReviewError('Invented receipt revoked')
            self.assertEqual(self.execute(plan).status_code, 409)
            self.assertEqual(gate.call_count, 3)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})

    def test_strict_output_refuses_external_calendar(self):
        self.accounts.calendar_policy = 'registered-accept'
        self.login()
        session = self.accounts.authenticate(self.client.cookies.get(COOKIE))
        with self.assertRaisesRegex(ReviewError, 'project-local'):
            workflow.local_preview(self.accounts, session, [], target='google:invented', remote=object())

    def test_changed_receipt_requires_new_preview_before_approval(self):
        self.login(); task, _ = self.enroll()
        self.accounts.calendar_policy = 'registered-accept'
        receipt = {'version': 1}
        def bind(registry, store, session, current, rows, **options):
            return [{**row, 'registered_accept': dict(receipt)} for row in rows]
        with patch('backend.student.registered_calendar.bind_registered_rows', side_effect=bind):
            plan = self.plan(task)
            receipt['version'] = 2
            self.assertEqual(self.approve(plan).status_code, 409)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})

    def test_strict_success_still_requires_explicit_approval_and_retains_policy(self):
        self.login(); task, _ = self.enroll()
        self.accounts.calendar_policy = 'registered-accept'
        def bind(registry, store, session, current, rows, **options):
            return [{**row, 'registered_accept': {'mock_receipt': 'invented-only'}} for row in rows]
        with patch('backend.student.registered_calendar.bind_registered_rows', side_effect=bind):
            plan = self.plan(task)
            self.assertEqual(self.execute(plan).status_code, 409)
            self.assertEqual(self.approve(plan).status_code, 200)
            self.assertEqual(self.execute(plan).json()['status'], 'SUCCEEDED')
            self.assertEqual(self.execute(plan).json()['status'], 'SUCCEEDED')
        workspace = self.client.get('/api/student/workspace').json()
        self.assertEqual(len(workspace['entries']), 1)
        self.assertEqual(next(iter(workspace['calendar_metadata'].values()))['calendar_policy'], 'registered-accept')

    def test_google_http_target_is_rejected_before_target_access(self):
        self.accounts.calendar_policy = 'registered-accept'
        self.login(); task, _ = self.enroll()
        result = self.client.post('/api/student/calendar/preview', headers=self.headers,
            json={'task_ids': [task['task_id']], 'target': 'google'})
        self.assertEqual(result.status_code, 409)
        self.assertIn('project-local', result.json()['detail'])


if __name__ == '__main__':
    unittest.main()
