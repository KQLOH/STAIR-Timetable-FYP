"""Source-first inventory/reconciliation, with no prediction-driven inventory."""

from backend.research.models import Finding, PageInventory, ProvisionalAnnotation, ReconciliationLink


def compare_provisional(inventories: tuple[PageInventory, ...], annotations: tuple[ProvisionalAnnotation, ...],
                        links: tuple[ReconciliationLink, ...]) -> tuple[Finding, ...]:
    """Only explicit human correspondences connect records. Neither side wins."""
    sessions = [s.session_id for inventory in inventories for s in inventory.sessions]
    annotation_ids = [a.annotation_id for a in annotations]
    findings = []
    for ids, code in ((sessions, "DUPLICATE_SOURCE_SESSION_ID"), (annotation_ids, "DUPLICATE_PROVISIONAL_ANNOTATION_ID")):
        if len(set(ids)) != len(ids):
            findings.append(Finding(code=code, count=len(ids) - len(set(ids))))
    source_pages = {s.session_id: i.page_id for i in inventories for s in i.sessions}
    annotation_pages = {a.annotation_id: a.page_id for a in annotations}
    linked_sessions, linked_annotations = [], []
    for link in links:
        if set(link.session_ids) - set(sessions) or set(link.annotation_ids) - set(annotation_ids):
            findings.append(Finding(code="RECONCILIATION_UNKNOWN_REFERENCE"))
        if len({source_pages.get(s) for s in link.session_ids} | {annotation_pages.get(a) for a in link.annotation_ids}) > 1:
            findings.append(Finding(code="RECONCILIATION_CROSS_PAGE"))
        if link.disposition == "CORRESPONDENCE" and (len(link.session_ids) != 1 or len(link.annotation_ids) != 1):
            findings.append(Finding(code="RECONCILIATION_NOT_ONE_TO_ONE"))
        if link.disposition == "SPLIT_CELL" and (len(link.session_ids) < 2 or len(link.annotation_ids) != 1):
            findings.append(Finding(code="INVALID_MULTI_SESSION_RECONCILIATION"))
        if link.disposition == "MERGED_ROWS" and (len(link.session_ids) != 1 or len(link.annotation_ids) < 2):
            findings.append(Finding(code="INVALID_MERGED_RECONCILIATION"))
        if link.disposition == "MISSING_ANNOTATION" and (not link.session_ids or link.annotation_ids):
            findings.append(Finding(code="INVALID_MISSING_ANNOTATION_RECORD"))
        if link.disposition == "EXTRA_ANNOTATION" and (link.session_ids or not link.annotation_ids):
            findings.append(Finding(code="INVALID_EXTRA_ANNOTATION_RECORD"))
        if link.disposition == "EXCLUDED_AMBIGUOUS" and not link.exclusion_region_ids:
            findings.append(Finding(code="EXCLUSION_SCOPE_MISSING"))
        linked_sessions.extend(link.session_ids)
        linked_annotations.extend(link.annotation_ids)
    for all_ids, covered, code in ((sessions, linked_sessions, "SOURCE_SESSION_UNRECONCILED"),
                                   (annotation_ids, linked_annotations, "PROVISIONAL_ROW_UNRECONCILED")):
        missing = set(all_ids) - set(covered)
        if missing:
            findings.append(Finding(code=code, count=len(missing)))
        if len(covered) != len(set(covered)):
            findings.append(Finding(code="DUPLICATE_RECONCILIATION_LINK"))
    if not inventories:
        findings.append(Finding(code="SOURCE_FIRST_INVENTORY_MISSING"))
    return tuple(findings)


def inventory_findings(inventory: PageInventory) -> tuple[Finding, ...]:
    findings = []
    if inventory.coverage_attestation is None:
        findings.append(Finding(code="INVENTORY_COVERAGE_UNATTESTED", scope=inventory.page_id))
    unresolved = sum(r.kind in {"UNKNOWN", "UNREADABLE"} for r in inventory.regions)
    if unresolved:
        findings.append(Finding(code="INVENTORY_UNRESOLVED_REGION", count=unresolved, scope=inventory.page_id))
    return tuple(findings)
