"""Synthetic workflow, inventory and gold-export contract tests."""

import unittest
from pydantic import ValidationError

from backend.research.inventory import compare_provisional, inventory_findings
from backend.research.agreement import agreement_summary
from backend.research.models import Adjudication, Comparison, PageInventory, ProvisionalAnnotation, ReconciliationLink, SourceRegion, WorkflowState
from backend.research.workflow import (
    WorkflowError, adjudicate, assign_roles, compare, export_gold_manifest, open_blind_task,
    restricted_view, submit, validate_history, verify_reference,
)
from tests.research_fixtures import CONDITIONAL_FIELDS, assigned, compared, inventory, stamp, submission, verified


class WorkflowTests(unittest.TestCase):
    def test_source_first_inventory_multi_session(self):
        inv = inventory(count=2)
        self.assertEqual(len(inv.sessions), 2)
        self.assertEqual(inv.regions[0].kind, "MULTI_SESSION")
        self.assertEqual(inv.basis, "SOURCE_ONLY")

    def test_prediction_inventory_basis_rejected(self):
        data = inventory().model_dump()
        data["basis"] = "PREDICTIONS"
        with self.assertRaises(ValidationError):
            PageInventory.model_validate(data)

    def test_parser_predictions_not_accepted_as_independent_human_labels(self):
        data = submission("INDEPENDENT", 3).model_dump()
        data["labels"][0]["revision"]["method_id"] = "tarumt_parser_adapter_m1"
        from backend.research.models import Submission
        with self.assertRaises(ValidationError):
            Submission.model_validate(data)

    def test_inventory_evidence_links_and_coverage(self):
        data = inventory().model_dump()
        data["evidence"] = []
        with self.assertRaises(ValidationError):
            PageInventory.model_validate(data)
        data = inventory().model_dump()
        data["coverage_attestation"] = None
        self.assertIn("INVENTORY_COVERAGE_UNATTESTED", {f.code for f in inventory_findings(PageInventory.model_validate(data))})

    def test_unknown_merged_and_continuation_need_notes(self):
        for kind in ("UNKNOWN", "MERGED_CELL", "CONTINUATION", "UNREADABLE"):
            with self.assertRaises(ValidationError):
                SourceRegion(region_id="test", kind=kind, evidence_ids=("test",))

    def test_reconciliation_does_not_declare_either_side_correct(self):
        inv = inventory(count=2)
        annotation = ProvisionalAnnotation(annotation_id="row1", page_id=inv.page_id, original_artifact_id="original", legacy_status="human_verified")
        findings = compare_provisional((inv,), (annotation,), ())
        self.assertEqual({f.code for f in findings}, {"SOURCE_SESSION_UNRECONCILED", "PROVISIONAL_ROW_UNRECONCILED"})
        link = ReconciliationLink(session_ids=tuple(s.session_id for s in inv.sessions), annotation_ids=("row1",),
                                  disposition="SPLIT_CELL", recorded_by=stamp(), reason="synthetic two sessions")
        self.assertEqual(compare_provisional((inv,), (annotation,), (link,)), ())

    def test_reconciliation_duplicate_and_unknown_links(self):
        inv = inventory()
        link = ReconciliationLink(session_ids=("nonexistent",), disposition="MISSING_ANNOTATION", recorded_by=stamp(), reason="test")
        self.assertIn("RECONCILIATION_UNKNOWN_REFERENCE", {f.code for f in compare_provisional((inv,), (), (link,))})

    def test_blind_source_view_contains_no_primary_labels_or_inventory(self):
        wf = submit(assigned(), submission())
        wf, view = open_blind_task(wf, "synthetic-source-file", stamp("synthetic-independent", 2))
        self.assertEqual(set(view), {"page_id", "source_sha256", "source_artifact_id", "mode"})
        self.assertTrue(validate_history(wf))
        with self.assertRaisesRegex(WorkflowError, "BLINDNESS_VIOLATION"):
            restricted_view(wf, stamp("synthetic-independent", 2), "PRIMARY_LABELS")

    def test_predictions_always_prohibited_for_gold_review(self):
        with self.assertRaisesRegex(WorkflowError, "PREDICTIONS_PROHIBITED"):
            restricted_view(assigned(), stamp("synthetic-adjudicator", 1), "PREDICTIONS")

    def test_self_review_assignment_rejected(self):
        wf = assigned()
        data = [a.model_dump() for a in wf.assignments]
        data[1]["actor_id"] = data[0]["actor_id"]
        from backend.research.models import Assignment, PageWorkflow
        with self.assertRaisesRegex(WorkflowError, "INDEPENDENCE"):
            assign_roles(PageWorkflow(page_id=wf.page_id, source_sha256=wf.source_sha256, origin="SYNTHETIC"),
                         wf.people, tuple(Assignment(**a) for a in data), stamp())

    def test_implementer_not_independent_verifier(self):
        wf = assigned()
        data = wf.people[1].model_dump()
        data["contributed_to_implementation"] = True
        from backend.research.models import Person, PageWorkflow
        with self.assertRaisesRegex(WorkflowError, "IMPLEMENTATION_INDEPENDENCE"):
            assign_roles(PageWorkflow(page_id=wf.page_id, source_sha256=wf.source_sha256, origin="SYNTHETIC"),
                         (wf.people[0], Person(**data), wf.people[2]), wf.assignments, stamp())

    def test_unassigned_actor_cannot_submit(self):
        data = submission().model_dump()
        data["submitted_by"] = stamp("synthetic-unassigned", 1)
        data["inventory"]["recorded_by"] = stamp("synthetic-unassigned", 1)
        from backend.research.models import Submission
        with self.assertRaisesRegex(WorkflowError, "NOT_ASSIGNED"):
            submit(assigned(), Submission.model_validate(data))

    def test_submission_cannot_automatically_verify_or_become_gold(self):
        wf = submit(assigned(), submission())
        self.assertEqual(wf.state, "PRIMARY_SUBMITTED")
        self.assertIsNone(wf.verification)
        with self.assertRaisesRegex(WorkflowError, "NOT_VERIFIED"):
            export_gold_manifest((wf,))

    def test_corrections_append_and_preserve_original(self):
        first = submission()
        wf = submit(assigned(), first)
        corrected = submission(minute=2, previous=first.submission_id, location="SYNTHETIC CORRECTED ROOM")
        data = corrected.model_dump()
        data["labels"][0]["revision"]["version"] = 2
        data["labels"][0]["revision"]["previous_revision_id"] = first.labels[0].revision.revision_id
        from backend.research.models import Submission
        corrected = Submission.model_validate(data)
        wf = submit(wf, corrected)
        self.assertEqual(len(wf.submissions), 2)
        self.assertEqual(wf.submissions[0], first)
        self.assertTrue(validate_history(wf))

    def test_changed_content_requires_m1_revision_predecessor(self):
        wf = submit(assigned(), submission())
        with self.assertRaisesRegex(WorkflowError, "REVISION_CHAIN_REQUIRED"):
            submit(wf, submission(minute=2, previous="primary-1", location="SYNTHETIC CHANGED"))

    def test_primary_reconciliation_not_independent_audit(self):
        wf = submit(assigned(), submission())
        inv = wf.submissions[0].inventory
        wf = verify_reference(wf, inv, ((wf.submissions[0].labels[0].label_id, inv.sessions[0].session_id),),
                              stamp("synthetic-primary", 2), field_registry_artifact_id="synthetic-registry",
                              conditional_fields=CONDITIONAL_FIELDS, independently_audited=False)
        self.assertEqual(wf.state, WorkflowState.MANUALLY_VERIFIED_REFERENCE)
        self.assertNotEqual(wf.state, WorkflowState.INDEPENDENTLY_VERIFIED)

    def test_invalid_correction_predecessor_and_locked_labels(self):
        wf = submit(assigned(), submission())
        with self.assertRaisesRegex(WorkflowError, "PREDECESSOR"):
            submit(wf, submission(minute=2))
        with self.assertRaisesRegex(WorkflowError, "LOCKED"):
            submit(compared(), submission(minute=5, previous="primary-1"))

    def test_agreement_not_automatic_verification(self):
        wf = compared()
        self.assertEqual(wf.state, WorkflowState.RECONCILED)
        self.assertIsNone(wf.verification)

    def test_raw_agreement_state_tables_and_undefined_kappa(self):
        result = agreement_summary(compared())
        self.assertEqual(result["paired_sessions"], 1)
        self.assertEqual(result["unmatched_primary"], 0)
        self.assertIsNone(result["field_state_tables"]["location"]["nominal_state_kappa"])
        self.assertEqual(result["field_state_tables"]["location"]["kappa_status"], "UNDEFINED")

    def test_raw_agreement_preserved_after_adjudication(self):
        wf = compared(location="SYNTHETIC OTHER ROOM")
        before = agreement_summary(wf)
        wf = adjudicate(wf, Adjudication(disagreement_id=wf.disagreements[0].disagreement_id, decision="USE_PRIMARY",
                                        recorded_by=stamp("synthetic-adjudicator", 5), reason="synthetic proof"))
        self.assertEqual(agreement_summary(wf), before)

    def test_disagreements_report_fields(self):
        wf = compared(location="SYNTHETIC OTHER ROOM")
        self.assertEqual(wf.state, WorkflowState.DISPUTED)
        self.assertIn("location", wf.disagreements[0].fields)

    def test_compare_before_blind_submission_rejected(self):
        with self.assertRaisesRegex(WorkflowError, "BOTH_BLIND_LABELS"):
            compare(assigned(), Comparison(primary_submission_id="p", independent_submission_id="i", label_pairs=(), compared_by=stamp()))

    def test_non_originating_adjudication_required(self):
        wf = compared(location="SYNTHETIC OTHER ROOM")
        decision = Adjudication(disagreement_id=wf.disagreements[0].disagreement_id, decision="USE_PRIMARY",
                                recorded_by=stamp("synthetic-primary", 5), reason="synthetic source evidence")
        with self.assertRaisesRegex(WorkflowError, "NON_ORIGINATING"):
            adjudicate(wf, decision)

    def test_adjudication_resolves_without_promoting_to_verified(self):
        wf = compared(location="SYNTHETIC OTHER ROOM")
        decision = Adjudication(disagreement_id=wf.disagreements[0].disagreement_id, decision="USE_PRIMARY",
                                recorded_by=stamp("synthetic-adjudicator", 5), reason="synthetic source evidence")
        wf = adjudicate(wf, decision)
        self.assertEqual(wf.state, WorkflowState.RECONCILED)
        self.assertIsNone(wf.verification)
        self.assertTrue(validate_history(wf))

    def test_adjudicated_correction_preserves_revision_evidence(self):
        wf = compared(location="SYNTHETIC OTHER ROOM")
        from backend.research.models import SessionLabel
        data = wf.submissions[0].labels[0].model_dump()
        data["label_id"] = "synthetic-corrected-label"
        data["revision"]["version"] = 2
        data["revision"]["previous_revision_id"] = data["revision"]["revision_id"]
        from uuid import uuid4
        data["revision"]["revision_id"] = uuid4()
        data["revision"]["method_id"] = "source_backed_human_correction_m2"
        corrected = SessionLabel.model_validate(data)
        wf = adjudicate(wf, Adjudication(disagreement_id=wf.disagreements[0].disagreement_id, decision="CORRECTED",
                                        recorded_by=stamp("synthetic-adjudicator", 5), reason="synthetic evidence-linked correction", corrected_label=corrected))
        self.assertEqual(wf.adjudications[0].corrected_label.revision.version, 2)
        self.assertEqual(wf.submissions[0].labels[0].revision.version, 1)

    def test_ambiguous_exclusion_needs_actual_source_region_scope(self):
        wf = compared(location="SYNTHETIC OTHER ROOM")
        for regions in ((), ("nonexistent-region",)):
            with self.assertRaises(WorkflowError):
                adjudicate(wf, Adjudication(disagreement_id=wf.disagreements[0].disagreement_id, decision="EXCLUDED_AMBIGUOUS",
                                            recorded_by=stamp("synthetic-adjudicator", 5), reason="synthetic ambiguity", exclusion_region_ids=regions))

    def test_invalid_verification_transition(self):
        wf = assigned()
        with self.assertRaisesRegex(WorkflowError, "TRANSITION_INVALID"):
            verify_reference(wf, inventory(), (), stamp(), field_registry_artifact_id="synthetic-registry",
                             conditional_fields=CONDITIONAL_FIELDS, independently_audited=True)

    def test_explicit_verification_bijection_and_export(self):
        wf = verified()
        self.assertEqual(wf.state, WorkflowState.INDEPENDENTLY_VERIFIED)
        self.assertTrue(validate_history(wf))
        result = export_gold_manifest((wf,))
        self.assertEqual(result["verified_event_count"], 1)
        self.assertEqual(result["events"][0]["origin"], "SYNTHETIC")

    def test_wrong_bijection_cannot_verify(self):
        wf = compared()
        with self.assertRaisesRegex(WorkflowError, "BIJECTION"):
            verify_reference(wf, wf.submissions[0].inventory, (), stamp("synthetic-independent", 6),
                             field_registry_artifact_id="synthetic-registry", conditional_fields=CONDITIONAL_FIELDS, independently_audited=True)

    def test_tba_location_not_verified_as_a_resolved_room(self):
        wf = submit(assigned(), submission(location="TBA"))
        inv = wf.submissions[0].inventory
        with self.assertRaisesRegex(WorkflowError, "PLACEHOLDER_UNRESOLVED"):
            verify_reference(wf, inv, ((wf.submissions[0].labels[0].label_id, inv.sessions[0].session_id),),
                              stamp("synthetic-primary", 2), field_registry_artifact_id="synthetic-registry",
                              conditional_fields=CONDITIONAL_FIELDS, independently_audited=False)

    def test_missing_registry_or_weak_conditional_registry_rejected(self):
        wf = compared()
        inv = wf.submissions[0].inventory
        bindings = ((wf.submissions[0].labels[0].label_id, inv.sessions[0].session_id),)
        for registry, fields in (("", CONDITIONAL_FIELDS), ("synthetic-registry", ())):
            with self.assertRaises(WorkflowError):
                verify_reference(wf, inv, bindings, stamp("synthetic-independent", 6), field_registry_artifact_id=registry,
                                 conditional_fields=fields, independently_audited=True)

    def test_tampered_workflow_history_does_not_verify(self):
        wf = verified()
        data = wf.model_dump()
        data["source_sha256"] = "b" * 64
        from backend.research.models import PageWorkflow
        tampered = PageWorkflow.model_validate(data)
        self.assertFalse(validate_history(tampered))
        with self.assertRaises(WorkflowError):
            export_gold_manifest((tampered,))


if __name__ == "__main__":
    unittest.main()
