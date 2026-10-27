"""SYNTHETIC M2 TEST DATA ONLY. Actors and attestations are fictional test records.

No item in this module is real research evidence or an approved reviewer action.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from backend.models.evidence import EvidenceSpan
from backend.models.event import EventRevision
from backend.research.models import (
    Assignment, Attestation, Comparison, PageInventory, PageWorkflow, Person,
    SessionLabel, SourcePage, SourceRegion, SourceSession, Submission,
)
from backend.research.workflow import REGISTERED_CONDITIONAL_FIELDS, assign_roles, compare, submit, verify_reference
from backend.services.event_adapter import adapt_tarumt_response
from backend.services.timetable_parser import parse_intranet_response
from tests.fixtures import timetable_html

HASH = "a" * 64
CONDITIONAL_FIELDS = tuple(sorted(REGISTERED_CONDITIONAL_FIELDS))


def synthetic_directory():
    """Allocate only inside the workspace; cleanup only this new test directory."""
    workspace = Path(__file__).resolve().parents[1]
    parent = workspace / "data" / "temp" / "m2-synthetic-tests"
    assert parent.resolve().is_relative_to(workspace)
    parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="synthetic-", dir=parent)
    assert Path(temporary.name).resolve().is_relative_to(parent.resolve())
    return temporary


def stamp(actor="synthetic-supervisor", minute=0):
    return Attestation(actor_id=actor, recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute),
                       evidence_ids=("synthetic-proof", "synthetic-registry"), origin="SYNTHETIC")


def page(page_id="page_001", partition="development", group="group1", **kwargs):
    return SourcePage(page_id=page_id, source_id="synthetic-source", page_number=int(page_id[-3:]), partition=partition,
                      dependency_group=group, timetable_bearing=True, programme_family="synthetic-programme",
                      complexity="simple", high_risk_categories=(), **kwargs)


def inventory(actor="synthetic-primary", minute=1, count=1):
    evidence = EvidenceSpan(evidence_id="synthetic-span", source_document_id="synthetic-document", route="text",
                            raw_text="SYNTHETIC SOURCE TEXT ONLY", page=1, locator="synthetic source cell")
    session_ids = tuple(f"{actor}-session-{i}" for i in range(count))
    sessions = tuple(SourceSession(session_id=s, page_id="page_001", region_id="synthetic-region", evidence_ids=(evidence.evidence_id,)) for s in session_ids)
    regions = (SourceRegion(region_id="synthetic-region", kind="MULTI_SESSION" if count > 1 else "SESSION",
                            evidence_ids=(evidence.evidence_id,), session_ids=session_ids),)
    return PageInventory(page_id="page_001", source_sha256=HASH, regions=regions, sessions=sessions,
                         evidence=(evidence,), recorded_by=stamp(actor, minute), coverage_attestation=stamp(actor, minute))


def revision(**content_changes):
    event = adapt_tarumt_response(parse_intranet_response(timetable_html())).events[0]
    data = event.revision.model_dump(mode="json")
    data["source_document_id"] = "synthetic-document"
    data["method_id"] = "source_first_human_annotation_m2"
    data["content"].update(content_changes)
    for o in data["observations"]:
        o["evidence_ids"] = ["synthetic-span"] if o["state"] == "PRESENT" else []
        if o["field_name"] in content_changes:
            o.update(state="PRESENT", raw_value=str(content_changes[o["field_name"]]), evidence_ids=["synthetic-span"])
        if o["field_name"] in CONDITIONAL_FIELDS and o["state"] == "MISSING":
            o.update(state="SOURCE_ABSENT", justification="Synthetic source explicitly omits this field", evidence_ids=["synthetic-span"])
    return EventRevision.model_validate(data)


def submission(role="PRIMARY", minute=1, previous=None, **changes):
    actor = "synthetic-primary" if role == "PRIMARY" else "synthetic-independent"
    inv = inventory(actor, minute)
    label_id = f"{role.lower()}-label-{minute}"
    return Submission(submission_id=f"{role.lower()}-{minute}", role=role, inventory=inv,
                      labels=(SessionLabel(label_id=label_id, session_id=inv.sessions[0].session_id, revision=revision(**changes)),),
                      submitted_by=stamp(actor, minute), previous_submission_id=previous,
                      correction_reason="Synthetic evidence-linked correction" if previous else None)


def assigned():
    workflow = PageWorkflow(page_id="page_001", source_id="synthetic-page-source", source_sha256=HASH, origin="SYNTHETIC")
    people = tuple(Person(actor_id=actor, identity_evidence_ids=("synthetic-proof",), origin="SYNTHETIC", contributed_to_implementation=role == "PRIMARY")
                   for role, actor in (("PRIMARY", "synthetic-primary"), ("INDEPENDENT", "synthetic-independent"), ("ADJUDICATOR", "synthetic-adjudicator")))
    assignments = tuple(Assignment(role=role, actor_id=p.actor_id, approved_by=stamp()) for role, p in zip(("PRIMARY", "INDEPENDENT", "ADJUDICATOR"), people))
    return assign_roles(workflow, people, assignments, stamp())


def compared(**independent_changes):
    workflow = submit(assigned(), submission())
    workflow = submit(workflow, submission("INDEPENDENT", 3, **independent_changes))
    primary, independent = workflow.submissions
    return compare(workflow, Comparison(primary_submission_id=primary.submission_id, independent_submission_id=independent.submission_id,
                    label_pairs=((primary.labels[0].label_id, independent.labels[0].label_id),), compared_by=stamp("synthetic-independent", 4)))


def verified():
    workflow = compared()
    inv = workflow.submissions[0].inventory
    return verify_reference(workflow, inv, ((workflow.submissions[0].labels[0].label_id, inv.sessions[0].session_id),),
                            stamp("synthetic-independent", 6), field_registry_artifact_id="synthetic-registry",
                            conditional_fields=CONDITIONAL_FIELDS, independently_audited=True)
