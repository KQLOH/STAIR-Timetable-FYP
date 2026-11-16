"""Synthetic scoring regressions; never real reference labels."""
import unittest
from backend.research.development_compare import score

class DevelopmentComparisonTests(unittest.TestCase):
    def test_duplicate_cannot_match_twice(self):
        s=score([{'x':'A'},{'x':'A'}],[{'x':'A'}],['x'])
        self.assertEqual((s['tp'],s['fp'],s['fn'],s['duplicates']),(1,1,0,1))
    def test_wrong_event_counts_both_fp_and_fn(self):
        s=score([{'x':'B'}],[{'x':'A'}],['x'])
        self.assertEqual((s['tp'],s['fp'],s['fn'],s['f1']),(0,1,1,0))
    def test_missing_event(self):
        self.assertEqual(score([], [{'x':1}],['x'])['fn'],1)
    def test_empty_denominator_is_undefined(self):
        self.assertIsNone(score([],[],['x'])['f1'])
    def test_normalization_does_not_repair_characters(self):
        self.assertEqual(score([{'x':' a  b '}],[{'x':'A B'}],['x'])['tp'],1)
        self.assertEqual(score([{'x':'BOO1'}],[{'x':'B001'}],['x'])['tp'],0)
