"""Invented source readings only: personal confirmation cannot resolve OCR ambiguity."""
from datetime import date
from unittest.mock import patch
import unittest
import os
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from backend.models.event import CandidateAlternative
from backend.review.models import ReviewTask, ReviewError
from backend.student import workflow
import test_student_accounts as base


class CalendarSourceSupportTests(unittest.TestCase):
    setUp = base.StudentAccountTests.setUp
    login = base.StudentAccountTests.login
    enroll = base.StudentAccountTests.enroll
    schedule = base.StudentAccountTests.schedule
    plan = base.StudentAccountTests.plan
    approve = base.StudentAccountTests.approve

    def fixture(self):
        self.login()
        record, _ = self.enroll("SYN101 Monday 2026-11-23 09:00-10:00 Lecture Room: SYN1")
        return ReviewTask.model_validate(record)

    def alter(self, task, field, readings):
        event = task.current_event
        observations = []
        for observation in event.revision.observations:
            if observation.field_name == field:
                observation = observation.model_copy(update={'alternatives': tuple(
                    CandidateAlternative(raw_value=raw, normalized_value=value,
                        evidence_ids=observation.evidence_ids) for raw, value in readings)})
            observations.append(observation)
        return task.model_copy(update={'current_event': event.model_copy(update={
            'revision': event.revision.model_copy(update={'observations': tuple(observations)})})})

    def checked_schedule(self, task):
        return workflow.Schedule(task_id=str(task.task_id),
            revision_id=str(task.current_event.revision.revision_id), frequency='ONCE',
            first_date=date(2026, 11, 23), timezone='Asia/Kuala_Lumpur', checked_original=True)

    def blocked(self, task):
        with self.assertRaises(ReviewError) as error:
            workflow.occurrences(task, self.checked_schedule(task), 'synthetic-account')
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('Edit class', error.exception.code)

    def test_unknown_time_alternatives_cannot_be_confirmed_by_a_checkbox(self):
        self.blocked(self.alter(self.fixture(), 'start_time', [('9 AM', None), ('10 AM', None)]))

    def test_single_conflicting_time_alternative_is_rejected(self):
        self.blocked(self.alter(self.fixture(), 'end_time', [('other time', '23:00')]))

    def test_uncertain_course_identity_is_rejected(self):
        self.blocked(self.alter(self.fixture(), 'course_code', [('SYNTHETIC?', None)]))

    def test_uncertain_venue_is_not_written_as_a_certain_location(self):
        self.blocked(self.alter(self.fixture(), 'location', [('SYNTHETIC ROOM?', None)]))

    def test_uncertain_source_date_is_not_replaced_by_personal_confirmation(self):
        self.blocked(self.alter(self.fixture(), 'date', [('unreadable date', None)]))

    def test_equivalent_plain_and_json_readings_remain_supported(self):
        task = self.fixture()
        value = task.current_event.revision.content.start_time
        task = self.alter(task, 'start_time', [('printed clock', '"'+value+'"'), (value, value)])
        self.assertEqual(len(workflow.occurrences(task, self.checked_schedule(task), 'synthetic-account')), 1)

    def test_missing_time_observation_is_rejected(self):
        task = self.fixture(); event = task.current_event
        observations = tuple(o for o in event.revision.observations if o.field_name != 'start_time')
        task = task.model_copy(update={'current_event': event.model_copy(update={
            'revision': event.revision.model_copy(update={'observations': observations})})})
        self.blocked(task)

    def test_readable_source_does_not_become_registered_accept(self):
        task = self.fixture()
        self.assertEqual(len(workflow.occurrences(task, self.checked_schedule(task), 'synthetic-account')), 1)
        self.assertFalse(task.decision.registered_routing_eligible)
        self.assertNotEqual(task.decision.decision.value, 'ACCEPT')

    def test_http_rejection_keeps_saved_calendar_and_check_state_unchanged(self):
        task = self.alter(self.fixture(), 'start_time', [('unreadable', None)])
        before = self.client.get('/api/student/workspace').json()
        with patch.object(workflow, 'owned_task', return_value=(None, task)):
            response = self.client.post('/api/student/schedule',
                json=self.checked_schedule(task).model_dump(mode='json'), headers=self.headers)
        self.assertEqual(response.status_code, 409)
        after = self.client.get('/api/student/workspace').json()
        self.assertEqual(before['schedules'], after['schedules'])
        self.assertEqual(before['entries'], after['entries'])

    def test_approval_cannot_execute_when_source_readings_become_uncertain(self):
        task = self.fixture(); plan = self.plan(task.model_dump(mode='json'))
        self.assertEqual(self.approve(plan).status_code, 200)
        uncertain = self.alter(task, 'start_time', [('unknown reading', None)])
        with patch.object(workflow, 'owned_task', return_value=(None, uncertain)):
            response = self.client.post('/api/student/calendar/execute',
                json={'plan_id': plan['id']}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})


    def test_completed_history_does_not_permanently_exhaust_preview_capacity(self):
        task = self.fixture(); self.schedule(task.model_dump(mode='json'))
        ids = []
        for _ in range(50):
            response = self.client.post('/api/student/calendar/preview',
                json={'task_ids': [str(task.task_id)], 'target': 'local'}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            plan = response.json(); ids.append(plan['id'])
            self.assertEqual(self.approve(plan).status_code, 200)
            self.assertEqual(self.client.post('/api/student/calendar/execute',
                json={'plan_id': plan['id']}, headers=self.headers).status_code, 200)
        response = self.client.post('/api/student/calendar/preview',
            json={'task_ids': [str(task.task_id)], 'target': 'local'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['operations'][0]['action'], 'NO_OP')
        state = self.client.get('/api/student/workspace').json()
        self.assertEqual(len(state['entries']), 1)
        self.assertTrue(set(ids) <= {plan['id'] for plan in state['plans']})

    def test_active_preview_capacity_remains_bounded(self):
        task = self.fixture(); self.schedule(task.model_dump(mode='json'))
        for _ in range(50):
            response = self.client.post('/api/student/calendar/preview',
                json={'task_ids': [str(task.task_id)], 'target': 'local'}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/student/calendar/preview',
            json={'task_ids': [str(task.task_id)], 'target': 'local'}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        state = self.client.get('/api/student/workspace').json()
        self.assertEqual(state['entries'], {})
        self.assertEqual(len(state['plans']), 50)


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI is required for the real default host')
class DefaultStudentCalendarTests(unittest.TestCase):
    def test_normal_host_blocks_unregistered_output_without_a_mode_override(self):
        from backend.student.api import build_student_app
        from backend.review.secure_directory import user_local_root
        with tempfile.TemporaryDirectory(prefix='STAIR-default-policy-QA-', dir=user_local_root()) as temp:
            case = base.StudentAccountTests(); case.setUp()
            case.app = build_student_app(Path(temp) / 'accounts')
            case.accounts = case.app.state.student_accounts
            case.client = TestClient(case.app, base_url=base.ORIGIN, client=('127.0.0.1', 44000))
            case.login(); task, _ = case.enroll()
            self.assertEqual(case.client.get('/api/student/session').json()['calendar_policy'], 'registered-accept')
            self.assertFalse(task['decision']['registered_routing_eligible'])
            self.assertEqual(case.schedule(task).status_code, 200)
            response = case.client.post('/api/student/calendar/preview',
                json={'task_ids': [task['task_id']], 'target': 'local'}, headers=case.headers)
            self.assertEqual(response.status_code, 409)
            self.assertIn('authentic approved operating point', response.json()['detail'])
            self.assertEqual(case.client.get('/api/student/workspace').json()['entries'], {})


if __name__ == '__main__':
    unittest.main()
