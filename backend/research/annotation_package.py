"""Local source-first draft preparation. Never infer labels or authenticate humans.

Uses M1/M2 contracts and transitions unchanged. All writes are create-only.
Drafts are not submissions; structural success is never Gold certification.
"""
import argparse
import json
import hashlib
from zipfile import ZipFile
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from pydantic import ValidationError
from backend.models.event import EventContent, FieldState
from backend.research.models import (ResearchBundle, Submission, PageWorkflow,
    Attestation, Assignment, Person, Comparison, Adjudication, PageInventory)
from backend.research.integrity import record_digest, source_for_page, check_sources, check_splits
from backend.research.inventory import inventory_findings
from backend.research import workflow as flow

VERSION = 'g1a-draft-v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, value):
    path = Path(path)
    def authentic(item):
        if isinstance(item, dict):
            return item.get('origin') == 'AUTHENTIC' or any(authentic(v) for v in item.values())
        return isinstance(item, (tuple, list)) and any(authentic(v) for v in item)
    if authentic(value) and path.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError('AUTHENTIC_RECORD_MUST_STAY_OUTSIDE_PROJECT')
    # Serialize before opening: an invalid object cannot leave a partial draft.
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)


def page_source(bundle, page_id):
    pages = [p for p in bundle.pages if p.page_id == page_id]
    if len(pages) != 1:
        raise ValueError('PAGE_ID_NOT_UNIQUE')
    page = pages[0]
    source = source_for_page(bundle.sources, page)
    if source is None or not source.original_sha256 or not page.page_number:
        raise ValueError('SOURCE_BINDING_INCOMPLETE')
    return page, source


def new_task(bundle, page_id, role):
    page, source = page_source(bundle, page_id)
    if page.partition != 'development':
        raise ValueError('DEVELOPMENT_ONLY')
    if role not in {'PRIMARY', 'INDEPENDENT'}:
        raise ValueError('ROLE_INVALID')
    return {'schema_version': VERSION, 'origin': bundle.origin,
        'page_id': page_id, 'source_document_id': source.source_document_id,
        'source_id': page.source_id, 'page_number': page.page_number,
        'source_sha256': source.original_sha256,
        'status': 'DRAFT_NOT_VERIFIED',
        'submission': {'submission_id': None, 'role': role,
            'inventory': {'page_id': page_id, 'source_sha256': source.original_sha256,
                'basis': 'SOURCE_ONLY', 'regions': [], 'sessions': [], 'evidence': [],
                'recorded_by': None, 'coverage_attestation': None},
            'labels': [], 'submitted_by': None, 'previous_submission_id': None,
            'correction_reason': None}}


def add_region(task, region_id, kind, locator, note):
    inv = task['submission']['inventory']
    if not region_id.strip() or any(r['region_id'] == region_id for r in inv['regions']):
        raise ValueError('REGION_ID_INVALID_OR_DUPLICATE')
    if kind not in {'UNKNOWN', 'NON_EVENT', 'UNREADABLE', 'MERGED_CELL'} or not locator.strip() or not note.strip():
        raise ValueError('REGION_KIND_LOCATOR_NOTE_REQUIRED')
    evidence_id = f"{task['page_id']}:{task['submission']['role']}:{region_id}:source"
    inv['evidence'].append({'evidence_id': evidence_id,
        'source_document_id': task['source_document_id'], 'route': 'authority',
        'raw_text': '', 'page': task['page_number'], 'locator': locator,
        'transformations': ['human-source-region-registration;not-OCR']})
    inv['regions'].append({'region_id': region_id, 'kind': kind, 'evidence_ids': [evidence_id],
        'session_ids': [], 'continuation_page_ids': [], 'note': note})
    return task


def add_event(task, event_id, region_id):
    sub = task['submission']; inv = sub['inventory']
    if not event_id.strip() or any(s['session_id'] == event_id for s in inv['sessions']):
        raise ValueError('EVENT_ID_INVALID_OR_DUPLICATE')
    matches = [r for r in inv['regions'] if r['region_id'] == region_id]
    if len(matches) != 1 or matches[0]['kind'] == 'NON_EVENT':
        raise ValueError('SESSION_REGION_REQUIRED')
    region = matches[0]
    inv['sessions'].append({'session_id': event_id, 'page_id': task['page_id'],
        'region_id': region_id, 'evidence_ids': region['evidence_ids'],
        'ambiguity_note': 'Unfilled draft; human source reading required', 'disposition': 'IN_SCOPE'})
    region['session_ids'].append(event_id)
    region['kind'] = 'SESSION' if len(region['session_ids']) == 1 else 'MULTI_SESSION'
    identity = f"{task['source_sha256']}:{task['page_id']}:{sub['role']}:{event_id}:v1"
    sub['labels'].append({'label_id': event_id, 'session_id': event_id, 'revision': {
        'revision_id': str(uuid5(NAMESPACE_URL, identity)), 'version': 1,
        'previous_revision_id': None, 'source_document_id': task['source_document_id'],
        'method_id': 'source_first_human_annotation_m2', 'contract_version': 'm1.0',
        'configuration_digest': record_digest({'template': VERSION}),
        'content': {field: None for field in EventContent.model_fields},
        'observations': [{'field_name': field, 'state': 'MISSING', 'raw_value': None,
            'origin': 'human', 'evidence_ids': [], 'alternatives': [], 'confidence': None,
            'justification': None, 'transformations': []} for field in EventContent.model_fields]}})
    return task


def submission_from_task(task, bundle):
    if task.get('schema_version') != VERSION or task.get('status') != 'DRAFT_NOT_VERIFIED':
        raise ValueError('DRAFT_VERSION_OR_STATUS_INVALID')
    page, source = page_source(bundle, task['page_id'])
    if page.partition != 'development' or task.get('origin') != bundle.origin:
        raise ValueError('DEVELOPMENT_OR_ORIGIN_MISMATCH')
    for key, expected in [('source_id', page.source_id), ('page_number', page.page_number),
                          ('source_document_id', source.source_document_id),
                          ('source_sha256', source.original_sha256)]:
        if task.get(key) != expected:
            raise ValueError('TASK_SOURCE_BINDING_MISMATCH')
    sub = Submission.model_validate(task['submission'])
    inv = sub.inventory
    if inv.page_id != page.page_id or inv.source_sha256 != source.original_sha256:
        raise ValueError('INVENTORY_SOURCE_BINDING_MISMATCH')
    if sub.submitted_by.origin != bundle.origin:
        raise ValueError('ORIGIN_MISMATCH')
    if inv.coverage_attestation and (inv.coverage_attestation.origin != bundle.origin or
            inv.coverage_attestation.actor_id != inv.recorded_by.actor_id or
            inv.coverage_attestation.recorded_at < inv.recorded_by.recorded_at or
            inv.coverage_attestation.recorded_at > sub.submitted_by.recorded_at):
        raise ValueError('COVERAGE_ATTESTATION_MISMATCH')
    if not inv.regions:
        raise ValueError('PAGE_ACCOUNTING_REQUIRED_EVEN_IF_EMPTY')
    if any(e.page != page.page_number or e.source_document_id != source.source_document_id
           for e in inv.evidence):
        raise ValueError('SOURCE_EVIDENCE_PAGE_MISMATCH')
    if any(label.revision.source_document_id != source.source_document_id for label in sub.labels):
        raise ValueError('LABEL_SOURCE_MISMATCH')
    if any(obs.origin != 'human' for label in sub.labels for obs in label.revision.observations):
        raise ValueError('HUMAN_SOURCE_READING_REQUIRED')
    return sub


def submission_findings(sub):
    findings = [f.code for f in inventory_findings(sub.inventory)]
    for label in sub.labels:
        rev = label.revision
        if not rev.core_fields_present:
            findings.append('CRITICAL_FIELDS_UNRESOLVED')
        if flow.has_unresolved_placeholder(rev):
            findings.append('PLACEHOLDER_CANNOT_BE_PRESENT')
        if any(o.state in {FieldState.MISSING, FieldState.UNRESOLVED, FieldState.CONFLICTING}
               for o in rev.observations):
            findings.append('UNRESOLVED_FIELDS_RETAINED')
        if any(o.state in {FieldState.UNRESOLVED, FieldState.CONFLICTING} and
               not o.justification for o in rev.observations):
            findings.append('AMBIGUITY_NOTE_REQUIRED')
        c = rev.content
        if c.start_time and c.end_time and c.end_time <= c.start_time and c.end_day_offset != 1:
            findings.append('TIME_ORDER_REQUIRES_SOURCE_REVIEW')
        if c.date and c.day_of_week and c.date.isoweekday() != c.day_of_week:
            findings.append('DATE_DAY_CONFLICT_REQUIRES_SOURCE_REVIEW')
    return sorted(set(findings))


def apply_action(current, request):
    """Explicit caller-supplied human actions; never invent an attestation."""
    if not flow.validate_history(current):
        raise ValueError('WORKFLOW_HISTORY_INVALID')
    action = request['action']; p = request['payload']
    if action == 'assign':
        return flow.assign_roles(current, tuple(Person.model_validate(x) for x in p['people']),
            tuple(Assignment.model_validate(x) for x in p['assignments']), Attestation.model_validate(p['recorded_by']))
    if action == 'submit':
        return flow.submit(current, Submission.model_validate(p))
    if action == 'open-blind':
        return flow.open_blind_task(current, p['source_artifact_id'], Attestation.model_validate(p['recorded_by']))[0]
    if action == 'compare':
        return flow.compare(current, Comparison.model_validate(p))
    if action == 'adjudicate':
        return flow.adjudicate(current, Adjudication.model_validate(p))
    if action in {'verify-primary','verify-independent'}:
        return flow.verify_reference(current, PageInventory.model_validate(p['inventory']),
            tuple(tuple(x) for x in p['bindings']), Attestation.model_validate(p['recorded_by']),
            field_registry_artifact_id=p['field_registry_artifact_id'],
            conditional_fields=tuple(p['conditional_fields']), independently_audited=action=='verify-independent')
    raise ValueError('UNSUPPORTED_ACTION')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init-task', 'check-task', 'build-submission'):
        p = commands.add_parser(name); p.add_argument('--bundle', type=Path, required=True)
        if name == 'init-task':
            p.add_argument('--page', required=True); p.add_argument('--role', choices=['PRIMARY','INDEPENDENT'], required=True)
        else: p.add_argument('--task', type=Path, required=True)
        if name != 'check-task': p.add_argument('--out', type=Path, required=True)
    for name in ('add-region', 'add-event'):
        p = commands.add_parser(name); p.add_argument('--task', type=Path, required=True)
        p.add_argument('--region', required=True); p.add_argument('--out', type=Path, required=True)
        if name == 'add-region':
            p.add_argument('--kind', choices=['UNKNOWN','NON_EVENT','UNREADABLE','MERGED_CELL'], default='UNKNOWN')
            p.add_argument('--locator', required=True); p.add_argument('--note', required=True)
        else: p.add_argument('--event', required=True)
    p = commands.add_parser('check-inventory'); p.add_argument('--bundle', type=Path, required=True)
    p = commands.add_parser('check-source'); p.add_argument('--path', type=Path, required=True)
    p.add_argument('--sha256', required=True); p.add_argument('--member')
    p = commands.add_parser('action'); p.add_argument('--workflow', type=Path, required=True)
    p.add_argument('--request', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    p = commands.add_parser('check-workflow'); p.add_argument('--workflow', type=Path, required=True)
    p = commands.add_parser('export'); p.add_argument('--workflow', type=Path, nargs='+', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check-source':
            if args.member:
                with ZipFile(args.path) as archive, archive.open(args.member) as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            else:
                with args.path.open('rb') as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            matches = actual == args.sha256
            print(json.dumps({'hash_matches': matches, 'source_correctness_verified': False}))
            return 0 if matches else 1
        bundle = ResearchBundle.model_validate(read(args.bundle)) if hasattr(args, 'bundle') else None
        if args.command == 'init-task':
            result = new_task(bundle, args.page, args.role)
        elif args.command in {'add-region','add-event'}:
            task = read(args.task)
            result = (add_region(task, args.region, args.kind, args.locator, args.note)
                      if args.command == 'add-region' else add_event(task, args.event, args.region))
        elif args.command in {'check-task', 'build-submission'}:
            sub = submission_from_task(read(args.task), bundle)
            findings = submission_findings(sub)
            if args.command == 'check-task':
                print(json.dumps({'status': 'STRUCTURALLY_VALID_NOT_VERIFIED', 'findings': findings,
                                  'sessions': len(sub.labels), 'source_correctness_verified': False}))
                return 1 if findings else 0
            # Preserve incomplete labels as submissions; verification remains separate.
            result = sub.model_dump(mode='json')
        elif args.command == 'check-inventory':
            findings = check_sources(bundle.sources, bundle.pages) + check_splits(bundle.pages, registered_allocation=False)
            print(json.dumps({'status': 'PREPARATION_ONLY_NOT_READY', 'sources': len(bundle.sources),
                'pages': len(bundle.pages), 'findings': sorted({f.code for f in findings}),
                'source_correctness_verified': False}))
            return 1 if findings else 0
        elif args.command in {'action', 'check-workflow'}:
            current = PageWorkflow.model_validate(read(args.workflow))
            if args.command == 'check-workflow':
                valid = flow.validate_history(current)
                print(json.dumps({'history_valid': valid, 'state': current.state.value,
                                  'human_identity_authenticated': False}))
                return 0 if valid else 1
            result = apply_action(current, read(args.request)).model_dump(mode='json')
        else:
            workflows = tuple(PageWorkflow.model_validate(read(p)) for p in args.workflow)
            if len({w.page_id for w in workflows}) != len(workflows):
                raise ValueError('DUPLICATE_EXPORT_PAGE')
            if any(w.state not in {flow.WorkflowState.MANUALLY_VERIFIED_REFERENCE,flow.WorkflowState.INDEPENDENTLY_VERIFIED} for w in workflows):
                raise ValueError('INDEPENDENT_VERIFICATION_REQUIRED')
            result = flow.export_gold_manifest(workflows)
        write_new(args.out, result)
        print(json.dumps({'status': 'SAVED_NEW_FILE', 'human_identity_authenticated': False}))
        return 0
    except ValidationError as error:
        # Locations/types only: never echo private field values or source text.
        print(json.dumps({'status': 'INVALID_OR_PENDING', 'errors': [
            {'location': list(e['loc']), 'type': e['type']} for e in error.errors()]}))
        return 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        safe = str(error) if isinstance(error, ValueError) and str(error).replace('_','').isalnum() else type(error).__name__
        print(json.dumps({'status': 'INVALID_OR_PENDING', 'reason': safe}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
