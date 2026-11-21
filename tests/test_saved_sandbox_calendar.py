"""Account-owned saved-calendar metadata and synthetic JS calendar checks."""
from pathlib import Path
import shutil
import subprocess
import unittest

import test_student_accounts as accounts_tests


class SavedCalendarTests(unittest.TestCase):
    setUp = accounts_tests.StudentAccountTests.setUp
    login = accounts_tests.StudentAccountTests.login
    enroll = accounts_tests.StudentAccountTests.enroll
    schedule = accounts_tests.StudentAccountTests.schedule
    plan = accounts_tests.StudentAccountTests.plan
    approve = accounts_tests.StudentAccountTests.approve
    execute = accounts_tests.StudentAccountTests.execute

    def test_only_applied_local_events_have_saved_metadata(self):
        self.login(); task, _ = self.enroll(); plan = self.plan(task)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})
        self.assertEqual(self.client.get('/api/student/workspace').json()['calendar_metadata'], {})
        self.approve(plan)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'], {})
        self.assertEqual(self.client.get('/api/student/workspace').json()['calendar_metadata'], {})
        self.execute(plan)
        workspace = self.client.get('/api/student/workspace').json()
        key = next(iter(workspace['entries']))
        meta = workspace['calendar_metadata'][key]
        self.assertEqual(meta['task_id'], task['task_id'])
        self.assertEqual(meta['revision_id'], task['current_event']['revision']['revision_id'])
        self.assertEqual(meta['current_revision_id'], meta['revision_id'])
        self.assertGreater(meta['saved_at'], 0)

    def test_legacy_entries_do_not_get_guessed_source_links(self):
        self.login(); task, _ = self.enroll(); plan = self.plan(task)
        self.approve(plan); self.execute(plan)
        session = self.accounts.authenticate(self.client.cookies.get('stair_student_session'))
        with self.accounts.transaction(write=True) as state:
            state['workspaces'][session.user_id]['plans'] = {}
        workspace = self.client.get('/api/student/workspace').json()
        self.assertEqual(len(workspace['entries']), 1)
        self.assertEqual(workspace['calendar_metadata'], {})

    def test_calendar_assets_are_loaded_before_account_controller(self):
        html = self.client.get('/').text
        self.assertLess(html.index('/js/sandbox-calendar.js'), html.index('/js/student-account.js'))
        self.assertIn('/sandbox-calendar.css', html)
        for url in ('/js/sandbox-calendar.js', '/sandbox-calendar.css'):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_synthetic_calendar_date_projection_and_layout(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the calendar rendering checks.')
        result = subprocess.run([node, str(Path(__file__).with_name('sandbox_calendar_checks.cjs'))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"passed":20', result.stdout)


if __name__ == '__main__':
    unittest.main()
