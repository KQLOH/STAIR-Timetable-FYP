"""Exercise the actual upload controller with synthetic invalid replacements."""
from pathlib import Path
import subprocess
import unittest

class StudentUploadPreservationTests(unittest.TestCase):
    def test_invalid_replacement_preserves_source_and_review_work(self):
        root=Path(__file__).resolve().parents[1]
        r=subprocess.run(['node',str(root/'tests/student_upload_preservation.cjs')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
