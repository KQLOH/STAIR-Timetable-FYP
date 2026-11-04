"""Student review regressions use invented records and the real frontend module."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class StudentReviewTests(unittest.TestCase):
    def check(self, name):
        run = subprocess.run(['node', str(ROOT/'tests/ui_review_checks.js'), name], cwd=ROOT,
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_counts(self): self.check('counts')
    def test_distinct_sessions_and_duplicate_records(self): self.check('distinct')
    def test_filters_and_lecturer_search(self): self.check('filters')
    def test_chronological_order_and_missing_values(self): self.check('sorting')
    def test_evidence_hidden_but_accessible(self): self.check('evidence_hidden')
    def test_safe_text_and_no_fake_actions(self): self.check('safe_text')
    def test_source_region_binding_and_missing_coordinates(self): self.check('region_binding')
    def test_verification_comes_only_from_records(self): self.check('verification')

if __name__ == '__main__': unittest.main()
