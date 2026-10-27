"""S4B-F1 isolated synthetic frontend regressions; never enroll a live browser."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from tests.s3c_fixtures import workflow_records
from tests.test_m51_integration import NODE

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    'switch_after_approval', 'switching_back_requires_fresh_preview_and_confirmation',
    'late_preview_success', 'late_approval_success', 'late_approval_403', 'late_approval_409',
    'late_approval_transport_failure', 'late_approval_body_decode', 'late_approval_history_success',
    'late_approval_state_success', 'late_approval_history_failure', 'late_connect_history',
    'late_execute_success', 'late_execute_failure', 'current_403_still_ends_session',
    'current_409_still_reports_stale_preview',
)


class S4BF1FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='m6-test-s4bf1-ui-', dir=ROOT/'data/temp')
        cls.records = Path(cls.temp.name)/'records.json'
        cls.records.write_text(json.dumps(workflow_records(Path(cls.temp.name)/'calendar')), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()


def attach(case):
    def test(self):
        result = subprocess.run([NODE, str(ROOT/'tests/s4bf1_frontend_checks.js'), str(self.records), case],
                                cwd=ROOT, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])
        self.assertEqual(json.loads(result.stdout)['status'], 'PASS')
    setattr(S4BF1FrontendTests, 'test_'+case, test)


for case in CASES:
    attach(case)
