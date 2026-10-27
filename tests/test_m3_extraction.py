"""Synthetic-only M3 extraction, baseline bridge and failure contract tests."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.models.extraction import BoundingBox as ExtractionBox, DocumentEvidence, EvidencePage, EvidenceRegion
from backend.services import file_extractor
from backend.services.extraction_adapter import adapt_baseline, adapt_extraction


class M3ExtractionTests(TestCase):
    def test_upload_validation_rejects_unknown_empty_and_oversized(self):
        for filename, payload, expected in (
            ("fixture.exe", b"x", "UNSUPPORTED_FILE_TYPE"),
            ("fixture.pdf", b"", "EMPTY_FILE"),
            ("fixture.pdf", b"x" * (file_extractor.MAX_UPLOAD_BYTES + 1), "FILE_TOO_LARGE"),
        ):
            with self.subTest(expected=expected), self.assertRaises(file_extractor.STAIRException) as raised:
                file_extractor.extract_document(filename, None, payload)
            self.assertEqual(raised.exception.code, expected)

    def test_native_pdf_extraction_keeps_geometry_and_route(self):
        words = [{"text": "MATH101", "top": 20.0, "bottom": 32.0, "x0": 10.0, "x1": 60.0}]
        page = SimpleNamespace(width=300, height=400, extract_words=lambda **kwargs: words)
        pdf = SimpleNamespace(pages=[page], close=lambda: None)
        with patch.dict("sys.modules", {"pdfplumber": SimpleNamespace(open=lambda _: pdf)}):
            document = file_extractor.extract_document("fixture.pdf", "application/pdf", b"%PDF-synthetic")
        packet = adapt_extraction(document)
        self.assertEqual(document.route_type, "upload_pdf")
        self.assertEqual(packet.source.route, "native_pdf")
        self.assertEqual(packet.evidence[0].route, "native_pdf")
        self.assertEqual(packet.evidence[0].bbox.coordinate_space, "PDF_POINTS")
        self.assertEqual(packet.evidence[0].raw_text, "MATH101")

    def test_image_ocr_unavailable_is_explicit(self):
        from PIL import Image
        with patch.dict("sys.modules", {"pytesseract": None}):
            with self.assertRaises(file_extractor.STAIRException) as raised:
                file_extractor._ocr_image("synthetic", Image.new("RGB", (4, 4)), 1, "synthetic-ocr")
        self.assertEqual(raised.exception.code, "OCR_UNAVAILABLE")
        self.assertEqual(raised.exception.status_code, 503)

    def _ocr_document(self, rows):
        regions = tuple(EvidenceRegion(
            evidence_id=f"ev-{i}", page=1, line=i, text=text,
            adapter="synthetic-ocr", confidence=0.9,
            bbox=ExtractionBox(x0=x0, y0=y0, x1=x1, y1=y1),
        ) for i, (text, x0, y0, x1, y1) in enumerate(rows, 1))
        return DocumentEvidence(
            source_id="src-synthetic", route_type="upload_ocr", original_label="synthetic.png",
            media_type="image/png", checksum="a" * 64, parser_version="test", pages=[
                EvidencePage(page=1, width=500, height=200,
                            text="\n".join(region.text for region in regions), regions=regions)
            ],
        )

    def test_b1_output_maps_to_m1_without_promoting_accept(self):
        document = self._ocr_document([
            ("Monday", 10, 10, 80, 25),
            ("MATH101 09:00-11:00 Room 10 Lecture", 10, 50, 400, 65),
        ])
        packet = adapt_baseline(document, "B1")
        self.assertEqual(len(packet.events), 1)
        event = packet.events[0]
        self.assertEqual(event.revision.content.course_code, "MATH101")
        self.assertEqual(event.revision.content.day_of_week, 1)
        self.assertIsNone(event.policy.decision)
        self.assertEqual(event.human_verification.status, "NOT_VERIFIED")
        self.assertIn("B1_BASELINE_ACCEPT_NOT_VERIFICATION", {issue.code for issue in packet.issues})
        self.assertTrue(event.revision.observations[0].evidence_ids)

    def test_b2_uses_spatial_day_header_and_surfaces_compound_cell(self):
        document = self._ocr_document([
            ("Monday", 50, 10, 100, 25),
            ("MATH101 09:00-11:00; CS201 13:00-15:00", 50, 60, 430, 75),
        ])
        packet = adapt_baseline(document, "B2")
        self.assertTrue(packet.events)
        self.assertEqual(packet.events[0].revision.content.day_of_week, 1)
        self.assertIn("B2_COMPOUND_CELL_REVIEW", {issue.code for issue in packet.issues})
        self.assertLessEqual(len(packet.events), 1)  # frozen B2 source emits a single first-match candidate

    def test_b2_inherits_a_spanning_day_header_by_geometry(self):
        document = self._ocr_document([
            ("Monday", 10, 10, 450, 25),
            ("MATH101 09:00-11:00", 40, 55, 240, 70),
            ("CS201 13:00-15:00", 40, 100, 240, 115),
        ])
        packet = adapt_baseline(document, "B2")
        self.assertEqual(len(packet.events), 2)
        self.assertEqual([event.revision.content.day_of_week for event in packet.events], [1, 1])

    def test_b1_keeps_unobserved_day_explicit(self):
        packet = adapt_baseline(self._ocr_document([
            ("MATH101 09:00-11:00", 10, 50, 300, 65),
        ]), "B1")
        self.assertEqual(len(packet.events), 1)
        event = packet.events[0]
        self.assertIsNone(event.revision.content.day_of_week)
        day = next(item for item in event.revision.observations if item.field_name == "day_of_week")
        self.assertEqual(day.state.value, "MISSING")

    def test_native_pdf_cannot_be_routed_through_b1_by_substitution(self):
        document = DocumentEvidence(
            source_id="src-native", route_type="upload_pdf", original_label="synthetic.pdf",
            media_type="application/pdf", checksum="b" * 64, parser_version="test",
            pages=[EvidencePage(page=1, text="MATH101")],
        )
        with self.assertRaisesRegex(ValueError, "require OCR evidence"):
            adapt_baseline(document, "B1")


if __name__ == "__main__":
    import unittest
    unittest.main()
