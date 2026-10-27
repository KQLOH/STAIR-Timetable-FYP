"""Synthetic documents only; no timetable, dataset, credentials or app imports."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from starter_tools.markdown_links import check_document, check_tree


class SyntheticMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='starter-doc-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text='synthetic example'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf8')
        return path

    def test_relative_file_and_fragment(self):
        self.write('note.md')
        doc = self.write('README.md', '[note](note.md#example)')
        self.assertEqual(check_document(doc, self.root), [])

    def test_missing_file_is_reported(self):
        doc = self.write('README.md', '[missing](absent.md)')
        self.assertEqual(check_document(doc, self.root)[0].reason, 'MISSING_TARGET')

    def test_anchor_only_is_not_a_file_lookup(self):
        doc = self.write('README.md', '[local](#example)')
        self.assertEqual(check_document(doc, self.root), [])

    def test_external_links_are_never_fetched(self):
        doc = self.write('README.md', '[web](https://example.test/missing) [mail](mailto:example@example.test)')
        with patch('socket.create_connection', side_effect=AssertionError('No network allowed')):
            self.assertEqual(check_document(doc, self.root), [])

    def test_escape_outside_root_is_rejected(self):
        doc = self.write('README.md', '[outside](../outside.md)')
        self.assertEqual(check_document(doc, self.root)[0].reason, 'OUTSIDE_ROOT')

    def test_private_directory_is_not_scanned(self):
        self.write('README.md')
        self.write('private/local.md', '[not-public](missing.md)')
        count, issues = check_tree(self.root)
        self.assertEqual((count, issues), (1, []))

    def test_percent_encoded_spaces(self):
        self.write('My Note.md')
        doc = self.write('README.md', '[note](My%20Note.md)')
        self.assertEqual(check_document(doc, self.root), [])

    def test_non_directory_root_is_rejected(self):
        file = self.write('note.md')
        with self.assertRaises(ValueError):
            check_tree(file)


if __name__ == '__main__':
    unittest.main()
