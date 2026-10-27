"""Append-only service controls. Caller authentication is a separate prerequisite.

No baseline verification flags are imported. Verification requires an explicit
human action and an evidence-linked, complete source inventory. No API routes
are added; deploy behind authenticated access before exposing these services.
"""

import json

from backend.models.event import EventContent, FieldState
from backend.research.integrity import record_digest
from backend.research.inventory import inventory_findings
from backend.research.models import (
    Adjudication, Assignment, Attestation, Comparison, Disagreement, HistoryEntry,
    PageInventory, PageWorkflow, Person, Submission, WorkflowState,
)


class WorkflowError(ValueError):
    """Message is an explicit reason code, never private source text."""


REGISTERED_CONDITIONAL_FIELDS = {"session_type", "location", "lecturer", "recurrence", "section", "teaching_weeks", "excluded_weeks"}


def has_unresolved_placeholder(revision):
    return any(isinstance(getattr(revision.content, name), str)
               and getattr(revision.content, name).strip().upper() in {"TBA", "TBD", "UNKNOWN", "UNREADABLE"}
               for name in ("course_code", "course_name", "location", "lecturer", "section"))


def _latest(workflow, role):
    return next((s for s in reversed(workflow.submissions) if s.role == role), None)


def _actor(workflow, role):
    return next((a.actor_id for a in workflow.assignments if a.role == role), None)


def _record(workflow, action, attestation, state, **updates):
    if not validate_history(workflow):
        raise WorkflowError("WORKFLOW_HISTORY_INVALID")
    if attestation.origin != workflow.origin:
        raise WorkflowError("ORIGIN_MISMATCH")
    if workflow.history and attestation.recorded_at < workflow.history[-1].recorded_by.recorded_at:
        raise WorkflowError("NON_MONOTONIC_WORKFLOW_TIMESTAMP")
    payload = workflow.model_dump(mode="json", exclude={"history"})
    payload.update(updates, state=state)
    # Re-validate every snapshot; model_copy(update=...) does not validate values.
    snapshot = PageWorkflow.model_validate(payload)
    payload_json = json.dumps(snapshot.model_dump(mode="json", exclude={"history"}), sort_keys=True,
                              ensure_ascii=False, separators=(",", ":"))
    body = dict(sequence=len(workflow.history) + 1, action=action, from_state=workflow.state,
                to_state=snapshot.state, recorded_by=attestation, payload_digest=record_digest(json.loads(payload_json)),
                payload_json=payload_json, predecessor_digest=workflow.history[-1].entry_digest if workflow.history else None)
    digest_body = {**body, "recorded_by": attestation.model_dump(mode="json")}
    entry = HistoryEntry(**body, entry_digest=record_digest(digest_body))
    return PageWorkflow.model_validate({**snapshot.model_dump(), "history": (*workflow.history, entry)})


def assign_roles(workflow: PageWorkflow, people: tuple[Person, ...], assignments: tuple[Assignment, ...],
                 recorded_by: Attestation) -> PageWorkflow:
    if workflow.submissions or workflow.verification:
        raise WorkflowError("ASSIGNMENT_LOCKED_AFTER_SUBMISSION")
    ids = [p.actor_id for p in people]
    roles = [a.role for a in assignments]
    actors = [a.actor_id for a in assignments]
    if len(set(ids)) != len(ids) or len(set(roles)) != len(roles) or len(set(actors)) != len(actors):
        raise WorkflowError("REVIEWER_INDEPENDENCE_VIOLATION")
    if "PRIMARY" not in roles or set(actors) - set(ids):
        raise WorkflowError("APPROVED_ASSIGNMENT_REQUIRED")
    for assignment in assignments:
        person = next(p for p in people if p.actor_id == assignment.actor_id)
        if person.origin != workflow.origin or assignment.approved_by.origin != workflow.origin:
            raise WorkflowError("ORIGIN_MISMATCH")
        if assignment.role == "INDEPENDENT" and person.contributed_to_implementation is not False:
            raise WorkflowError("REVIEWER_IMPLEMENTATION_INDEPENDENCE_UNPROVEN")
        if assignment.approved_by.recorded_at > recorded_by.recorded_at:
            raise WorkflowError("ASSIGNMENT_APPROVAL_AFTER_RECORDING")
    return _record(workflow, "ASSIGN_ROLES", recorded_by, WorkflowState.ASSIGNED,
                   people=people, assignments=assignments)


def open_blind_task(workflow: PageWorkflow, source_artifact_id: str, recorded_by: Attestation):
    if recorded_by.actor_id != _actor(workflow, "INDEPENDENT"):
        raise WorkflowError("INDEPENDENT_ASSIGNMENT_REQUIRED")
    if _latest(workflow, "INDEPENDENT") is not None:
        raise WorkflowError("BLIND_SUBMISSION_ALREADY_RECORDED")
    updated = _record(workflow, "VIEW_SOURCE", recorded_by, workflow.state)
    # Do not expose primary inventory, counts, session IDs, notes, labels or predictions.
    return updated, {"page_id": workflow.page_id, "source_sha256": workflow.source_sha256,
                     "source_artifact_id": source_artifact_id, "mode": "BLIND_SOURCE_ONLY"}


def restricted_view(workflow: PageWorkflow, recorded_by: Attestation, kind: str):
    if kind == "PREDICTIONS":
        raise WorkflowError("PREDICTIONS_PROHIBITED_IN_GOLD_WORKFLOW")
    if kind != "PRIMARY_LABELS":
        raise WorkflowError("UNKNOWN_VIEW_KIND")
    if recorded_by.actor_id == _actor(workflow, "INDEPENDENT") and _latest(workflow, "INDEPENDENT") is None:
        raise WorkflowError("BLINDNESS_VIOLATION")
    if recorded_by.actor_id not in {a.actor_id for a in workflow.assignments}:
        raise WorkflowError("ASSIGNED_ACTOR_REQUIRED")
    return _record(workflow, "VIEW_LABELS", recorded_by, workflow.state), _latest(workflow, "PRIMARY")


def submit(workflow: PageWorkflow, submission: Submission) -> PageWorkflow:
    if workflow.comparison or workflow.verification:
        raise WorkflowError("LABELS_LOCKED_USE_NEW_VERSION")
    if submission.submitted_by.actor_id != _actor(workflow, submission.role):
        raise WorkflowError("SUBMISSION_ACTOR_NOT_ASSIGNED")
    if submission.inventory.page_id != workflow.page_id or submission.inventory.source_sha256 != workflow.source_sha256:
        raise WorkflowError("SUBMISSION_SOURCE_MISMATCH")
    if submission.inventory.recorded_by.origin != workflow.origin:
        raise WorkflowError("ORIGIN_MISMATCH")
    if submission.submission_id in {s.submission_id for s in workflow.submissions}:
        raise WorkflowError("DUPLICATE_SUBMISSION_ID")
    previous = _latest(workflow, submission.role)
    if previous is None and submission.previous_submission_id is not None or previous is not None and submission.previous_submission_id != previous.submission_id:
        raise WorkflowError("CORRECTION_PREDECESSOR_MISMATCH")
    if previous:
        old = {l.session_id: l.revision for l in previous.labels}
        for label in submission.labels:
            before = old.get(label.session_id)
            after = label.revision
            if before and after != before and (after.previous_revision_id != before.revision_id or after.version != before.version + 1):
                raise WorkflowError("CORRECTION_EVENT_REVISION_CHAIN_REQUIRED")
    submissions = (*workflow.submissions, submission)
    roles = {s.role for s in submissions}
    state = WorkflowState.LABELS_SUBMITTED if len(roles) == 2 else WorkflowState.PRIMARY_SUBMITTED if "PRIMARY" in roles else WorkflowState.INDEPENDENT_SUBMITTED
    return _record(workflow, "SUBMIT_" + submission.role, submission.submitted_by, state, submissions=submissions)


def compare(workflow: PageWorkflow, comparison: Comparison) -> PageWorkflow:
    if workflow.state != WorkflowState.LABELS_SUBMITTED:
        raise WorkflowError("BOTH_BLIND_LABELS_REQUIRED")
    if comparison.compared_by.actor_id not in {_actor(workflow, "INDEPENDENT"), _actor(workflow, "ADJUDICATOR")}:
        raise WorkflowError("INDEPENDENT_COMPARISON_ACTOR_REQUIRED")
    primary, independent = _latest(workflow, "PRIMARY"), _latest(workflow, "INDEPENDENT")
    if (comparison.primary_submission_id, comparison.independent_submission_id) != (primary.submission_id, independent.submission_id):
        raise WorkflowError("COMPARISON_SUBMISSION_MISMATCH")
    p_labels, i_labels = {l.label_id: l for l in primary.labels}, {l.label_id: l for l in independent.labels}
    left = [p for p, _ in comparison.label_pairs]
    right = [i for _, i in comparison.label_pairs]
    if len(set(left)) != len(left) or len(set(right)) != len(right) or set(left) - p_labels.keys() or set(right) - i_labels.keys():
        raise WorkflowError("COMPARISON_NOT_ONE_TO_ONE")
    disagreements = []
    for p, i in comparison.label_pairs:
        a, b = p_labels[p].revision, i_labels[i].revision
        a_states, b_states = {o.field_name: o.state for o in a.observations}, {o.field_name: o.state for o in b.observations}
        fields = tuple(name for name in EventContent.model_fields
                       if getattr(a.content, name) != getattr(b.content, name) or a_states[name] != b_states[name])
        if fields:
            disagreements.append(Disagreement(disagreement_id=f"disagreement:{len(disagreements)}", primary_label_id=p, independent_label_id=i, fields=fields))
    for p in sorted(p_labels.keys() - set(left)):
        disagreements.append(Disagreement(disagreement_id=f"disagreement:{len(disagreements)}", primary_label_id=p, fields=("MISSING_INDEPENDENT_SESSION",)))
    for i in sorted(i_labels.keys() - set(right)):
        disagreements.append(Disagreement(disagreement_id=f"disagreement:{len(disagreements)}", independent_label_id=i, fields=("MISSING_PRIMARY_SESSION",)))
    return _record(workflow, "COMPARE_LABELS", comparison.compared_by,
                   WorkflowState.DISPUTED if disagreements else WorkflowState.RECONCILED,
                   comparison=comparison, disagreements=tuple(disagreements))


def adjudicate(workflow: PageWorkflow, adjudication: Adjudication) -> PageWorkflow:
    if workflow.state != WorkflowState.DISPUTED:
        raise WorkflowError("DISPUTED_WORKFLOW_REQUIRED")
    actor = adjudication.recorded_by.actor_id
    if actor != _actor(workflow, "ADJUDICATOR") or actor in {_actor(workflow, "PRIMARY"), _actor(workflow, "INDEPENDENT")}:
        raise WorkflowError("NON_ORIGINATING_ADJUDICATOR_REQUIRED")
    disagreement = next((d for d in workflow.disagreements if d.disagreement_id == adjudication.disagreement_id), None)
    if disagreement is None or adjudication.disagreement_id in {a.disagreement_id for a in workflow.adjudications}:
        raise WorkflowError("UNKNOWN_OR_ALREADY_ADJUDICATED_DISAGREEMENT")
    if adjudication.decision == "USE_PRIMARY" and disagreement.primary_label_id is None or adjudication.decision == "USE_INDEPENDENT" and disagreement.independent_label_id is None:
        raise WorkflowError("ADJUDICATION_LABEL_MISSING")
    if adjudication.decision == "CORRECTED" and adjudication.corrected_label is None:
        raise WorkflowError("CORRECTION_LABEL_REQUIRED")
    if adjudication.decision == "CORRECTED":
        original_revisions = [l.revision for s in workflow.submissions for l in s.labels
                              if l.label_id in {disagreement.primary_label_id, disagreement.independent_label_id}]
        corrected = adjudication.corrected_label.revision
        if not any(corrected.previous_revision_id == before.revision_id and corrected.version == before.version + 1
                   and corrected.source_document_id == before.source_document_id for before in original_revisions):
            raise WorkflowError("ADJUDICATION_REVISION_CHAIN_REQUIRED")
    if adjudication.decision != "CORRECTED" and adjudication.corrected_label is not None:
        raise WorkflowError("UNEXPECTED_CORRECTION_LABEL")
    if adjudication.decision == "EXCLUDED_AMBIGUOUS" and not adjudication.exclusion_region_ids:
        raise WorkflowError("EXCLUSION_SCOPE_REQUIRED")
    if adjudication.decision == "EXCLUDED_AMBIGUOUS" and set(adjudication.exclusion_region_ids) - {r.region_id for s in workflow.submissions for r in s.inventory.regions}:
        raise WorkflowError("EXCLUSION_SOURCE_REGION_UNKNOWN")
    records = (*workflow.adjudications, adjudication)
    return _record(workflow, "ADJUDICATE", adjudication.recorded_by,
                   WorkflowState.RECONCILED if len(records) == len(workflow.disagreements) else WorkflowState.DISPUTED,
                   adjudications=records)


def resolved_labels(workflow):
    primary = _latest(workflow, "PRIMARY")
    labels = {l.label_id: l for l in primary.labels} if primary else {}
    independent = _latest(workflow, "INDEPENDENT")
    independent_labels = {l.label_id: l for l in independent.labels} if independent else {}
    for adjudication in workflow.adjudications:
        disagreement = next(d for d in workflow.disagreements if d.disagreement_id == adjudication.disagreement_id)
        if adjudication.decision != "USE_PRIMARY" and disagreement.primary_label_id:
            labels.pop(disagreement.primary_label_id, None)
        if adjudication.decision == "USE_INDEPENDENT":
            selected = independent_labels[disagreement.independent_label_id]
            labels[selected.label_id] = selected
        if adjudication.decision == "CORRECTED":
            selected = adjudication.corrected_label
            if selected.label_id in labels:
                raise WorkflowError("CORRECTION_LABEL_ID_COLLISION")
            labels[selected.label_id] = selected
    return tuple(labels[key] for key in sorted(labels))


def verify_reference(workflow: PageWorkflow, inventory: PageInventory, bindings: tuple[tuple[str, str], ...],
                     recorded_by: Attestation, *, field_registry_artifact_id: str,
                     conditional_fields: tuple[str, ...], independently_audited: bool) -> PageWorkflow:
    """Explicit human verification, never called by submission/comparison/export."""
    required_state = WorkflowState.RECONCILED if independently_audited else WorkflowState.PRIMARY_SUBMITTED
    if workflow.source_id is None:
        raise WorkflowError("SOURCE_PAGE_IDENTITY_REQUIRED")
    if workflow.state != required_state:
        raise WorkflowError("VERIFICATION_TRANSITION_INVALID")
    role = "INDEPENDENT" if independently_audited else "PRIMARY"
    permitted = {_actor(workflow, role)} | ({_actor(workflow, "ADJUDICATOR")} if independently_audited else set())
    if recorded_by.actor_id not in permitted or independently_audited and recorded_by.actor_id == _actor(workflow, "PRIMARY"):
        raise WorkflowError("VERIFIER_INDEPENDENCE_VIOLATION")
    if not field_registry_artifact_id or field_registry_artifact_id not in recorded_by.evidence_ids:
        raise WorkflowError("FROZEN_FIELD_REGISTRY_REQUIRED")
    if set(conditional_fields) - EventContent.model_fields.keys():
        raise WorkflowError("UNKNOWN_REGISTERED_FIELD")
    if not REGISTERED_CONDITIONAL_FIELDS.issubset(conditional_fields):
        raise WorkflowError("REGISTERED_CONDITIONAL_FIELDS_OMITTED")
    if inventory.page_id != workflow.page_id or inventory.source_sha256 != workflow.source_sha256 or inventory.recorded_by.origin != workflow.origin:
        raise WorkflowError("FINAL_INVENTORY_SOURCE_MISMATCH")
    if inventory_findings(inventory):
        raise WorkflowError("FINAL_INVENTORY_INCOMPLETE")
    labels = resolved_labels(workflow)
    left, right = [a for a, _ in bindings], [b for _, b in bindings]
    excluded_regions = {r for a in workflow.adjudications if a.decision == "EXCLUDED_AMBIGUOUS" for r in a.exclusion_region_ids}
    if any(s.disposition == "EXCLUDED_AMBIGUOUS" and s.region_id not in excluded_regions for s in inventory.sessions):
        raise WorkflowError("SOURCE_SESSION_EXCLUSION_UNADJUDICATED")
    if (set(left) != {l.label_id for l in labels} or set(right) != {s.session_id for s in inventory.sessions if s.disposition == "IN_SCOPE"}
            or len(set(left)) != len(left) or len(set(right)) != len(right)):
        raise WorkflowError("GOLD_SOURCE_SESSION_BIJECTION_REQUIRED")
    known = {e.evidence_id for e in inventory.evidence}
    for label in labels:
        revision = label.revision
        if not revision.core_fields_present:
            raise WorkflowError("GOLD_REQUIRED_FIELD_MISSING")
        if has_unresolved_placeholder(revision):
            raise WorkflowError("GOLD_SOURCE_PLACEHOLDER_UNRESOLVED")
        states = {o.field_name: o.state for o in revision.observations}
        if any(states[name] not in {FieldState.PRESENT, FieldState.SOURCE_ABSENT, FieldState.NOT_APPLICABLE} for name in conditional_fields):
            raise WorkflowError("GOLD_CONDITIONAL_FIELD_UNRESOLVED")
        if any(set(o.evidence_ids) - known for o in revision.observations):
            raise WorkflowError("GOLD_FIELD_EVIDENCE_UNKNOWN")
        if revision.source_document_id not in {e.source_document_id for e in inventory.evidence}:
            raise WorkflowError("GOLD_SOURCE_DOCUMENT_MISMATCH")
    return _record(workflow, "VERIFY_INDEPENDENT" if independently_audited else "VERIFY_PRIMARY", recorded_by,
                   WorkflowState.INDEPENDENTLY_VERIFIED if independently_audited else WorkflowState.MANUALLY_VERIFIED_REFERENCE,
                   final_inventory=inventory, label_session_bindings=bindings, verification=recorded_by)


def validate_history(workflow: PageWorkflow) -> bool:
    """Tamper/chain check, not authentication of a person or signature."""
    previous_digest, state, timestamp = None, WorkflowState.UNASSIGNED, None
    for index, entry in enumerate(workflow.history, 1):
        body = entry.model_dump(mode="json", exclude={"entry_digest"})
        try:
            payload = json.loads(entry.payload_json)
        except ValueError:
            return False
        allowed = {
            "ASSIGN_ROLES": ({WorkflowState.UNASSIGNED, WorkflowState.ASSIGNED}, {WorkflowState.ASSIGNED}),
            "SUBMIT_PRIMARY": ({WorkflowState.ASSIGNED, WorkflowState.PRIMARY_SUBMITTED, WorkflowState.INDEPENDENT_SUBMITTED, WorkflowState.LABELS_SUBMITTED},
                               {WorkflowState.PRIMARY_SUBMITTED, WorkflowState.LABELS_SUBMITTED}),
            "SUBMIT_INDEPENDENT": ({WorkflowState.ASSIGNED, WorkflowState.PRIMARY_SUBMITTED, WorkflowState.INDEPENDENT_SUBMITTED, WorkflowState.LABELS_SUBMITTED},
                                   {WorkflowState.INDEPENDENT_SUBMITTED, WorkflowState.LABELS_SUBMITTED}),
            "COMPARE_LABELS": ({WorkflowState.LABELS_SUBMITTED}, {WorkflowState.RECONCILED, WorkflowState.DISPUTED}),
            "ADJUDICATE": ({WorkflowState.DISPUTED}, {WorkflowState.DISPUTED, WorkflowState.RECONCILED}),
            "VERIFY_PRIMARY": ({WorkflowState.PRIMARY_SUBMITTED}, {WorkflowState.MANUALLY_VERIFIED_REFERENCE}),
            "VERIFY_INDEPENDENT": ({WorkflowState.RECONCILED}, {WorkflowState.INDEPENDENTLY_VERIFIED}),
        }
        if entry.action in {"VIEW_SOURCE", "VIEW_LABELS"}:
            valid_transition = entry.from_state == entry.to_state
        else:
            before, after = allowed.get(entry.action, (set(), set()))
            valid_transition = entry.from_state in before and entry.to_state in after
        if (entry.sequence != index or entry.predecessor_digest != previous_digest or entry.from_state != state
                or record_digest(body) != entry.entry_digest or record_digest(payload) != entry.payload_digest
                or not valid_transition or payload.get("state") != entry.to_state.value
                or timestamp is not None and entry.recorded_by.recorded_at < timestamp):
            return False
        previous_digest, state, timestamp = entry.entry_digest, entry.to_state, entry.recorded_by.recorded_at
    if not workflow.history:
        return workflow.state == WorkflowState.UNASSIGNED and not workflow.submissions and not workflow.assignments
    return state == workflow.state and workflow.history[-1].payload_digest == record_digest(workflow.model_dump(mode="json", exclude={"history"}))


def export_gold_manifest(workflows: tuple[PageWorkflow, ...]) -> dict:
    """Export only explicitly verified records. Does not freeze, promote or write."""
    if not workflows:
        raise WorkflowError("GOLD_WORKFLOWS_MISSING")
    output, exclusions = [], []
    for workflow in workflows:
        if workflow.state not in {WorkflowState.MANUALLY_VERIFIED_REFERENCE, WorkflowState.INDEPENDENTLY_VERIFIED} or not workflow.verification or not validate_history(workflow):
            raise WorkflowError("GOLD_WORKFLOW_NOT_VERIFIED")
        for label in resolved_labels(workflow):
            output.append({"reference_event_id": f"{workflow.page_id}:{label.label_id}",
                           "page_id": workflow.page_id, "source_id": workflow.source_id,
                           "source_session_id": dict(workflow.label_session_bindings)[label.label_id],
                           "source_document_id": label.revision.source_document_id,
                           "label": label.model_dump(mode="json"),
                           "source_sha256": workflow.source_sha256, "status": workflow.state.value,
                           "verification": workflow.verification.model_dump(mode="json"),
                           "workflow_digest": record_digest(workflow), "origin": workflow.origin})
        exclusions.extend({"page_id": workflow.page_id, **a.model_dump(mode="json")} for a in workflow.adjudications if a.decision == "EXCLUDED_AMBIGUOUS")
    return {"schema_version": "m2.0", "verified_event_count": len(output), "events": output, "exclusions": exclusions}
