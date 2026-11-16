"""Synthetic regression for source-preserving room spacing; no private values."""
import unittest
from tests.test_four_line_mapping import fixture,values
from tests.test_rt3_blocks import wrapped_fixture
from backend.layout.block_grid import block_graph
class SourceVenueSpacingTests(unittest.TestCase):
    def test_explicit_room_word_boundary_survives(self):
        p,s=fixture(['QA1701 (T)','ZX 482','Alex River','9:00 AM - 11:00 AM'])
        self.assertEqual(values(p,s)[0]['location'],'ZX 482')
        graph,_,_=block_graph(p,s)
        nodes=[n for g in graph.groups for n in g.domains['location'] if n.value=='ZX 482']
        self.assertTrue(nodes)
        self.assertTrue(any('preserve-source-word-boundaries' in t for n in nodes for t in n.transformations))
    def test_compact_code_remains_compact(self):
        p,s=fixture(['QA1701 (T)','ZX482','Alex River','9:00 AM - 11:00 AM'])
        self.assertEqual(values(p,s)[0]['location'],'ZX482')
    def test_wrapped_source_roles_preserve_room_spacing(self):
        p,s=wrapped_fixture(['QA1701','(T)','ZX 482','Alex','River','9:00 AM -','11:00 AM'])
        self.assertEqual(values(p,s)[0]['location'],'ZX 482')
    def test_ocr_letter_confusion_is_not_fixed_by_spacing(self):
        p,s=fixture(['QA1701 (T)','ZXO82','Alex River','9:00 AM - 11:00 AM'])
        self.assertNotEqual(values(p,s)[0]['location'],'ZX082')
