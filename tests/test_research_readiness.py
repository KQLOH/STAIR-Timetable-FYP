"""Synthetic manifests, audit sampling and fail-closed PRE-01 tests."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from backend.research.audit import AuditError, audit_progress, prepare_audit
from backend.research.audit_detection_simulation_v1 import simulate_detection
from backend.research.cli import main
from backend.research.integrity import check_artifacts, check_sources, check_splits, file_sha256, record_digest, safe_path
from backend.research.models import Artifact, AuditPolicy, HistoricalAccess, ResearchBundle, SourceTimetable
from backend.research.readiness import assess_pre01
from tests.research_fixtures import HASH, page, stamp, synthetic_directory, verified


class ManifestTests(unittest.TestCase):
    def test_missing_metadata_explicit_not_defaulted(self):
        source = SourceTimetable(source_id="synthetic-source")
        self.assertIsNone(source.institution)
        self.assertIsNone(source.captured_on)
        self.assertEqual(source.usage_permission, "UNKNOWN")
        self.assertIn("SOURCE_METADATA_MISSING", {f.code for f in check_sources((source,), ())})

    def test_invalid_hash_and_unknown_fields_rejected(self):
        for data in ({"original_sha256": "not a hash"}, {"unexpected": "value"}, {"page_count": 0}):
            with self.assertRaises(ValidationError):
                SourceTimetable(**data)

    def test_timezone_scope_requires_authority_or_documented_exclusion(self):
        source = SourceTimetable(source_id="synthetic-source", timezone_conversion_scope="AUTHORISED")
        self.assertIn("SOURCE_TIMEZONE_AUTHORITY_MISSING", {f.code for f in check_sources((source,), ())})
        source = SourceTimetable(source_id="synthetic-source", timezone_conversion_scope="DISABLED_UNRESOLVED")
        self.assertIn("TIMEZONE_CONVERSION_EXCLUSION_UNDOCUMENTED", {f.code for f in check_sources((source,), ())})
        self.assertIsNone(source.source_timezone)

    def test_missing_and_duplicate_source_ids(self):
        sources = (SourceTimetable(), SourceTimetable(source_id="duplicate"), SourceTimetable(source_id="duplicate"))
        codes = {f.code for f in check_sources(sources, ())}
        self.assertTrue({"SOURCE_ID_MISSING", "DUPLICATE_SOURCE_ID"}.issubset(codes))

    def test_missing_page_reference(self):
        self.assertIn("UNKNOWN_PAGE_SOURCE", {f.code for f in check_sources((), (page(),))})

    def test_document_link_preserves_distinct_page_source_ids(self):
        from backend.research.integrity import source_for_page
        from backend.research.models import SourcePage
        source = SourceTimetable(source_id="synthetic-container", source_document_id="synthetic-document")
        data = page().model_dump()
        data.update(source_id="synthetic-page-source", source_document_id="synthetic-document")
        linked = SourcePage(**data)
        self.assertEqual(source_for_page((source,), linked), source)
        self.assertEqual(linked.source_id, "synthetic-page-source")
        other = SourceTimetable(source_id="synthetic-other", source_document_id="synthetic-document")
        self.assertIsNone(source_for_page((source, other), linked))

    def test_duplicate_page_ids(self):
        self.assertIn("DUPLICATE_PAGE_ID", {f.code for f in check_splits((page(), page()), registered_allocation=False)})

    def test_dependency_group_overlap(self):
        pages = (page(), page("page_002", "test", "group1"))
        self.assertIn("DEPENDENCY_GROUP_SPLIT_LEAKAGE", {f.code for f in check_splits(pages, registered_allocation=False)})

    def test_related_continued_cropped_revised_pages_cannot_cross_split(self):
        for relation in ("CONTINUATION", "REVISION", "CROP", "DERIVATIVE"):
            pages = (page(related_page_ids=("page_002",), relationship=relation), page("page_002", "test", "group2"))
            self.assertIn("RELATED_PAGE_SPLIT_LEAKAGE", {f.code for f in check_splits(pages, registered_allocation=False)})

    def test_repeated_timetable_identity_not_independent(self):
        pages = (page(timetable_id="synthetic-table"), page("page_002", "test", "group2", timetable_id="synthetic-table"))
        self.assertIn("REPEATED_TIMETABLE_SPLIT_LEAKAGE", {f.code for f in check_splits(pages, registered_allocation=False)})

    def test_registered_counts_are_checks_not_regenerated_assignments(self):
        original = page()
        self.assertIn("REGISTERED_PAGE_ALLOCATION_MISMATCH", {f.code for f in check_splits((original,))})
        self.assertEqual(original.partition, "development")

    def test_hash_verification_and_tampering(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            path = root / "synthetic.txt"
            path.write_bytes(b"SYNTHETIC NON-SENSITIVE TEST DATA")
            artifact = Artifact(artifact_id="test", relative_path=path.name, sha256=file_sha256(path), kind="test", origin="AUTHENTIC")
            self.assertEqual(check_artifacts(root, (artifact,)), ())
            path.write_bytes(b"SYNTHETIC CHANGED TEST DATA")
            self.assertIn("ARTIFACT_HASH_MISMATCH", {f.code for f in check_artifacts(root, (artifact,))})

    def test_missing_file_and_path_escape(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            artifact = Artifact(artifact_id="test", relative_path="missing.json", sha256=HASH, kind="test", origin="AUTHENTIC")
            self.assertIn("MISSING_FILE_REFERENCE", {f.code for f in check_artifacts(root, (artifact,))})
            for relative in ("../escape", "C:\\outside\\file", "..\\escape"):
                with self.assertRaises(ValueError):
                    safe_path(root, relative)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.pages = (page(), page("page_002", "validation", "group2"), page("page_003", "test", "group3"),
                      page("page_004", "development", "group4"))
        self.policy = AuditPolicy(version="SYNTHETIC_TEST_POLICY", target_pages=2, seed=20260902,
                                  strata_quotas=(("synthetic-programme", "simple", 2),),
                                  registered_spec_artifact_id="synthetic-spec", approval=stamp())

    def prepare(self, pages=None, policy=None):
        return prepare_audit(pages or self.pages, policy or self.policy, stamp(), version="SYNTHETIC_TEST_SAMPLE")

    def test_unapproved_policy_cannot_finalize(self):
        with self.assertRaisesRegex(AuditError, "UNAPPROVED"):
            self.prepare(policy=AuditPolicy(version="unapproved-template"))

    def test_deterministic_sampling_and_input_order_independence(self):
        first, second = self.prepare(), self.prepare(pages=tuple(reversed(self.pages)))
        self.assertEqual(first.page_ids, second.page_ids)
        self.assertEqual(first.frame_digest, second.frame_digest)
        self.assertEqual(len(first.page_ids), 2)
        self.assertIn("page_003", first.page_ids)
        self.assertEqual(first.seed, 20260902)

    def test_dependency_groups_sampled_whole(self):
        pages = (page(), page("page_002", "development", "group1"), page("page_003", "test", "group3"))
        with self.assertRaisesRegex(AuditError, "INFEASIBLE"):
            self.prepare(pages=pages)

    def test_unknown_strata_and_risk_metadata_block_selection(self):
        data = self.pages[0].model_dump()
        data["complexity"] = None
        from backend.research.models import SourcePage
        with self.assertRaisesRegex(AuditError, "METADATA_MISSING"):
            self.prepare(pages=(SourcePage(**data), *self.pages[1:]))

    def test_all_represented_high_risk_categories_required(self):
        data = self.pages[0].model_dump()
        data["high_risk_categories"] = ("synthetic-merged-cell",)
        from backend.research.models import SourcePage
        pages = (SourcePage(**data), *self.pages[1:])
        with self.assertRaisesRegex(AuditError, "REGISTRY_MISMATCH"):
            self.prepare(pages=pages)
        policy_data = self.policy.model_dump()
        policy_data["high_risk_categories"] = ("synthetic-merged-cell",)
        sample = self.prepare(pages=pages, policy=AuditPolicy(**policy_data))
        self.assertIn("page_001", sample.page_ids)

    def test_no_actual_independent_audit_from_selection(self):
        progress = audit_progress(self.prepare(), (), self.pages)
        self.assertEqual(progress["independently_audited_pages"], 0)
        self.assertEqual(progress["verified_sessions"], 0)
        self.assertEqual(progress["status"], "INCOMPLETE")

    def test_simulation_is_reproducible_diagnostic_synthetic_only(self):
        sample = self.prepare()
        first = simulate_detection(self.pages, sample, stamp(), iterations=100)
        second = simulate_detection(self.pages, sample, stamp(), iterations=100)
        self.assertEqual(first, second)
        self.assertEqual({s["affected_page_percent"] for s in first["scenarios"]}, {5, 10, 20})
        self.assertTrue(first["diagnostic_only"])
        self.assertEqual(first["output_digest"], record_digest({k: v for k, v in first.items() if k != "output_digest"}))


class ReadinessTests(unittest.TestCase):
    def test_empty_schema_valid_bundle_is_not_ready(self):
        report = assess_pre01(ResearchBundle(origin="AUTHENTIC"), Path.cwd())
        self.assertEqual(report.status, "NOT READY")
        self.assertTrue({"SOURCE_MANIFEST_MISSING", "GOLD_FREEZE_MISSING", "INDEPENDENT_AUDIT_INCOMPLETE", "REVIEWER_ASSIGNMENTS_UNAPPROVED"}.issubset(report.reason_codes))
        self.assertEqual(report.independently_audited_pages, 0)

    def test_legacy_gold_flags_cannot_create_review_evidence(self):
        bundle = ResearchBundle(origin="AUTHENTIC", sources=(SourceTimetable(source_id="synthetic-source", annotation_status="RECONCILED", audit_status="INDEPENDENTLY_AUDITED"),), pages=(page(),))
        report = assess_pre01(bundle, Path.cwd())
        self.assertEqual(report.verified_reference_sessions, 0)
        self.assertEqual(report.independently_audited_pages, 0)
        self.assertEqual(report.status, "NOT READY")

    def test_synthetic_verified_workflow_not_real_gold_readiness(self):
        wf = verified()
        report = assess_pre01(ResearchBundle(origin="SYNTHETIC", workflows=(wf,)), Path.cwd())
        self.assertEqual(report.status, "NOT READY")
        self.assertIn("SYNTHETIC_INPUT_NOT_RESEARCH_EVIDENCE", report.reason_codes)

    def test_historical_exposure_blocks_and_remains_disclosed(self):
        access = HistoricalAccess(record_id="synthetic-test-access", partition="test", purpose="synthetic earlier access",
                                  artifact_id="missing-proof", label_access="UNKNOWN", predictions_opened="YES")
        report = assess_pre01(ResearchBundle(origin="AUTHENTIC", history_access=(access,)), Path.cwd())
        self.assertIn("HISTORICAL_HELDOUT_EXPOSURE_UNRESOLVED", report.reason_codes)
        self.assertEqual(report.historical_access_records, 1)

    def test_resolution_without_protocol_amendment_insufficient(self):
        access = HistoricalAccess(record_id="synthetic-test-access", partition="test", purpose="test", artifact_id="missing",
                                  resolution=stamp())
        report = assess_pre01(ResearchBundle(origin="SYNTHETIC", history_access=(access,)), Path.cwd())
        self.assertIn("HISTORICAL_EXPOSURE_AMENDMENT_REQUIRED", report.reason_codes)

    def test_readiness_report_roundtrip_and_deterministic_digest(self):
        bundle = ResearchBundle(origin="AUTHENTIC", ambiguities=("unresolved source authority",))
        first, second = assess_pre01(bundle, Path.cwd()), assess_pre01(bundle, Path.cwd())
        self.assertEqual(first, second)
        self.assertIn("OUTSTANDING_AMBIGUITIES", first.reason_codes)
        self.assertEqual(json.loads(first.model_dump_json())["status"], "NOT READY")

    def test_cli_invalid_input_fails_closed_without_private_content(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            path = root / "synthetic-invalid.json"
            path.write_text('{"private_secret":"SYNTHETIC_SECRET"}', encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                result = main(["pre01", "--bundle", str(path), "--evidence-root", str(root)])
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(out.getvalue())["report"]["status"], "NOT READY")
            self.assertNotIn("SYNTHETIC_SECRET", out.getvalue())

    def test_cli_empty_bundle_returns_actual_blockers_and_nonzero(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            path = root / "synthetic-empty.json"
            path.write_text(ResearchBundle(origin="SYNTHETIC").model_dump_json(), encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                result = main(["pre01", "--bundle", str(path), "--evidence-root", str(root)])
            self.assertEqual(result, 1)
            self.assertIn("GOLD_FREEZE_MISSING", json.loads(out.getvalue())["report"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
