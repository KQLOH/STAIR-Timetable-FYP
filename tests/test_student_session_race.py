"""Account response lifecycle checks with invented session state only."""
from pathlib import Path
import subprocess
import unittest


class StudentSessionRaceTests(unittest.TestCase):
    def test_stale_responses_do_not_clear_new_sessions_or_expose_old_account_data(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', str(root/'tests/student_session_race_checks.cjs')],
            cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('PASS stale session responses', result.stdout)
