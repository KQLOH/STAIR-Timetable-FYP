"""Actual optional host bootstrap with invented source and no real authority."""
from pathlib import Path
import subprocess
import unittest

class LocalReviewFrontendTests(unittest.TestCase):
    def check(self, case):
        root=Path(__file__).resolve().parents[1]
        r=subprocess.run(['node',str(root/'tests/local_review_host_checks.cjs'),case],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_pending_is_not_authorization(self):self.check('pending')
    def test_transport_limits_and_no_storage(self):self.check('transport')
    def test_source_change_requires_new_consent(self):self.check('source_change')
    def test_source_file_required(self):self.check('no_file')
