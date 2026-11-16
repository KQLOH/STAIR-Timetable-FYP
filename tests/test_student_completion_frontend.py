"""Synthetic student correction and preview warnings in actual frontend modules."""
from pathlib import Path
import subprocess
import unittest
class StudentCompletionFrontendTests(unittest.TestCase):
    def test_explicit_human_submission_and_calendar_warnings(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/student_completion_checks.cjs')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
