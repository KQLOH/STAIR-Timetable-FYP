"""Pre-adjudication raw agreement counts; no evaluator or correctness claim."""

from collections import Counter

from backend.models.event import EventContent
from backend.research.integrity import record_digest
from backend.research.workflow import WorkflowError, _latest


def agreement_summary(workflow) -> dict:
    if workflow.comparison is None:
        raise WorkflowError("PRE_ADJUDICATION_COMPARISON_REQUIRED")
    primary, independent = _latest(workflow, "PRIMARY"), _latest(workflow, "INDEPENDENT")
    a, b = {l.label_id: l for l in primary.labels}, {l.label_id: l for l in independent.labels}
    pairs = workflow.comparison.label_pairs
    tables = {}
    for field in EventContent.model_fields:
        table = Counter((next(o.state.value for o in a[left].revision.observations if o.field_name == field),
                         next(o.state.value for o in b[right].revision.observations if o.field_name == field)) for left, right in pairs)
        count = sum(table.values())
        observed = sum(n for (left, right), n in table.items() if left == right) / count if count else None
        left_counts, right_counts = Counter(), Counter()
        for (left, right), n in table.items():
            left_counts[left] += n
            right_counts[right] += n
        expected = sum(left_counts[k] * right_counts[k] for k in left_counts.keys() | right_counts.keys()) / count ** 2 if count else None
        kappa = (observed - expected) / (1 - expected) if count and expected != 1 else None
        tables[field] = {"state_contingency": [[left, right, n] for (left, right), n in sorted(table.items())],
                         "paired_labels": count, "nominal_state_kappa": kappa,
                         "kappa_status": "DEFINED" if kappa is not None else "UNDEFINED",
                         "normalized_value_agreements": sum(getattr(a[left].revision.content, field) is not None
                                                             and getattr(a[left].revision.content, field) == getattr(b[right].revision.content, field) for left, right in pairs)}
    return {"page_id": workflow.page_id, "origin": workflow.origin, "comparison_digest": record_digest(workflow.comparison),
            "primary_source_sessions": len(primary.inventory.sessions), "independent_source_sessions": len(independent.inventory.sessions),
            "paired_sessions": len(pairs), "unmatched_primary": len(a) - len(pairs), "unmatched_independent": len(b) - len(pairs),
            "field_state_tables": tables, "basis": "retained raw labels before adjudication; not source correctness"}


def agreement_manifest(workflows) -> dict:
    return {"schema_version": "m2.0", "pages": [agreement_summary(w) for w in sorted(workflows, key=lambda w: w.page_id) if w.comparison]}
