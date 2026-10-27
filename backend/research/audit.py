"""Audit preparation only. The detailed algorithm needs explicit approval.

The Report registers risk coverage, programme/complexity strata, whole dependency
groups, 29 pages and seed 20260902. It does not supply a frozen quota/tie policy.
This deterministic implementation is provisional until that policy is approved.
"""

from collections import Counter, defaultdict
from random import Random

from backend.research.integrity import check_splits, record_digest
from backend.research.models import AuditPolicy, AuditSample, Attestation, PageWorkflow, SourcePage, WorkflowState
from backend.research.workflow import _latest, validate_history


class AuditError(ValueError):
    pass


def prepare_audit(pages: tuple[SourcePage, ...], policy: AuditPolicy, finalized_by: Attestation,
                  *, version: str, previous_sample: AuditSample | None = None) -> AuditSample:
    if policy.approval is None or policy.registered_spec_artifact_id is None or not policy.strata_quotas:
        raise AuditError("AUDIT_SPEC_UNAPPROVED")
    if policy.approval.origin != finalized_by.origin:
        raise AuditError("AUDIT_ORIGIN_MISMATCH")
    if finalized_by.origin == "AUTHENTIC" and (policy.target_pages != 29 or policy.seed != 20260902):
        raise AuditError("REGISTERED_AUDIT_CONDITIONS_CHANGED")
    if previous_sample and previous_sample.version == version:
        raise AuditError("AUDIT_NEW_VERSION_REQUIRED")
    if check_splits(pages, registered_allocation=False):
        raise AuditError("AUDIT_FRAME_INTEGRITY_BLOCKED")
    eligible = sorted((p for p in pages if p.timetable_bearing is True and p.partition != "excluded"), key=lambda p: p.page_id)
    if any(p.programme_family is None or p.complexity is None or p.high_risk_categories is None for p in eligible):
        raise AuditError("AUDIT_STRATA_OR_RISK_METADATA_MISSING")
    quotas = {(programme, complexity): count for programme, complexity, count in policy.strata_quotas}
    if len(quotas) != len(policy.strata_quotas) or any(v < 0 for v in quotas.values()) or sum(quotas.values()) != policy.target_pages:
        raise AuditError("AUDIT_QUOTAS_INVALID")
    represented_risks = {r for p in eligible for r in p.high_risk_categories}
    if set(policy.high_risk_categories) != represented_risks:
        raise AuditError("AUDIT_HIGH_RISK_REGISTRY_MISMATCH")
    groups = defaultdict(list)
    for page in eligible:
        if (page.programme_family, page.complexity) not in quotas:
            raise AuditError("AUDIT_STRATUM_QUOTA_MISSING")
        groups[page.dependency_group].append(page)
    keys = sorted(groups)
    Random(policy.seed).shuffle(keys)
    # Prioritize risk-bearing groups, preserving the seeded order within ties.
    keys.sort(key=lambda k: -len({r for p in groups[k] for r in p.high_risk_categories}))
    nodes = 0

    def select(index, selected, counts, covered, has_test):
        nonlocal nodes
        nodes += 1
        if nodes > 100000:
            raise AuditError("AUDIT_SELECTION_SEARCH_LIMIT_NO_FINALIZATION")
        total = sum(counts.values())
        if total == policy.target_pages:
            return selected if covered == represented_risks and has_test else None
        if index == len(keys):
            return None
        remaining = [p for key in keys[index:] for p in groups[key]]
        available = Counter((p.programme_family, p.complexity) for p in remaining)
        if any(counts.get(k, 0) + available.get(k, 0) < v for k, v in quotas.items()):
            return None
        if not represented_risks.issubset(covered | {r for p in remaining for r in p.high_risk_categories}):
            return None
        group = groups[keys[index]]
        added = Counter((p.programme_family, p.complexity) for p in group)
        new_counts = counts + added
        if all(new_counts[k] <= quotas[k] for k in new_counts):
            found = select(index + 1, (*selected, keys[index]), new_counts,
                           covered | {r for p in group for r in p.high_risk_categories},
                           has_test or any(p.partition == "test" for p in group))
            if found is not None:
                return found
        return select(index + 1, selected, counts, covered, has_test)

    chosen = select(0, (), Counter(), set(), False)
    if chosen is None:
        raise AuditError("AUDIT_GROUP_QUOTAS_OR_HELDOUT_REPRESENTATION_INFEASIBLE")
    return AuditSample(version=version, policy_digest=record_digest(policy),
                       frame_digest=record_digest([p.model_dump(mode="json") for p in sorted(pages, key=lambda p: p.page_id)]),
                       seed=policy.seed, page_ids=tuple(sorted(p.page_id for k in chosen for p in groups[k])),
                       dependency_groups=tuple(sorted(chosen)), finalized_by=finalized_by,
                       predecessor_sample_digest=record_digest(previous_sample) if previous_sample else None)


def audit_progress(sample: AuditSample | None, workflows: tuple[PageWorkflow, ...], pages: tuple[SourcePage, ...]) -> dict:
    """Count actual valid workflow records, never legacy page-status flags."""
    selected = set(sample.page_ids) if sample else set()
    verified = [w for w in workflows if w.page_id in selected and w.state == WorkflowState.INDEPENDENTLY_VERIFIED
                and w.verification is not None and w.final_inventory is not None and _latest(w, "INDEPENDENT") is not None and validate_history(w)]
    by_page = {p.page_id: p for p in pages}
    return {
        "selected_pages": len(selected), "independently_audited_pages": len({w.page_id for w in verified}),
        "verified_sessions": sum(sum(s.disposition == "IN_SCOPE" for s in w.final_inventory.sessions) for w in verified),
        "audited_fields": sum(len(label.revision.observations) for w in verified for label in _latest(w, "INDEPENDENT").labels),
        "page_progress": [{"page_id": w.page_id, "state": w.state.value,
                           "source_sessions": len(w.final_inventory.sessions),
                           "excluded_sessions": sum(s.disposition == "EXCLUDED_AMBIGUOUS" for s in w.final_inventory.sessions),
                           "non_event_regions": sum(r.kind == "NON_EVENT" for r in w.final_inventory.regions)} for w in verified],
        "by_partition": dict(Counter(by_page[w.page_id].partition for w in verified if w.page_id in by_page)),
        "unresolved_disagreements": sum(len(w.disagreements) - len(w.adjudications) for w in workflows if w.page_id in selected),
        "status": "COMPLETE" if sample is not None and len(verified) == len(selected) == 29 else "INCOMPLETE",
    }
