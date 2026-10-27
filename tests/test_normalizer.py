"""Synthetic examples for the selected conservative baseline function ports."""

import unittest
from backend.services.normalizer import (
    clean_text, normalize_class_type, normalize_date, normalize_day, normalize_time, normalize_venue,
)


class NormalizerTests(unittest.TestCase):
    def test_known_aliases_and_whitespace(self):
        self.assertEqual(normalize_day(" Mon. "), "MONDAY")
        self.assertEqual(normalize_class_type("(L)"), "Lecture")
        self.assertEqual(normalize_class_type("lab"), "Practical")
        self.assertEqual(clean_text(" TEST\n DATA "), "TEST DATA")

    def test_unknown_values_not_fabricated(self):
        self.assertIsNone(normalize_day("Funday"))
        self.assertIsNone(normalize_class_type("mystery"))
        self.assertIsNone(normalize_time("not a time"))
        self.assertIsNone(normalize_time("123"))
        self.assertIsNone(normalize_time("99"))
        self.assertIsNone(normalize_date("08/09/2026"))
        self.assertIsNone(normalize_date("2026-02-30"))
        self.assertIsNone(clean_text(" "))

    def test_time_formats_and_midnight(self):
        self.assertEqual(normalize_time("1:00 PM"), "13:00")
        self.assertEqual(normalize_time("12:00 AM"), "00:00")
        self.assertEqual(normalize_time("13:00"), "13:00")
        self.assertIsNone(normalize_time("24:00"))
        self.assertEqual(normalize_time("24:00", allow_end_of_day=True), "24:00")

    def test_venue_labels_preserved(self):
        self.assertEqual(normalize_venue("Venue: TEST ROOM"), "Venue: TEST ROOM")


if __name__ == "__main__":
    unittest.main()
