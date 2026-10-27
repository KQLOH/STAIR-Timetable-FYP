"""Read a fixed metadata allowlist. Never open held-out labels or predictions."""

import csv
from datetime import datetime
import json
from pathlib import Path
import re

from backend.research.integrity import file_sha256, safe_path
from backend.research.models import Artifact, HistoricalAccess, ResearchBundle, SourcePage, SourceTimetable

METADATA_FILES = {
    "baseline:sources": ("data/manifests/sources.csv", "source_manifest"),
    "baseline:splits": ("data/manifests/splits.csv", "split_manifest"),
    "baseline:annotation-manifest": ("data/manifests/annotation_manifest.csv", "provisional_annotation_summary"),
    "baseline:evidence-manifest": ("data/manifests/evidence_manifest.csv", "evidence_manifest"),
    "baseline:dataset-reference": ("dataset_reference.json", "legacy_dataset_claims"),
    "baseline:validation-report": ("data/STAIR_Timetable_Dataset_v1/documentation/validation_report.json", "preannotation_status"),
    "baseline:test-access": ("outputs/provenance/access_log_test.json", "historical_access"),
    "baseline:invalid-test-run": ("outputs/invalid_runs/B3_test_20260827_022607/run_metadata.json", "historical_access"),
}


def _csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_baseline_metadata(root: Path) -> tuple[ResearchBundle, dict]:
    """Keep IDs/partitions verbatim. Legacy 'confirmed' never creates workflow evidence."""
    artifacts, missing = [], []
    declared_path = safe_path(root, METADATA_FILES["baseline:dataset-reference"][0])
    declared = json.loads(declared_path.read_text(encoding="utf-8")) if declared_path.is_file() else {}
    declared_hash_keys = {"baseline:splits": "split_manifest_sha256", "baseline:annotation-manifest": "annotation_manifest_sha256",
                          "baseline:evidence-manifest": "evidence_manifest_sha256"}
    for artifact_id, (relative, kind) in METADATA_FILES.items():
        path = safe_path(root, relative)
        if path.is_file():
            artifacts.append(Artifact(artifact_id=artifact_id, relative_path=relative,
                                      sha256=declared.get(declared_hash_keys.get(artifact_id, "")) or file_sha256(path), kind=kind, origin="AUTHENTIC"))
        else:
            missing.append(artifact_id)
    sources_path, splits_path = safe_path(root, METADATA_FILES["baseline:sources"][0]), safe_path(root, METADATA_FILES["baseline:splits"][0])
    source_rows = _csv(sources_path) if sources_path.is_file() else []
    split_rows = _csv(splits_path) if splits_path.is_file() else []
    pages = []
    for row in split_rows:
        match = re.fullmatch(r"page_(\d+)", row.get("page_id", ""))
        pages.append(SourcePage(
            page_id=row["page_id"], source_id=row["source_id"], page_number=int(match.group(1)) if match else None,
            source_document_id=row.get("source_document_id") or None,
            partition=row["split"], dependency_group=row.get("leakage_group") or None,
            timetable_id=row.get("timetable_id") or None, programme_family=row.get("program_family") or None,
            timetable_bearing=True if row.get("sample_status") == "included" else False if row.get("sample_status") == "excluded" else None,
            # Exclusion classification is a provisional declaration, not an audit.
            exclusion_reason="baseline manifest declares excluded/control" if row.get("sample_status") == "excluded" else None,
            metadata_evidence_ids=("baseline:splits",),
        ))
    sources = []
    for row in source_rows:
        partitions = {p.partition for p in pages if p.source_id == row.get("source_id") or p.source_document_id == row.get("source_document_id")}
        sources.append(SourceTimetable(
            source_id=row.get("source_id") or None, source_document_id=row.get("source_document_id") or None,
            original_filename=row.get("source_document_id") or None, source_path=row.get("source_path") or None,
            original_sha256=row.get("checksum_sha256") or None, source_format=row.get("source_modality") or None,
            page_count=int(row["total_pages"]) if row.get("total_pages") else None,
            partition="mixed" if len(partitions) > 1 else next(iter(partitions), None),
            annotation_status="PROVISIONAL", metadata_evidence_ids=("baseline:sources",),
        ))
    history = []
    for artifact in artifacts:
        if artifact.kind != "historical_access":
            continue
        record = json.loads(safe_path(root, artifact.relative_path).read_text(encoding="utf-8"))
        timestamp = record.get("export_time")
        history.append(HistoricalAccess(
            record_id=artifact.artifact_id, partition=record["split"], purpose=record.get("status", "CANDIDATE_EXPORT"),
            observed_at=datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else None,
            artifact_id=artifact.artifact_id,
            # A guard's access_violation=false is not proof of an untouched test split.
            label_access="UNKNOWN", predictions_opened="UNKNOWN",
        ))
    claims = {}
    path = safe_path(root, METADATA_FILES["baseline:dataset-reference"][0])
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        claims["legacy_claimed_verified_annotations"] = data.get("verified_annotations")
    path = safe_path(root, METADATA_FILES["baseline:annotation-manifest"][0])
    if path.is_file():
        rows = _csv(path)
        claims["declared_preannotation_rows"] = sum(int(r.get("total_events") or 0) for r in rows)
        claims["legacy_annotation_statuses"] = sorted({r.get("annotation_status", "") for r in rows})
    return ResearchBundle(origin="AUTHENTIC", sources=tuple(sources), pages=tuple(pages),
                          artifacts=tuple(artifacts), history_access=tuple(history),
                          split_manifest_artifact_id="baseline:splits" if splits_path.is_file() else None), {
        "mode": "METADATA_ONLY_NO_LABELS_OR_PREDICTIONS", "missing_metadata_artifacts": missing,
        "legacy_claims_not_verification": claims,
        "manifest_source_ids": sorted({s.source_id for s in sources if s.source_id}),
        "page_source_id_count": len({p.source_id for p in pages}),
        "page_source_id_examples": sorted({p.source_id for p in pages})[:2],
        "document_link_basis": "explicit source_document_id column; page-source IDs unchanged",
        "metadata_hash_basis": "dataset_reference declared hashes where supplied; observed fingerprints otherwise; no gold freeze claim",
        "documented_independent_audit_completed": 0, "documented_independent_audit_target": 29,
        "audit_basis": "approved documents; no new authentic audit submissions found in the inspected metadata",
    }
