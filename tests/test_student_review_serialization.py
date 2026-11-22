"""Regression for source review admission through the actual account frontend."""
from pathlib import Path
import subprocess
import unittest


class StudentReviewSerializationTests(unittest.TestCase):
    def test_automatic_enrollment_preserves_original_bound_reconstruction_json(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', str(root/'tests/student_review_serialization_checks.cjs')],
            cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('PASS actual account source-connect preserves bound JSON', result.stdout)
