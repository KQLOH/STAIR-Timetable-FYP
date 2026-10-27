"""Read-only integrity guards adapted from inspected baseline guards."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from backend.research.models import Artifact, Finding, SourcePage, SourceTimetable

REGISTERED_PAGE_ALLOCATION = {"development": 45, "validation": 30, "test": 93, "excluded": 17}


def file_sha256(path: Path) -> str:
    """Streaming port of evaluation/test_seal.py:compute_file_sha256."""
    h = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def record_digest(value) -> str:
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or ":" in relative:
        raise ValueError("EXTERNAL_FILE_REFERENCE")
    root = root.resolve()
    path = (root / relative.replace("\\", "/")).resolve()
    if not path.is_relative_to(root):
        raise ValueError("FILE_REFERENCE_ESCAPES_ROOT")
    return path


def check_artifacts(root: Path, artifacts: tuple[Artifact, ...]) -> tuple[Finding, ...]:
    findings = []
    seen = set()
    for artifact in artifacts:
        if artifact.artifact_id in seen:
            findings.append(Finding(code="DUPLICATE_ARTIFACT_ID"))
        seen.add(artifact.artifact_id)
        if artifact.origin != "AUTHENTIC":
            findings.append(Finding(code="SYNTHETIC_EVIDENCE", scope=artifact.artifact_id))
        try:
            path = safe_path(root, artifact.relative_path)
            if not path.is_file():
                findings.append(Finding(code="MISSING_FILE_REFERENCE", scope=artifact.artifact_id))
            elif file_sha256(path) != artifact.sha256:
                findings.append(Finding(code="ARTIFACT_HASH_MISMATCH", scope=artifact.artifact_id))
        except (ValueError, OSError):
            findings.append(Finding(code="UNSAFE_OR_UNREADABLE_FILE_REFERENCE", scope=artifact.artifact_id))
    return tuple(findings)


def source_for_page(sources: tuple[SourceTimetable, ...], page: SourcePage) -> SourceTimetable | None:
    """Preserve page-source IDs; resolve only explicit, unambiguous document links."""
    matches = [s for s in sources if s.source_id == page.source_id]
    if not matches and page.source_document_id:
        matches = [s for s in sources if s.source_document_id == page.source_document_id]
    return matches[0] if len(matches) == 1 else None


def check_sources(sources: tuple[SourceTimetable, ...], pages: tuple[SourcePage, ...]) -> tuple[Finding, ...]:
    findings = []
    ids = [s.source_id for s in sources]
    if not sources:
        findings.append(Finding(code="SOURCE_MANIFEST_MISSING"))
    if None in ids:
        findings.append(Finding(code="SOURCE_ID_MISSING"))
    if len(set(ids)) != len(ids):
        findings.append(Finding(code="DUPLICATE_SOURCE_ID"))
    for source in sources:
        required = ("source_document_id", "institution", "programme", "semester", "academic_year", "original_filename",
                    "source_path", "original_sha256", "source_format", "page_count", "template_family", "captured_on", "acquisition_channel")
        for name in required:
            if getattr(source, name) is None or getattr(source, name) == "":
                findings.append(Finding(code="SOURCE_METADATA_MISSING", scope=name))
        if source.usage_permission not in {"RESTRICTED_ACADEMIC", "PERMITTED"} or not source.permission_evidence_ids:
            findings.append(Finding(code="USAGE_PERMISSION_UNPROVEN"))
        if not source.metadata_evidence_ids:
            findings.append(Finding(code="SOURCE_PROVENANCE_MISSING"))
        if source.timezone_conversion_scope == "NOT_ASSESSED":
            findings.append(Finding(code="SOURCE_TIMEZONE_AUTHORITY_UNASSESSED"))
        elif source.timezone_conversion_scope == "AUTHORISED" and (not source.source_timezone or not source.timezone_evidence_ids):
            findings.append(Finding(code="SOURCE_TIMEZONE_AUTHORITY_MISSING"))
        elif source.timezone_conversion_scope == "DISABLED_UNRESOLVED" and not source.timezone_evidence_ids:
            findings.append(Finding(code="TIMEZONE_CONVERSION_EXCLUSION_UNDOCUMENTED"))
        members = [p for p in pages if source_for_page(sources, p) == source]
        if source.page_count is not None and len(members) != source.page_count:
            findings.append(Finding(code="SOURCE_PAGE_COUNT_MISMATCH"))
        numbers = [p.page_number for p in members]
        if source.page_count is not None and set(numbers) != set(range(1, source.page_count + 1)):
            findings.append(Finding(code="SOURCE_PAGE_NUMBER_COVERAGE_MISMATCH"))
    for page in pages:
        if source_for_page(sources, page) is None:
            findings.append(Finding(code="UNKNOWN_PAGE_SOURCE"))
    return tuple(findings)


def check_splits(pages: tuple[SourcePage, ...], *, registered_allocation=True) -> tuple[Finding, ...]:
    """Preserve assignments. Check groups and related/repeated timetable links."""
    findings = []
    ids = [p.page_id for p in pages]
    if len(set(ids)) != len(ids):
        findings.append(Finding(code="DUPLICATE_PAGE_ID"))
    if not pages:
        findings.append(Finding(code="SPLIT_MANIFEST_MISSING"))
    by_id = {p.page_id: p for p in pages}
    groups, timetables = defaultdict(set), defaultdict(set)
    for page in pages:
        if page.dependency_group is None:
            findings.append(Finding(code="DEPENDENCY_GROUP_MISSING"))
        else:
            groups[page.dependency_group].add(page.partition)
        if page.timetable_id:
            timetables[page.timetable_id].add(page.partition)
        for linked_id in page.related_page_ids:
            linked = by_id.get(linked_id)
            if linked is None:
                findings.append(Finding(code="RELATED_PAGE_MISSING"))
            elif linked.partition != page.partition or linked.dependency_group != page.dependency_group:
                findings.append(Finding(code="RELATED_PAGE_SPLIT_LEAKAGE"))
        if page.relationship != "NONE" and not page.related_page_ids:
            findings.append(Finding(code="RELATIONSHIP_LINK_MISSING"))
        if page.timetable_bearing is None:
            findings.append(Finding(code="TIMETABLE_PAGE_CLASSIFICATION_UNKNOWN"))
        if page.partition == "excluded" and (page.timetable_bearing is not False or not page.exclusion_reason):
            findings.append(Finding(code="CONTROL_EXCLUSION_UNEXPLAINED"))
        if page.partition != "excluded" and page.timetable_bearing is not True:
            findings.append(Finding(code="PARTITION_PAGE_CLASSIFICATION_MISMATCH"))
    if any(len(splits) > 1 for splits in groups.values()):
        findings.append(Finding(code="DEPENDENCY_GROUP_SPLIT_LEAKAGE"))
    if any(len(splits) > 1 for splits in timetables.values()):
        findings.append(Finding(code="REPEATED_TIMETABLE_SPLIT_LEAKAGE"))
    if registered_allocation and dict(Counter(p.partition for p in pages)) != REGISTERED_PAGE_ALLOCATION:
        findings.append(Finding(code="REGISTERED_PAGE_ALLOCATION_MISMATCH"))
    return tuple(findings)
