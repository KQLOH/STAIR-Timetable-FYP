"""Development-only JSON comparison; no Gold promotion or held-out evaluation."""
from pydantic import Field
from backend.models.evidence import ContractModel
from backend.models.event import EventContent
from backend.review.models import ReviewError
from backend.student.workflow import current_tasks
from backend.student.registered_calendar import authentic_registry_available


class ReferenceRow(ContractModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    content: EventContent


class Reference(ContractModel):
    format: str = Field(pattern='^stair-personal-reference-v1$')
    source_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    checked_by: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)
    events: tuple[ReferenceRow, ...] = Field(max_length=500)


def export_source(accounts, session, task_id):
    store = accounts.store(session)
    snapshot = store.snapshot(task_id, session.principal)
    original, current = [], []
    for task in snapshot['tasks']:
        if task['current_event']:
            history = store.history(task['task_id'], session.principal)
            original.append(history['revisions'][0])
            current.append(task['current_event'])
    source = snapshot['packet']['source']
    with accounts.transaction() as state:
        saved = state['workspaces'][session.user_id]['checks']
        schedules = {t['task_id']: saved[t['task_id']] for t in snapshot['tasks'] if t['task_id'] in saved}
    return {'format': 'stair-workspace-export-v1', 'source': source,
            'original_predictions': original, 'current_revisions': current,
            'current_source_evidence': snapshot['packet']['evidence'],
            'personal_schedules_not_machine_predictions': schedules,
            'routing': [t['decision'] for t in snapshot['tasks']],
            'reference_template': {'format': 'stair-personal-reference-v1',
                'source_sha256': source['content_sha256'], 'checked_by': '', 'note': '', 'events': []},
            'research_status': 'APPLICATION_EXPORT_NOT_INDEPENDENT_GOLD',
            'complete_schedule_verified': False,
            'calendar_policy': accounts.calendar_policy,
            'automatic_accept_enabled': authentic_registry_available(accounts.registry)}


def compare_reference(accounts, session, task_id, reference):
    exported = export_source(accounts, session, task_id)
    if reference.source_sha256 != exported['source']['content_sha256']:
        raise ReviewError('The reference JSON belongs to a different source file.', 409)
    ids = [r.candidate_id for r in reference.events]
    if len(ids) != len(set(ids)):
        raise ReviewError('Reference candidate IDs must be distinct.', 422)
    gold = {r.candidate_id: r.content.model_dump(mode='json') for r in reference.events}

    def differences(rows):
        actual = {r['candidate_id']: r['revision']['content'] for r in rows}
        missing, extra, changed, exact = [], [], [], []
        for key in gold.keys() - actual.keys():
            missing.append(key)
        for key in actual.keys() - gold.keys():
            extra.append(key)
        for key in actual.keys() & gold.keys():
            fields = {f: {'actual': actual[key][f], 'reference': gold[key][f]}
                      for f in EventContent.model_fields if actual[key][f] != gold[key][f]}
            (changed if fields else exact).append({'candidate_id': key, 'fields': fields} if fields else key)
        return {'missing': sorted(missing), 'extra': sorted(extra), 'changed': changed, 'exact_matches': sorted(exact)}
    return {'purpose': 'PERSONAL_DEVELOPMENT_COMPARISON_NOT_FORMAL_EVALUATION',
            'reference_trust': 'USER_SUPPLIED_NOT_INDEPENDENTLY_ATTESTED',
            'original_predictions': differences(exported['original_predictions']),
            'current_revisions': differences(exported['current_revisions']),
            'registered_accept_evaluation': 'NOT_MEASURED: requires approved independent validation and research gates.',
            'checked_by': reference.checked_by, 'note': reference.note}
