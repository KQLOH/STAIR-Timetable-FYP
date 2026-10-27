"""SYNTHETIC metadata packages; sealed-label files are access tripwires."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.research.baseline_reader import load_baseline_metadata
from backend.research.integrity import file_sha256
from tests.research_fixtures import synthetic_directory


class BaselineReaderTests(unittest.TestCase):
    def create_package(self, root):
        def write(relative, content):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        write("source/synthetic.pdf", "SYNTHETIC NON-SENSITIVE SOURCE BYTES")
        digest = file_sha256(root / "source/synthetic.pdf")
        write("data/manifests/sources.csv", "source_id,source_document_id,source_path,checksum_sha256,total_pages,source_modality\n"
              f"SYNTHETIC_CONTAINER,synthetic.pdf,source/synthetic.pdf,{digest},1,synthetic_pdf\n")
        write("data/manifests/splits.csv", "page_id,source_id,source_document_id,split,leakage_group,timetable_id,program_family,sample_status\n"
              "page_001,SYNTHETIC_PAGE_SOURCE,synthetic.pdf,test,SYNTHETIC_GROUP,SYNTHETIC_TABLE,SYNTHETIC_PROGRAMME,included\n")
        write("data/manifests/annotation_manifest.csv", "total_events,annotation_status\n999,confirmed\n")
        write("dataset_reference.json", json.dumps({"verified_annotations": 999}))
        write("data/annotations/reference_events.csv", "SYNTHETIC SEALED LABEL TRIPWIRE - DO NOT READ")
        write("outputs/predictions/test.json", "SYNTHETIC SEALED PREDICTION TRIPWIRE - DO NOT READ")

    def test_preserves_source_ids_partition_and_provisional_status(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            self.create_package(root)
            bundle, observation = load_baseline_metadata(root)
            self.assertEqual(bundle.sources[0].source_id, "SYNTHETIC_CONTAINER")
            self.assertEqual(bundle.pages[0].source_id, "SYNTHETIC_PAGE_SOURCE")
            self.assertEqual(bundle.pages[0].partition, "test")
            self.assertEqual(bundle.sources[0].annotation_status, "PROVISIONAL")
            self.assertEqual(bundle.workflows, ())
            self.assertEqual(observation["legacy_claims_not_verification"]["legacy_claimed_verified_annotations"], 999)

    def test_never_opens_sealed_labels_or_predictions(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            self.create_package(root)
            original_open = Path.open
            def guarded_open(path, *args, **kwargs):
                if "reference_events" in path.name or path.parent.name == "predictions":
                    raise AssertionError("Sealed development input was opened")
                return original_open(path, *args, **kwargs)
            with patch.object(Path, "open", guarded_open):
                bundle, observation = load_baseline_metadata(root)
            self.assertEqual(bundle.provisional_annotations, ())
            self.assertEqual(observation["mode"], "METADATA_ONLY_NO_LABELS_OR_PREDICTIONS")

    def test_missing_metadata_is_reported_without_regeneration(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            bundle, observation = load_baseline_metadata(root)
            self.assertEqual(bundle.sources, ())
            self.assertTrue(observation["missing_metadata_artifacts"])
            self.assertEqual(list(root.iterdir()), [])

    def test_declared_manifest_hash_not_replaced_with_observed_hash(self):
        with synthetic_directory() as directory:
            root = Path(directory)
            self.create_package(root)
            (root / "dataset_reference.json").write_text(json.dumps({"split_manifest_sha256": "b" * 64}), encoding="utf-8")
            bundle, _ = load_baseline_metadata(root)
            from backend.research.integrity import check_artifacts
            self.assertIn("ARTIFACT_HASH_MISMATCH", {f.code for f in check_artifacts(root, bundle.artifacts)})


if __name__ == "__main__":
    unittest.main()
