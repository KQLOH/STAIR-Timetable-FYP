"""Synthetic M3.1 tests. Mocked OCR is explicitly distinguished from live OCR."""

from io import BytesIO
import os
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from backend.main import app
from backend.models.extraction import BoundingBox, DocumentEvidence, EvidencePage, EvidenceRegion
from backend.services import file_extractor, ocr_runtime
from backend.services.extraction_adapter import adapt_baseline


def synthetic_image():
    image = Image.new("RGB", (1100, 160), "white")
    font = ImageFont.load_default(size=32)
    ImageDraw.Draw(image).text((20, 30), "CS101 Monday 09:00-11:00 Lecture", fill="black", font=font)
    return image


def image_bytes():
    output = BytesIO()
    synthetic_image().save(output, format="PNG")
    return output.getvalue()


def synthetic_pdf():
    from reportlab.pdfgen.canvas import Canvas
    output = BytesIO()
    canvas = Canvas(output, pagesize=(600, 200))
    canvas.drawString(20, 130, "CS101 Monday 09:00-11:00 Lecture")
    canvas.save()
    return output.getvalue()


def ocr_data(text="CS101 Monday 09:00-11:00 Lecture", score="91"):
    return dict(text=[text], conf=[score], block_num=[1], par_num=[1], line_num=[1],
                left=[20], top=[30], width=[600], height=[35])


def document(*rows):
    regions = [EvidenceRegion(evidence_id=f"ev-{i}", page=1, line=i + 1,
                              group=kwargs.get("group", f"cell-{i}"), text=text, adapter="synthetic-ocr",
                              bbox=BoundingBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]) if box else None,
                              continuation_of=kwargs.get("continuation_of"))
               for i, (text, box, kwargs) in enumerate(rows)]
    return DocumentEvidence(source_id="synthetic", route_type="upload_ocr", original_label="synthetic.png",
                            media_type="image/png", checksum="c" * 64, parser_version="synthetic",
                            pages=[EvidencePage(page=1, width=600, height=300, regions=regions,
                                                text="\n".join(r.text for r in regions))])


class RuntimeTests(unittest.TestCase):
    def test_project_local_executable_setting(self):
        config = SimpleNamespace(is_file=lambda: True, read_text=lambda **kwargs: '{"tesseract_cmd":"synthetic.exe"}')
        with patch.dict(os.environ, {}, clear=True), patch.object(ocr_runtime, "LOCAL_CONFIG_FILE", config):
            self.assertEqual(ocr_runtime.configured_executable(), "synthetic.exe")

    def test_malformed_local_setting_fails_explicitly(self):
        config = SimpleNamespace(is_file=lambda: True, read_text=lambda **kwargs: '{"unexpected":true}')
        with patch.dict(os.environ, {}, clear=True), patch.object(ocr_runtime, "LOCAL_CONFIG_FILE", config):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.configured_executable()
        self.assertEqual(raised.exception.code, "OCR_CONFIGURATION_INVALID")

    def runtime_patches(self, languages="eng\nosd\n"):
        wrapper = SimpleNamespace(__version__="test", pytesseract=SimpleNamespace(tesseract_cmd="old"),
                                  Output=SimpleNamespace(DICT="dict"))
        responses = [SimpleNamespace(stdout="tesseract 5.synthetic\n"),
                     SimpleNamespace(stdout="List of available languages (2):\n" + languages)]
        return wrapper, responses

    def test_runtime_missing_wrapper(self):
        with patch("backend.services.ocr_runtime.importlib.import_module", side_effect=ImportError):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.detect_runtime()
        self.assertEqual(raised.exception.code, "OCR_WRAPPER_MISSING")

    def test_runtime_missing_executable(self):
        wrapper, _ = self.runtime_patches()
        with patch.dict(os.environ, {"STAIR_TESSERACT_CMD": ""}), patch.object(ocr_runtime.importlib, "import_module", return_value=wrapper), patch.object(ocr_runtime.shutil, "which", return_value=None), patch.object(ocr_runtime.Path, "is_file", return_value=False):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.detect_runtime()
        self.assertEqual(raised.exception.code, "OCR_EXECUTABLE_MISSING")

    def test_explicit_bad_path_does_not_fall_back(self):
        with patch.dict(os.environ, {"STAIR_TESSERACT_CMD": "missing-synthetic.exe"}), patch.object(ocr_runtime.Path, "is_file", return_value=False):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.detect_runtime()
        self.assertEqual(raised.exception.code, "OCR_EXECUTABLE_PATH_INVALID")

    def test_verified_runtime_and_language_detection(self):
        wrapper, responses = self.runtime_patches()
        with patch.dict(os.environ, {"STAIR_TESSERACT_CMD": ""}), patch.object(ocr_runtime.importlib, "import_module", return_value=wrapper), patch.object(ocr_runtime.shutil, "which", return_value="synthetic.exe"), patch.object(ocr_runtime.subprocess, "run", side_effect=responses):
            runtime = ocr_runtime.detect_runtime()
        self.assertEqual(runtime.language, "eng")
        self.assertEqual(runtime.config, "--psm 6")

    def test_missing_language_model(self):
        wrapper, responses = self.runtime_patches("osd\n")
        with patch.dict(os.environ, {"STAIR_TESSERACT_CMD": ""}), patch.object(ocr_runtime.importlib, "import_module", return_value=wrapper), patch.object(ocr_runtime.shutil, "which", return_value="synthetic.exe"), patch.object(ocr_runtime.subprocess, "run", side_effect=responses):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.detect_runtime()
        self.assertEqual(raised.exception.code, "OCR_LANGUAGE_MODEL_MISSING")

    def test_runtime_initialization_failure(self):
        with patch.dict(os.environ, {"STAIR_TESSERACT_CMD": ""}), patch.object(ocr_runtime.shutil, "which", return_value="synthetic.exe"), patch.object(ocr_runtime.subprocess, "run", side_effect=OSError):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.detect_runtime()
        self.assertEqual(raised.exception.code, "OCR_INITIALIZATION_FAILED")

    def test_invocation_failure_restores_wrapper_command(self):
        wrapper, _ = self.runtime_patches()
        wrapper.image_to_data = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError())
        runtime = ocr_runtime.OCRRuntime("new", "test", "test", ("eng",))
        with patch.object(ocr_runtime, "detect_runtime", return_value=runtime), patch.object(ocr_runtime.importlib, "import_module", return_value=wrapper):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.recognize(synthetic_image())
        self.assertEqual(raised.exception.code, "OCR_INVOCATION_FAILED")
        self.assertEqual(wrapper.pytesseract.tesseract_cmd, "old")

    def test_unknown_confidence_is_not_zero_or_one(self):
        for score in (None, "-1", "NaN", "inf", "unknown", "101"):
            self.assertIsNone(ocr_runtime.confidence(score))
        self.assertEqual(ocr_runtime.confidence("91.5"), 0.915)
        self.assertEqual(ocr_runtime.confidence("0"), 0)

    def test_invocation_uses_registered_psm_and_preserves_command(self):
        from unittest.mock import Mock
        wrapper, _ = self.runtime_patches()
        wrapper.image_to_data = Mock(return_value=ocr_data())
        runtime = ocr_runtime.OCRRuntime("new", "test", "test", ("eng",))
        with patch.object(ocr_runtime, "detect_runtime", return_value=runtime), patch.object(ocr_runtime.importlib, "import_module", return_value=wrapper):
            data, metadata = ocr_runtime.recognize(synthetic_image())
        self.assertEqual(wrapper.image_to_data.call_args.kwargs["config"], "--psm 6")
        self.assertEqual(wrapper.image_to_data.call_args.kwargs["lang"], "eng")
        self.assertEqual(wrapper.pytesseract.tesseract_cmd, "old")

    def test_invalid_wrapper_columns_fail_explicitly(self):
        from unittest.mock import Mock
        wrapper, _ = self.runtime_patches()
        wrapper.image_to_data = Mock(return_value={"text": ["CS101"]})
        with patch.object(ocr_runtime, "detect_runtime", return_value=ocr_runtime.OCRRuntime("new", "test", "test", ("eng",))), patch.object(ocr_runtime.importlib, "import_module", return_value=wrapper):
            with self.assertRaises(ocr_runtime.OCRRuntimeError) as raised:
                ocr_runtime.recognize(synthetic_image())
        self.assertEqual(raised.exception.code, "OCR_OUTPUT_INVALID")

    def test_ocr_source_geometry_and_unknown_score(self):
        with patch.object(file_extractor, "recognize", return_value=(ocr_data(score="-1"), {"engine_version": "synthetic"})):
            page = file_extractor._ocr_image("synthetic", synthetic_image(), 2, "synthetic-ocr")
        self.assertEqual(page.page, 2)
        self.assertIsNone(page.regions[0].confidence)
        self.assertEqual(page.regions[0].bbox.x0, 20)
        self.assertNotIn("weekly", page.text)

    def test_real_synthetic_image_ocr_when_available(self):
        try:
            runtime = ocr_runtime.detect_runtime()
        except ocr_runtime.OCRRuntimeError as error:
            self.skipTest(f"Live OCR unavailable: {error.code}: {error}")
        page = file_extractor._ocr_image("synthetic-live", synthetic_image(), 1, "tesseract-live-test")
        self.assertIn("CS101", page.text.replace(" ", "").upper())
        self.assertEqual(page.runtime["engine_version"], runtime.engine_version)


class CompoundCellTests(unittest.TestCase):
    def test_two_courses_original_vs_variant(self):
        source = document(("CS101 Monday 09:00-11:00 Lecture; MATH201 Monday 13:00-15:00 Tutorial", (10, 30, 500, 70), {}))
        original = adapt_baseline(source, "B2")
        variant = adapt_baseline(source, "B2H-v1")
        self.assertEqual(len(original.events), 1)
        self.assertEqual(len(variant.events), 2)
        self.assertEqual([e.revision.content.course_code for e in variant.events], ["CS101", "MATH201"])
        self.assertNotEqual(original.events[0].revision.method_id, variant.events[0].revision.method_id)
        for event in variant.events:
            self.assertIsNone(event.policy.decision)
            self.assertEqual(event.identity.status, "UNRESOLVED")
            self.assertEqual(event.human_verification.status, "NOT_VERIFIED")

    def test_shared_course_lecture_tutorial(self):
        source = document(("CS101 Monday Lecture 09:00-11:00; Tutorial 11:00-12:00", (10, 30, 500, 70), {"group": "merged-cell"}))
        packet = adapt_baseline(source, "B2H-v1")
        self.assertEqual(len(packet.events), 2)
        self.assertEqual([e.revision.content.session_type for e in packet.events], ["Lecture", "Tutorial"])
        self.assertEqual([e.revision.content.course_code for e in packet.events], ["CS101", "CS101"])
        # Monday is explicit in the first fragment; no cross-fragment day invention.
        self.assertIsNone(packet.events[1].revision.content.day_of_week)
        course = next(o for o in packet.events[1].revision.observations if o.field_name == "course_code")
        self.assertEqual(course.origin, "derived")
        self.assertEqual(course.evidence_ids, ("ev-0",))

    def test_merged_row_and_column_header_inheritance(self):
        source = document(("Monday", (0, 50, 40, 200), {}),
                          ("09:00-11:00", (60, 0, 400, 20), {}),
                          ("CS101 Lecture", (60, 60, 180, 80), {}),
                          ("MATH201 Tutorial", (220, 120, 400, 140), {}))
        packet = adapt_baseline(source, "B2H-v1")
        self.assertEqual(len(packet.events), 2)
        for event in packet.events:
            self.assertEqual(event.revision.content.day_of_week, 1)
            self.assertEqual(event.revision.content.start_time, "09:00")

    def test_merged_cell_overlapping_two_times_is_conflicting(self):
        source = document(("09:00-10:00", (50, 0, 140, 20), {}),
                          ("10:00-11:00", (180, 0, 280, 20), {}),
                          ("CS101 Lecture", (50, 50, 280, 80), {}))
        packet = adapt_baseline(source, "B2H-v1")
        self.assertEqual(len(packet.events), 1)
        self.assertIsNone(packet.events[0].revision.content.start_time)
        observation = next(o for o in packet.events[0].revision.observations if o.field_name == "start_time")
        self.assertEqual(observation.state.value, "CONFLICTING")
        self.assertEqual(len(observation.alternatives), 2)

    def test_explicit_continuation_preserves_course_evidence(self):
        source = document(("CS101", (50, 50, 200, 70), {}),
                          ("Tutorial 11:00-12:00", (50, 80, 400, 100), {"continuation_of": "ev-0"}))
        packet = adapt_baseline(source, "B2H-v1")
        self.assertEqual(packet.events[-1].revision.content.course_code, "CS101")
        self.assertIn("B2H_V1_EXPLICIT_CONTINUATION", {i.code for i in packet.issues})

    def test_incomplete_unreadable_and_tba_remain_explicit(self):
        source = document(("Tutorial Time TBA", None, {}))
        packet = adapt_baseline(source, "B2H-v1")
        self.assertEqual(len(packet.events), 1)
        self.assertIsNone(packet.events[0].revision.content.start_time)
        time = next(o for o in packet.events[0].revision.observations if o.field_name == "start_time")
        self.assertEqual(time.state.value, "UNRESOLVED")
        self.assertEqual(time.raw_value, "TBA")
        self.assertIn("B2H_V1_CELL_GEOMETRY_UNAVAILABLE", {i.code for i in packet.issues})

    def test_ambiguous_unsplit_sessions_do_not_invent_events(self):
        source = document(("CS101 Lecture/Tutorial 09:00-11:00", (20, 50, 450, 80), {}))
        packet = adapt_baseline(source, "B2H-v1")
        self.assertEqual(len(packet.events), 1)
        self.assertIsNone(packet.events[0].revision.content.session_type)
        self.assertEqual(packet.events[0].extraction_status.value, "CONFLICTING")

    def test_native_evidence_rejected_even_when_document_is_mislabeled(self):
        source = document(("CS101 Monday 09:00-11:00", (10, 10, 400, 30), {}))
        source.pages[0].regions[0].adapter = "pdf-native-coordinate"
        for pipeline in ("B1", "B2", "B2H-v1"):
            with self.assertRaisesRegex(ValueError, "require OCR"):
                adapt_baseline(source, pipeline)

    def test_repeated_header_uses_nearest_preceding_same_column(self):
        source = document(("Monday", (10, 0, 400, 20), {}),
                          ("Tuesday", (10, 50, 400, 70), {}),
                          ("CS101 Lecture 09:00-11:00", (10, 80, 400, 100), {}))
        self.assertEqual(adapt_baseline(source, "B2H-v1").events[0].revision.content.day_of_week, 2)

    def test_unknown_venue_is_unresolved_and_not_a_source_value(self):
        source = document(("CS101 Monday Lecture 09:00-11:00 Venue TBA", (10, 40, 500, 70), {}))
        event = adapt_baseline(source, "B2H-v1").events[0]
        self.assertIsNone(event.revision.content.location)
        observation = next(o for o in event.revision.observations if o.field_name == "location")
        self.assertEqual(observation.state.value, "UNRESOLVED")
        self.assertEqual(observation.raw_value, "TBA")

    def test_original_baseline_room_tba_stays_unresolved(self):
        source = document(("CS101 Monday 09:00-11:00 Room TBA Lecture", (10, 40, 500, 70), {}))
        for pipeline in ("B1", "B2"):
            event = adapt_baseline(source, pipeline).events[0]
            self.assertIsNone(event.revision.content.location)
            observation = next(o for o in event.revision.observations if o.field_name == "location")
            self.assertEqual(observation.state.value, "UNRESOLVED")


class ExtractHTTPTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_import_and_invalid_upload(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        response = self.client.post("/api/extract", content=b"invalid", headers={"X-Filename": "bad.exe"})
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["detail"]["code"], "UNSUPPORTED_FILE_TYPE")

    def test_image_to_b1_http_with_mocked_ocr_only(self):
        with patch.object(file_extractor, "recognize", return_value=(ocr_data(), {"engine_version": "synthetic"})):
            response = self.client.post("/api/extract?pipeline=B1", content=image_bytes(), headers={"X-Filename": "synthetic.png", "Content-Type": "image/png"})
        self.assertEqual(response.status_code, 200)
        packet = response.json()["data"]
        self.assertEqual(len(packet["events"]), 1)
        self.assertEqual(packet["evidence"][0]["route"], "ocr")
        self.assertEqual(packet["evidence"][0]["bbox"]["coordinate_space"], "PIXELS")

    def test_pdf_forced_ocr_for_all_baseline_routes(self):
        pdf = synthetic_pdf()
        for pipeline in ("B1", "B2", "B2H-v1"):
            with self.subTest(pipeline=pipeline), patch.object(file_extractor, "recognize", return_value=(ocr_data(), {})), patch("pdfplumber.page.Page.extract_words", side_effect=AssertionError("native text substitution")):
                response = self.client.post(f"/api/extract?pipeline={pipeline}", content=pdf, headers={"X-Filename": "synthetic.pdf", "Content-Type": "application/pdf"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"]["source"]["route"], "ocr")

    def test_real_native_pdf_evidence_mode(self):
        response = self.client.post("/api/extract", content=synthetic_pdf(), headers={"X-Filename": "synthetic.pdf", "Content-Type": "application/pdf"})
        self.assertEqual(response.status_code, 200)
        spans = response.json()["data"]["evidence"]
        self.assertTrue(spans)
        self.assertTrue(all(span["route"] == "native_pdf" and span["confidence"] is None for span in spans))

    def test_unavailable_runtime_http_is_transparent(self):
        with patch.object(file_extractor, "recognize", side_effect=ocr_runtime.OCRRuntimeError("OCR_EXECUTABLE_MISSING", "Install approved system Tesseract.")):
            response = self.client.post("/api/extract?pipeline=B2", content=image_bytes(), headers={"X-Filename": "synthetic.png", "Content-Type": "image/png"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["diagnostic_code"], "OCR_EXECUTABLE_MISSING")

    def test_compound_cell_variant_http_response(self):
        data = ocr_data("CS101 Monday Lecture 09:00-11:00; CS201 Monday Tutorial 13:00-15:00")
        with patch.object(file_extractor, "recognize", return_value=(data, {})):
            response = self.client.post("/api/extract?pipeline=B2H-v1", content=image_bytes(), headers={"X-Filename": "synthetic.png", "Content-Type": "image/png"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["events"]), 2)

    def test_live_image_and_pdf_all_baseline_routes(self):
        try:
            ocr_runtime.detect_runtime()
        except ocr_runtime.OCRRuntimeError as error:
            self.skipTest(f"Live OCR unavailable: {error.code}")
        for filename, mime, content in (("synthetic.png", "image/png", image_bytes()),
                                        ("synthetic.pdf", "application/pdf", synthetic_pdf())):
            for pipeline in ("B1", "B2", "B2H-v1"):
                with self.subTest(filename=filename, pipeline=pipeline):
                    response = self.client.post(f"/api/extract?pipeline={pipeline}", content=content,
                                                headers={"X-Filename": filename, "Content-Type": mime})
                    self.assertEqual(response.status_code, 200, response.text)
                    packet = response.json()["data"]
                    self.assertEqual(packet["source"]["route"], "ocr")
                    self.assertEqual(len(packet["events"]), 1)
                    self.assertTrue(all(span["route"] == "ocr" and span["bbox"]["coordinate_space"] == "PIXELS" for span in packet["evidence"]))
                    event = packet["events"][0]
                    self.assertEqual(event["revision"]["content"]["course_code"], "CS101")
                    self.assertEqual(event["revision"]["content"]["start_time"], "09:00")
                    self.assertEqual(event["revision"]["content"]["end_time"], "11:00")
                    self.assertIsNone(event["policy"]["decision"])
                    self.assertEqual(event["human_verification"]["status"], "NOT_VERIFIED")

    def test_live_compound_original_and_variant_separation(self):
        try:
            ocr_runtime.detect_runtime()
        except ocr_runtime.OCRRuntimeError as error:
            self.skipTest(f"Live OCR unavailable: {error.code}")
        image = Image.new("RGB", (2200, 160), "white")
        ImageDraw.Draw(image).text((20, 30), "CS101 Monday Lecture 09:00-11:00; CS201 Monday Tutorial 13:00-15:00", fill="black", font=ImageFont.load_default(size=32))
        output = BytesIO()
        image.save(output, format="PNG")
        counts = {}
        for pipeline in ("B2", "B2H-v1"):
            response = self.client.post(f"/api/extract?pipeline={pipeline}", content=output.getvalue(),
                                        headers={"X-Filename": "synthetic-compound.png", "Content-Type": "image/png"})
            self.assertEqual(response.status_code, 200, response.text)
            counts[pipeline] = len(response.json()["data"]["events"])
        self.assertEqual(counts, {"B2": 1, "B2H-v1": 2})


if __name__ == "__main__":
    unittest.main()
