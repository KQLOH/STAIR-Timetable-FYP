"""Contract tests against explicitly synthetic records only."""

from datetime import date
import json
import unittest
from uuid import uuid4

from pydantic import ValidationError

from backend.models.evidence import BoundingBox, EvidenceSpan
from backend.models.event import (
    CalendarState, CanonicalEvent, CanonicalTimetable, CompleteScheduleVerification,
    ConstraintValidation, EventContent, EventIdentity, EventRevision, FieldObservation,
    HumanReview, HumanVerification, PolicyDecision, RecurrenceRule, stable_session_key,
)
from backend.services.event_adapter import adapt_tarumt_response
from backend.services.timetable_parser import parse_intranet_response
from tests.fixtures import timetable_html


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.packet = adapt_tarumt_response(parse_intranet_response(timetable_html()))
        self.event = self.packet.events[0]
        self.revision = self.event.revision
        self.evidence_id = self.packet.evidence[-2].evidence_id

    def revision_with(self, field, state, value=None, **observation_values):
        payload = self.revision.model_dump(mode="json")
        payload["content"][field] = value
        for index, observation in enumerate(payload["observations"]):
            if observation["field_name"] == field:
                payload["observations"][index] = {
                    "field_name": field, "state": state,
                    "evidence_ids": [self.evidence_id], **observation_values,
                }
        return EventRevision.model_validate(payload)

    def test_serialization_round_trip(self):
        encoded = self.packet.model_dump_json()
        restored = CanonicalTimetable.model_validate_json(encoded)
        self.assertEqual(restored, self.packet)
        self.assertEqual(json.loads(encoded)["events"][0]["revision"]["content"]["date"], "2026-08-17")

    def test_frozen_records_and_unknown_fields(self):
        with self.assertRaises(ValidationError):
            self.event.candidate_id = "mutated"
        with self.assertRaises(ValidationError):
            EventContent(fabricated_field="value")
        self.assertIsInstance(self.revision.observations, tuple)

    def test_present_requires_evidence(self):
        with self.assertRaises(ValidationError):
            FieldObservation(field_name="lecturer", state="PRESENT", raw_value="TEST")

    def test_missing_cannot_hide_observed_text(self):
        with self.assertRaises(ValidationError):
            FieldObservation(field_name="lecturer", state="MISSING", raw_value="TEST")

    def test_unresolved_keeps_raw_value_without_normalized_value(self):
        revision = self.revision_with("lecturer", "UNRESOLVED", raw_value="illegible TEST TEXT")
        self.assertIsNone(revision.content.lecturer)
        with self.assertRaises(ValidationError):
            self.revision_with("lecturer", "UNRESOLVED", value="guessed", raw_value="unknown")

    def test_source_absent_and_not_applicable_require_justification(self):
        for state in ("SOURCE_ABSENT", "NOT_APPLICABLE"):
            with self.subTest(state=state):
                with self.assertRaises(ValidationError):
                    self.revision_with("location", state)
                revision = self.revision_with("location", state, justification="Synthetic source explicitly omits venue")
                self.assertIsNone(revision.content.location)

    def test_mandatory_times_cannot_be_inapplicable(self):
        for state in ("SOURCE_ABSENT", "NOT_APPLICABLE"):
            with self.assertRaises(ValidationError):
                self.revision_with("start_time", state, justification="synthetic omission")

    def test_conditional_missing_and_partial_core_are_representable(self):
        revision = self.revision_with("session_type", "MISSING")
        self.assertTrue(revision.core_fields_present)
        revision = self.revision_with("start_time", "MISSING")
        self.assertFalse(revision.core_fields_present)

    def test_required_identity_or_day_group_not_declared_absent(self):
        for fields in (("course_code", "course_name"), ("day_of_week", "date")):
            payload = self.revision.model_dump(mode="json")
            for field in fields:
                payload["content"][field] = None
                for observation in payload["observations"]:
                    if observation["field_name"] == field:
                        observation.update(state="SOURCE_ABSENT", raw_value=None,
                                           justification="synthetic omitted group", evidence_ids=[self.evidence_id])
            with self.assertRaises(ValidationError):
                EventRevision.model_validate(payload)

    def test_field_registry_is_complete_and_unique(self):
        self.assertEqual({o.field_name for o in self.revision.observations}, set(EventContent.model_fields))
        for observations in (self.revision.observations[:-1], self.revision.observations + (self.revision.observations[0],)):
            payload = self.revision.model_dump()
            payload["observations"] = observations
            with self.assertRaises(ValidationError):
                EventRevision.model_validate(payload)

    def test_conflicting_alternatives_need_distinct_evidence(self):
        base = dict(field_name="session_type", state="CONFLICTING")
        with self.assertRaises(ValidationError):
            FieldObservation(**base, alternatives=[{"raw_value": "L"}, {"raw_value": "T"}])
        alternatives = [{"raw_value": value, "evidence_ids": [self.evidence_id]} for value in ("L", "T")]
        revision = self.revision_with("session_type", "CONFLICTING", alternatives=alternatives)
        self.assertIsNone(revision.content.session_type)
        with self.assertRaises(ValidationError):
            FieldObservation(**base, alternatives=[alternatives[0], alternatives[0]])

    def test_dangling_evidence_rejected(self):
        payload = self.packet.model_dump(mode="json")
        payload["events"][0]["revision"]["observations"][0]["evidence_ids"] = ["nonexistent"]
        with self.assertRaises(ValidationError):
            CanonicalTimetable.model_validate(payload)

    def test_duplicate_evidence_and_candidate_ids_rejected(self):
        payload = self.packet.model_dump(mode="json")
        payload["evidence"].append(payload["evidence"][0])
        with self.assertRaises(ValidationError):
            CanonicalTimetable.model_validate(payload)
        payload = self.packet.model_dump(mode="json")
        payload["events"].append(payload["events"][0])
        with self.assertRaises(ValidationError):
            CanonicalTimetable.model_validate(payload)

    def test_evidence_source_capture_must_match(self):
        payload = self.packet.model_dump(mode="json")
        payload["evidence"][0]["source_document_id"] = "other-capture"
        with self.assertRaises(ValidationError):
            CanonicalTimetable.model_validate(payload)

    def test_confidence_is_unknown_unless_supplied(self):
        span = EvidenceSpan(evidence_id="test", source_document_id="synthetic", route="text", raw_text="test")
        self.assertIsNone(span.confidence)
        for confidence in (-0.1, 1.1, float("nan")):
            with self.assertRaises(ValidationError):
                EvidenceSpan(**{**span.model_dump(), "confidence": confidence})

    def test_bounding_boxes_use_explicit_coordinate_space(self):
        with self.assertRaises(ValidationError):
            BoundingBox(x0=0, y0=0, x1=1, y1=1)
        with self.assertRaises(ValidationError):
            BoundingBox(x0=1, y0=0, x1=0, y1=1, coordinate_space="PIXELS")
        with self.assertRaises(ValidationError):
            BoundingBox(x0=0, y0=0, x1=2, y1=1, coordinate_space="NORMALIZED")

    def test_time_and_date_representations(self):
        content = EventContent(date="2026-08-17", start_time="23:00", end_time="24:00")
        self.assertEqual(content.date, date(2026, 8, 17))
        self.assertIsNone(content.source_timezone)
        for values in ({"date": "2026-02-30"}, {"start_time": "24:00"}, {"end_time": "25:00"}, {"start_time": "9:00"}):
            with self.assertRaises(ValidationError):
                EventContent(**values)

    def test_overnight_not_inferred_or_silently_repaired(self):
        content = EventContent(start_time="23:00", end_time="01:00")
        self.assertIsNone(content.end_day_offset)
        self.assertEqual(content.end_time, "01:00")

    def test_recurrence_preserves_boundaries_and_exceptions(self):
        rule = RecurrenceRule(frequency="WEEKLY", by_day=(1,), excluded_dates=("2026-08-24",),
                              replacement_dates=("2026-08-25",), source_rule="SYNTHETIC weekly rule")
        self.assertIsNone(rule.until)
        self.assertIsNone(rule.interval)
        self.assertEqual(rule.excluded_dates, (date(2026, 8, 24),))
        self.assertEqual(RecurrenceRule.model_validate_json(rule.model_dump_json()), rule)
        with self.assertRaises(ValidationError):
            RecurrenceRule(frequency="WEEKLY", by_day=(0,))

    def test_weeks_and_excluded_weeks_are_distinct(self):
        content = EventContent(teaching_weeks=(1, 2, 3), excluded_weeks=(2,))
        self.assertEqual(content.excluded_weeks, (2,))
        self.assertIsNone(content.recurrence)
        with self.assertRaises(ValidationError):
            EventContent(teaching_weeks=(0,))

    def test_identity_requires_authoritative_components(self):
        self.assertIsNone(self.event.identity.stable_key)
        with self.assertRaises(ValidationError):
            EventIdentity(status="CONFIRMED", stable_key="a" * 64)
        for status in ("UNRESOLVED", "AMBIGUOUS"):
            with self.assertRaises(ValidationError):
                EventIdentity(status=status, stable_key="a" * 64)

    def test_stable_key_independent_of_mutable_content(self):
        key = stable_session_key("synthetic-family", "synthetic-table", "session-1")
        identity = EventIdentity(status="CONFIRMED", source_family_id="synthetic-family",
                                 timetable_id="synthetic-table", stable_source_session_id="session-1",
                                 stable_key=key, evidence_ids=(self.evidence_id,))
        self.assertEqual(identity.stable_key, stable_session_key("synthetic-family", "synthetic-table", "session-1"))
        self.assertNotEqual(key, stable_session_key("synthetic-family", "synthetic-table", "session-2"))
        self.assertNotEqual(stable_session_key("ab", "c", "d"), stable_session_key("a", "bc", "d"))
        with self.assertRaises(ValueError):
            stable_session_key("", "table", "session")

    def test_revision_predecessor_required_and_not_self(self):
        payload = self.revision.model_dump()
        payload["version"] = 2
        with self.assertRaises(ValidationError):
            EventRevision.model_validate(payload)
        payload["previous_revision_id"] = uuid4()
        self.assertEqual(EventRevision.model_validate(payload).version, 2)
        payload["previous_revision_id"] = payload["revision_id"]
        with self.assertRaises(ValidationError):
            EventRevision.model_validate(payload)

    def test_accept_does_not_verify_or_approve(self):
        payload = self.event.model_dump()
        payload["policy"] = PolicyDecision(decision="ACCEPT", revision_id=self.revision.revision_id, policy_version="synthetic-test-policy")
        event = CanonicalEvent.model_validate(payload)
        self.assertEqual(event.human_verification.status, "NOT_VERIFIED")
        self.assertEqual(event.calendar.approval_status, "NOT_APPROVED")
        self.assertEqual(self.packet.complete_schedule_verification.status, "NOT_VERIFIED")

    def test_rule_validity_and_review_do_not_verify_source(self):
        payload = self.event.model_dump()
        payload["validation"] = ConstraintValidation(status="VALID", revision_id=self.revision.revision_id, validator_version="synthetic-validator")
        payload["human_review"] = HumanReview(status="APPROVED", revision_id=self.revision.revision_id,
                                              actor_id="synthetic-test-actor", recorded_at="2026-10-14T12:00:00+08:00")
        event = CanonicalEvent.model_validate(payload)
        self.assertEqual(event.human_verification.status, "NOT_VERIFIED")

    def test_positive_statuses_need_revision_and_provenance(self):
        for factory, values in ((PolicyDecision, {"decision": "ACCEPT"}),
                                (ConstraintValidation, {"status": "VALID"}),
                                (HumanReview, {"status": "APPROVED"}),
                                (HumanVerification, {"status": "MANUALLY_VERIFIED_REFERENCE"})):
            with self.assertRaises(ValidationError):
                factory(**values)

    def test_stale_assessment_rejected(self):
        payload = self.event.model_dump()
        payload["policy"] = PolicyDecision(decision="ACCEPT", revision_id=uuid4(), policy_version="test")
        with self.assertRaises(ValidationError):
            CanonicalEvent.model_validate(payload)

    def test_calendar_eligibility_is_not_approval_or_external_confirmation(self):
        calendar = CalendarState(eligibility="ELIGIBLE", eligibility_revision_id=self.revision.revision_id,
                                 eligibility_policy_version="synthetic-calendar-policy")
        self.assertEqual(calendar.approval_status, "NOT_APPROVED")
        self.assertEqual(calendar.sync_status, "NOT_SUBMITTED")
        for values in ({"approval_status": "CALENDAR_APPROVED"}, {"sync_status": "CONFIRMED"},
                       {"sync_status": "APPROVED"}, {"eligibility": "ELIGIBLE"}):
            with self.assertRaises(ValidationError):
                CalendarState(**values)
        self.assertEqual(CalendarState(sync_status="OUTCOME_UNKNOWN").sync_status, "OUTCOME_UNKNOWN")

    def test_calendar_records_bind_current_revision_and_preserve_separation(self):
        payload = self.event.model_dump(mode="json")
        payload["calendar"] = {
            "eligibility": "ELIGIBLE", "eligibility_revision_id": str(self.revision.revision_id),
            "eligibility_policy_version": "synthetic-policy", "approval_status": "CALENDAR_APPROVED",
            "approval": {"revision_id": str(self.revision.revision_id), "actor_id": "synthetic-actor",
                         "recorded_at": "2026-10-14T12:00:00+08:00", "action_digest": "a" * 64,
                         "target_calendar_id": "synthetic-calendar", "remote_snapshot_digest": "b" * 64},
        }
        event = CanonicalEvent.model_validate(payload)
        self.assertEqual(event.calendar.sync_status, "NOT_SUBMITTED")
        self.assertEqual(event.human_verification.status, "NOT_VERIFIED")
        payload["calendar"]["approval"]["revision_id"] = str(uuid4())
        with self.assertRaises(ValidationError):
            CanonicalEvent.model_validate(payload)

    def test_remote_confirmation_requires_observation_and_does_not_verify_source(self):
        payload = self.event.model_dump(mode="json")
        payload["calendar"] = {
            "sync_status": "CONFIRMED",
            "confirmation": {"revision_id": str(self.revision.revision_id), "external_event_id": "synthetic-remote",
                             "observed_at": "2026-10-14T12:00:00+08:00", "remote_state_digest": "c" * 64,
                             "evidence_record_id": "synthetic-observation"},
        }
        event = CanonicalEvent.model_validate(payload)
        self.assertEqual(event.human_verification.status, "NOT_VERIFIED")
        self.assertEqual(event.calendar.approval_status, "NOT_APPROVED")
        payload["calendar"]["confirmation"]["external_event_id"] = ""
        with self.assertRaises(ValidationError):
            CanonicalEvent.model_validate(payload)

    def test_complete_schedule_needs_separate_attestation(self):
        self.assertTrue(self.revision.core_fields_present)
        self.assertEqual(self.packet.complete_schedule_verification.status, "NOT_VERIFIED")
        with self.assertRaises(ValidationError):
            CompleteScheduleVerification(status="COMPLETE_SCHEDULE_VERIFIED")

    def test_verification_timestamps_must_be_timezone_aware(self):
        with self.assertRaises(ValidationError):
            HumanVerification(status="MANUALLY_VERIFIED_REFERENCE", revision_id=self.revision.revision_id,
                              actor_id="synthetic", recorded_at="2026-10-14T12:00:00", evidence_record_id="synthetic-proof")


if __name__ == "__main__":
    unittest.main()
