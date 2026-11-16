"""Synthetic diagnostics must never change the strict comparison predicate."""
import unittest
from backend.research.diagnose_comparison import diagnostic, CORE
from backend.research.development_compare import score
class DiagnosisTests(unittest.TestCase):
    def test_week_absence_is_not_empty_list(self):
        p={'course_code':'SYN1001','teaching_weeks':None};r={'course_code':'SYN1001','teaching_weeks':[]}
        self.assertEqual(score([p],[r],['course_code'])['tp'],1)
        self.assertEqual(score([p],[r],['course_code','teaching_weeks'])['tp'],0)
        self.assertIn('teaching_weeks',diagnostic([p],[r],['course_code','teaching_weeks'])[0]['differences'])
    def test_closest_links_do_not_increase_exact_score(self):
        p={f:'synthetic' for f in CORE};r=dict(p,end_time='different')
        self.assertTrue(diagnostic([p],[r],CORE)[0]['plausible_link'])
        self.assertEqual(score([p],[r],CORE)['tp'],0)
    def test_no_overlap_reports_ties_as_unreliable(self):
        d=diagnostic([{'course_code':'UNKNOWN'}],[{'course_code':'SYN1'},{'course_code':'SYN2'}],['course_code'])[0]
        self.assertFalse(d['plausible_link']);self.assertEqual(d['closest_reference_indices'],[0,1])
        self.assertIn('course_code',d['differences'])
