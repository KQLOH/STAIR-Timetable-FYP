"""Synthetic batch scheduling: shared settings, partial failures and exact approval."""
import unittest
from uuid import uuid4

import test_student_accounts as account_tests
from test_m5_policy import fixture

MONDAY = 'CS101 Monday 2026-11-23 09:00-11:00 Lecture Room: A1'
TUESDAY = 'CS102 Tuesday 2026-11-24 13:00-14:00 Tutorial Room: A2'


class BatchScheduleTests(unittest.TestCase):
    setUp = account_tests.StudentAccountTests.setUp
    login = account_tests.StudentAccountTests.login

    def enroll_many(self, *texts):
        request, _ = fixture(*texts)
        source = request.source_packet.source
        response = self.client.post('/api/student/source', content='\n'.join(texts).encode(), headers={**self.headers,
            'X-STAIR-Source-ID': source.source_document_id, 'X-STAIR-Source-SHA256': source.content_sha256})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post('/api/review/ingest', json=request.model_dump(mode='json'), headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return [self.client.get('/api/review/tasks/' + task_id).json()['task'] for task_id in response.json()['task_ids']]

    def batch(self, tasks, **settings):
        return self.client.post('/api/student/schedules/batch', headers=self.headers, json={
            'classes': [{'task_id': t['task_id'], 'revision_id': t['current_event']['revision']['revision_id']} for t in tasks],
            'frequency': 'ONCE', 'timezone': 'Asia/Kuala_Lumpur', 'checked_original': True, **settings})

    def test_source_dates_used_for_whole_selection_without_notes_or_individual_confirmations(self):
        self.login(); tasks = self.enroll_many(MONDAY, TUESDAY)
        response = self.batch(tasks)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['ready_task_ids']), 2)
        self.assertEqual(response.json()['blocked'], [])
        workspace = self.client.get('/api/student/workspace').json()
        schedules = list(workspace['schedules'].values())
        self.assertEqual({s['first_date'] for s in schedules}, {'2026-11-23', '2026-11-24'})
        self.assertTrue(all(s['source_note'] == '' for s in schedules))
        self.assertEqual(workspace['entries'], {})
        self.assertFalse(response.json()['write_approved'])
        self.assertFalse(response.json()['independently_verified'])

    def test_shared_weekly_range_preserves_each_weekday_and_matching_exceptions(self):
        self.login(); tasks = self.enroll_many(MONDAY, TUESDAY)
        result = self.batch(tasks, frequency='WEEKLY', starts_on='2026-11-23', ends_on='2026-12-08',
                            excluded_dates=['2026-12-01'], source_note='g')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(result.json()['ready_task_ids']), 2)
        preview = self.client.post('/api/student/calendar/preview', headers=self.headers,
            json={'task_ids': result.json()['ready_task_ids'], 'target': 'local'})
        self.assertEqual(preview.status_code, 200, preview.text)
        dates = [op['body']['start']['dateTime'][:10] for op in preview.json()['operations']]
        self.assertEqual(set(dates), {'2026-11-23', '2026-11-24', '2026-11-30', '2026-12-07', '2026-12-08'})

    def test_missing_date_is_blocked_individually_while_known_date_remains_ready(self):
        self.login(); tasks = self.enroll_many(MONDAY, 'CS102 Tuesday 13:00-14:00 Tutorial Room: A2')
        result = self.batch(tasks).json()
        self.assertEqual(len(result['ready_task_ids']), 1)
        self.assertEqual(len(result['blocked']), 1)
        self.assertIn('date is missing', result['blocked'][0]['message'])

    def test_only_missing_date_can_be_supplied_explicitly(self):
        self.login(); tasks = self.enroll_many('CS101 Monday 09:00-11:00 Lecture Room: A1')
        item = {'task_id': tasks[0]['task_id'], 'revision_id': tasks[0]['current_event']['revision']['revision_id'],
                'first_date': '2026-11-23'}
        response = self.batch(tasks, classes=[item])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['ready_task_ids']), 1)

    def test_conflicting_override_does_not_replace_source_date(self):
        self.login(); tasks = self.enroll_many(MONDAY)
        item = {'task_id': tasks[0]['task_id'], 'revision_id': tasks[0]['current_event']['revision']['revision_id'],
                'first_date': '2026-11-30'}
        result = self.batch(tasks, classes=[item]).json()
        self.assertEqual(result['ready_task_ids'], [])
        self.assertIn('differs from the saved source', result['blocked'][0]['message'])

    def test_stale_revision_is_excluded_without_approving_other_records(self):
        self.login(); tasks = self.enroll_many(MONDAY, TUESDAY)
        items = [{'task_id': t['task_id'], 'revision_id': t['current_event']['revision']['revision_id']} for t in tasks]
        items[0]['revision_id'] = str(uuid4())
        response = self.batch(tasks, classes=items)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['ready_task_ids']), 1)
        self.assertEqual(response.json()['blocked'][0]['task_id'], tasks[0]['task_id'])

    def test_common_required_fields_and_repeat_limits_remain_checked(self):
        self.login(); tasks = self.enroll_many(MONDAY)
        for settings in ({'checked_original': False}, {'timezone': 'Mars/Unknown'},
                         {'frequency': 'WEEKLY'}, {'frequency': 'WEEKLY', 'starts_on': '2026-11-23', 'ends_on': '2028-11-23'},
                         {'frequency': 'WEEKLY', 'starts_on': '2026-11-23', 'ends_on': '2026-12-01', 'excluded_dates': ['2027-01-01']}):
            response = self.batch(tasks, **settings)
            self.assertEqual(response.status_code, 422, response.text)

    def test_batch_needs_exact_preview_approval_and_retains_completed_history(self):
        self.login(); tasks = self.enroll_many(MONDAY, TUESDAY)
        result = self.batch(tasks).json()
        plan = self.client.post('/api/student/calendar/preview', headers=self.headers,
            json={'task_ids': result['ready_task_ids'], 'target': 'local'}).json()
        body = {'plan_id': plan['id']}
        self.assertEqual(self.client.post('/api/student/calendar/execute', headers=self.headers, json=body).status_code, 409)
        self.assertEqual(self.client.post('/api/student/calendar/approve', headers=self.headers,
            json={**body, 'confirmed': True}).status_code, 200)
        self.assertEqual(self.client.post('/api/student/calendar/execute', headers=self.headers, json=body).json()['status'], 'SUCCEEDED')
        self.batch(tasks)
        workspace = self.client.get('/api/student/workspace').json()
        self.assertEqual(len(workspace['entries']), 2)
        self.assertTrue(any(p['id'] == plan['id'] and p['outcome']['status'] == 'SUCCEEDED' for p in workspace['plans']))

    def test_same_account_other_source_cannot_be_mixed_into_one_batch(self):
        self.login(); first = self.enroll_many(MONDAY); second = self.enroll_many(TUESDAY)
        response = self.batch(first + second)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/api/student/workspace').json()['schedules'], {})

    def test_batch_edit_invalidates_old_pending_approval(self):
        self.login(); tasks = self.enroll_many(MONDAY)
        self.batch(tasks)
        plan = self.client.post('/api/student/calendar/preview', headers=self.headers,
            json={'task_ids': [tasks[0]['task_id']], 'target': 'local'}).json()
        self.client.post('/api/student/calendar/approve', headers=self.headers, json={'plan_id': plan['id'], 'confirmed': True})
        self.batch(tasks, frequency='WEEKLY', starts_on='2026-11-23', ends_on='2026-11-30')
        response = self.client.post('/api/student/calendar/execute', headers=self.headers, json={'plan_id': plan['id']})
        self.assertEqual(response.status_code, 404)

    def test_weekly_conflicting_classes_are_excluded_but_unrelated_class_is_ready(self):
        self.login(); tasks = self.enroll_many(MONDAY,
            'CS103 Monday 2026-11-30 10:00-12:00 Lecture Room: A3', TUESDAY)
        response = self.batch(tasks, frequency='WEEKLY', starts_on='2026-11-23', ends_on='2026-12-08')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(len(result['ready_task_ids']), 1)
        self.assertEqual(len(result['blocked']), 2)
        self.assertTrue(all('Overlaps' in b['message'] for b in result['blocked']))


if __name__ == '__main__':
    unittest.main()
