"""Synthetic TAR UMT parser/adapter cases; no portal access or gold data."""

from hashlib import sha256
import unittest

from backend.models.event import CanonicalTimetable
from backend.services.event_adapter import adapt_tarumt_response, canonical_api_data
from backend.services.timetable_parser import parse_intranet_response
from tests.fixtures import timetable_html


class AdapterTests(unittest.TestCase):
    def adapt(self, **values):
        return adapt_tarumt_response(parse_intranet_response(timetable_html(**values)))

    def test_maps_existing_parser_fields_conservatively(self):
        packet = self.adapt()
        event = packet.events[0]
        content = event.revision.content
        self.assertEqual(content.course_code, "TEST1001")
        self.assertEqual(content.course_name, "SYNTHETIC COURSE")
        self.assertEqual(content.session_type, "Lecture")
        self.assertEqual(content.day_of_week, 1)
        self.assertEqual(content.start_time, "09:00")
        self.assertEqual(content.location, "TEST ROOM")
        self.assertEqual(content.location_detail, "TEST ROOM, Test Block")
        self.assertEqual(content.source_class_status, "normal")
        self.assertEqual(event.extraction_status, "EXTRACTED")

    def test_original_evidence_and_hash_basis_are_preserved(self):
        html = timetable_html()
        packet = adapt_tarumt_response(parse_intranet_response(html))
        self.assertEqual(packet.source.content_sha256, sha256(html.encode("utf-8")).hexdigest())
        self.assertEqual(packet.source.content_hash_basis, "decoded_text_utf8")
        cell = next(span for span in packet.evidence if span.raw_html)
        self.assertIn("SYNTHETIC COURSE", cell.raw_html)
        self.assertIn("TEST1001", cell.raw_text)
        self.assertIsNone(cell.bbox)
        self.assertIsNone(cell.confidence)
        self.assertIn("zero-based", cell.locator)
        start = next(o for o in packet.events[0].revision.observations if o.field_name == "start_time")
        self.assertEqual(start.raw_value, "9:00 AM")
        self.assertTrue(start.transformations)

    def test_legacy_response_fields_preserved_and_internal_fields_hidden(self):
        parsed = parse_intranet_response(timetable_html())
        response = canonical_api_data(parsed)
        for key in ("semester", "week", "week_start", "week_end", "event_count", "events"):
            self.assertEqual(response[key], parsed[key])
        self.assertFalse(any(key.startswith("_") for key in response))
        CanonicalTimetable.model_validate(response["canonical"])

    def test_week_view_does_not_invent_recurrence_or_teaching_pattern(self):
        content = self.adapt().events[0].revision.content
        for name in ("source_timezone", "teaching_weeks", "excluded_weeks", "recurrence", "end_day_offset", "section", "cohort_id"):
            self.assertIsNone(getattr(content, name))

    def test_no_automatic_policy_validation_or_verification(self):
        packet = self.adapt()
        event = packet.events[0]
        self.assertIsNone(event.policy.decision)
        self.assertEqual(event.validation.status, "NOT_ASSESSED")
        self.assertEqual(event.human_review.status, "NOT_REVIEWED")
        self.assertEqual(event.human_verification.status, "NOT_VERIFIED")
        self.assertEqual(packet.complete_schedule_verification.status, "NOT_VERIFIED")
        self.assertEqual(event.calendar.approval_status, "NOT_APPROVED")
        self.assertEqual(event.calendar.sync_status, "NOT_SUBMITTED")

    def test_capture_ids_are_not_stable_session_ids(self):
        first, second = self.adapt(), self.adapt()
        self.assertNotEqual(first.source.source_document_id, second.source.source_document_id)
        self.assertNotEqual(first.events[0].identity.event_id, second.events[0].identity.event_id)
        for packet in (first, second):
            identity = packet.events[0].identity
            for name in ("stable_key", "stable_source_session_id", "source_family_id", "timetable_id"):
                self.assertIsNone(getattr(identity, name))
            self.assertEqual(identity.status, "UNRESOLVED")

    def test_missing_tooltip_values_not_assumed_source_absent(self):
        packet = self.adapt(title="")
        states = {o.field_name: o.state for o in packet.events[0].revision.observations}
        self.assertEqual(states["lecturer"], "MISSING")
        self.assertEqual(states["location"], "MISSING")
        self.assertIsNone(packet.events[0].revision.content.lecturer)

    def test_unparseable_tooltip_retains_unresolved_text(self):
        packet = self.adapt(title="SYNTHETIC unreadable tooltip")
        observation = next(o for o in packet.events[0].revision.observations if o.field_name == "lecturer")
        self.assertEqual(observation.state, "UNRESOLVED")
        self.assertEqual(observation.raw_value, "SYNTHETIC unreadable tooltip")
        self.assertIsNone(packet.events[0].revision.content.lecturer)

    def test_conflicting_visible_and_tooltip_type_not_silently_overwritten(self):
        packet = self.adapt(visible="TEST1001 (T) 9:00 AM - 10:00 AM")
        event = packet.events[0]
        observation = next(o for o in event.revision.observations if o.field_name == "session_type")
        self.assertEqual(observation.state, "CONFLICTING")
        self.assertEqual({a.normalized_value for a in observation.alternatives}, {"Lecture", "Tutorial"})
        self.assertIsNone(event.revision.content.session_type)
        self.assertEqual(event.extraction_status, "CONFLICTING")
        self.assertIn("CONFLICTING_SESSION_TYPE", {issue.code for issue in packet.issues})

    def test_multiple_course_values_remain_unresolved_alternatives(self):
        packet = self.adapt(visible="TEST1001 / TEST2002 (L) 9:00 AM - 10:00 AM")
        observation = next(o for o in packet.events[0].revision.observations if o.field_name == "course_code")
        self.assertEqual(observation.state, "UNRESOLVED")
        self.assertEqual({a.normalized_value for a in observation.alternatives}, {"TEST1001", "TEST2002"})
        self.assertIsNone(packet.events[0].revision.content.course_code)

    def test_multiple_time_ranges_not_silently_reduced_to_first_range(self):
        packet = self.adapt(visible="TEST1001 (L) 9:00 AM - 10:00 AM OR 11:00 AM - 12:00 PM")
        event = packet.events[0]
        self.assertIsNone(event.revision.content.start_time)
        self.assertIsNone(event.revision.content.end_time)
        observation = next(o for o in event.revision.observations if o.field_name == "start_time")
        self.assertEqual(observation.state, "UNRESOLVED")
        self.assertEqual({a.normalized_value for a in observation.alternatives}, {"09:00", "11:00"})

    def test_bad_time_is_retained_instead_of_raising_or_disappearing(self):
        parsed = parse_intranet_response(timetable_html(visible="TEST1001 (L) 99:00 AM - 10:00 AM"))
        self.assertEqual(parsed["event_count"], 0)
        packet = adapt_tarumt_response(parsed)
        self.assertEqual(len(packet.events), 1)
        observation = next(o for o in packet.events[0].revision.observations if o.field_name == "start_time")
        self.assertEqual(observation.state, "UNRESOLVED")
        self.assertEqual(observation.raw_value, "99:00 AM")
        self.assertIn("INVALID_SESSION_TIME", {issue.code for issue in packet.issues})

    def test_bad_date_is_unresolved_not_defaulted(self):
        packet = self.adapt(header="Mon 2026-02-30")
        observation = next(o for o in packet.events[0].revision.observations if o.field_name == "date")
        self.assertEqual(observation.state, "UNRESOLVED")
        self.assertIsNone(packet.events[0].revision.content.date)

    def test_unrecognized_row_and_nonempty_cell_retained(self):
        packet = self.adapt(header="SYNTHETIC UNKNOWN DAY")
        self.assertEqual(len(packet.events), 1)
        self.assertIsNone(packet.events[0].revision.content.date)
        self.assertIn("UNPARSED_ROW_HEADER", {issue.code for issue in packet.issues})

    def test_unrecognized_event_grammar_and_plain_cell_not_discarded(self):
        packet = self.adapt(visible="SYNTHETIC UNKNOWN SESSION", title="", extra_cells="<td>SYNTHETIC unclassified cell</td><td></td>")
        self.assertEqual(len(packet.events), 2)
        self.assertEqual(len(packet.issues), 2)
        self.assertFalse(packet.events[0].revision.core_fields_present)
        self.assertEqual(packet.events[0].extraction_status, "PARTIAL")

    def test_unknown_colour_retained_as_unresolved(self):
        packet = self.adapt(style="background-color:#123456")
        observation = next(o for o in packet.events[0].revision.observations if o.field_name == "source_class_status")
        self.assertEqual(observation.state, "UNRESOLVED")
        self.assertEqual(observation.raw_value, "#123456")
        self.assertIsNone(packet.events[0].revision.content.source_class_status)

    def test_multiple_source_sessions_in_one_cell_are_retained(self):
        html = timetable_html().replace('</div></td>', '</div><div data-rel="tooltip" title="">TEST2002 (T) 11:00 AM - 12:00 PM</div></td>')
        parsed = parse_intranet_response(html)
        self.assertEqual(parsed["event_count"], 1)
        packet = adapt_tarumt_response(parsed)
        self.assertEqual(len(packet.events), 2)
        self.assertEqual({event.revision.content.course_code for event in packet.events}, {"TEST1001", "TEST2002"})
        self.assertIn("MULTIPLE_SESSION_CELL", {issue.code for issue in packet.issues})

    def test_end_of_day_representation_without_legacy_success(self):
        packet = self.adapt(visible="TEST1001 (L) 23:00 - 24:00")
        self.assertEqual(packet.events[0].revision.content.end_time, "24:00")
        self.assertEqual(packet.events[0].extraction_status, "PARTIAL")
        self.assertTrue(packet.issues)

    def test_parser_only_evidence_is_honestly_labelled(self):
        packet = adapt_tarumt_response({"events": [{"subject_code": "TEST1001", "day": "Mon", "date": "2026-08-17",
                                                   "start_time": "09:00", "end_time": "10:00", "class_type": "Unknown", "class_status": "unknown"}]})
        self.assertEqual(packet.source.content_hash_basis, "parser_output_json")
        self.assertEqual(packet.source.route, "text")
        self.assertTrue(all(e.raw_html is None for e in packet.evidence))
        self.assertIn("ORIGINAL_SOURCE_UNAVAILABLE", {issue.code for issue in packet.issues})
        self.assertIsNone(packet.events[0].revision.content.session_type)

    def test_missing_week_table_remains_explicit_failure(self):
        with self.assertRaises(ValueError):
            parse_intranet_response("<html>SYNTHETIC NOT A TIMETABLE</html>")

    def test_empty_timetable_is_not_complete_schedule_proof(self):
        html = '<table id="simple-table"><tr><td>Day/Time</td></tr><tr><td>Mon 2026-08-17</td><td></td></tr></table>'
        packet = adapt_tarumt_response(parse_intranet_response(html))
        self.assertEqual(packet.events, ())
        self.assertEqual(packet.complete_schedule_verification.status, "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
