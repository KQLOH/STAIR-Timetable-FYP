"""Strict FR5 -> account-local FR6 bridge. Never registers or manufactures ACCEPT.

Use only host-loaded authentic receipts over the current protected snapshot.
Human date choices can select source-backed occurrences, never extend them.
The legacy TEST_ONLY M6 adapter and its authority contracts remain unchanged.
"""
from datetime import datetime, timezone

from backend.calendar.mapping import map_event
from backend.calendar.models import CalendarInput, CalendarContext, CalendarError
from backend.reconstruction.models import digest
from backend.review.models import ReviewError
from backend.review.registration import OperatingRegistry


def authentic_registry_available(registry):
    return isinstance(registry, OperatingRegistry) and registry.active and registry.mode == 'AUTHENTIC'


def _instant(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def bind_registered_rows(registry, store, session, task, rows, *, context_provider=None, schedule=None):
    if not authentic_registry_available(registry):
        raise ReviewError('Strict output needs an authentic approved operating point. '
                          'Independent validation and approval have not been supplied.', 409)
    snapshot = store.snapshot(str(task.task_id), session.principal)
    # Take event AND decision from the same current server-held snapshot.
    current = next((item for item in snapshot['tasks'] if item['task_id'] == str(task.task_id)), None)
    if current is None:
        raise ReviewError('The selected class is no longer available. Refresh your timetable.', 409)
    from backend.review.models import ReviewTask
    task = ReviewTask.model_validate(current)
    event, decision = task.current_event, task.decision
    context = snapshot['reconstruction_context']
    bundle = CalendarInput.model_validate({
        'packet': snapshot['packet'], 'reconstruction_status': snapshot['reconstruction_status'],
        'context': CalendarContext(timezone_evidence_ids=tuple(context.get('timezone_authority_ids', ())),
                                   term=context.get('term')).model_dump(mode='json')})
    if context_provider is not None:
        from backend.student.calendar_context import ApprovedContextFile
        if not isinstance(context_provider, ApprovedContextFile):
            raise ReviewError('Approved calendar-context host configuration is invalid.', 503)
        bundle = bundle.model_copy(update={'context': context_provider.context_for(bundle.packet)})
    if (event is None or bundle.reconstruction_status != 'COMPLETE_FINITE_SEARCH' or
            decision.decision.value != 'ACCEPT' or not decision.registered_routing_eligible or
            decision.registration_mode != 'AUTHENTIC' or not registry.verifies(decision, bundle.packet, event)):
        raise ReviewError('This class has no current registered ACCEPT. Revalidate it before strict output.', 409)
    if (event.validation.status.value not in {'VALID', 'WARNING'} or
            any(trace.severity == 'HARD' and trace.passed is not True for trace in event.validation.traces)):
        raise ReviewError('This ACCEPT no longer passes its constraint checks. Revalidate the class.', 409)
    try:
        payload = map_event(bundle, event)
    except CalendarError as error:
        raise ReviewError('Source evidence does not support strict calendar output: ' + error.code, 409) from None
    if schedule is not None and schedule.frequency == 'WEEKLY':
        recurrence = payload.recurrence
        if (recurrence is None or recurrence.frequency != 'WEEKLY' or
                schedule.first_date < recurrence.term_start or schedule.last_date > recurrence.term_end):
            raise ReviewError('Weekly output must stay within the accepted source teaching period.', 409)
        # Source week/exclusion/replacement semantics determine the actual dates.
        # The user's range selects a subset; it cannot create a weekly recurrence.
        selected = [o for o in payload.occurrences if
            schedule.first_date <= o.source_local_date <= schedule.last_date and
            o.source_local_date not in schedule.excluded_dates]
        if not selected:
            raise ReviewError('No source-supported classes remain in the selected range.', 409)
        template = rows[0]
        mapped = []
        for occurrence in selected:
            key = digest({'account': session.user_id, 'stable_session_key': payload.stable_key,
                          'date': occurrence.source_local_date.isoformat()})
            body = {**template['body'], 'summary': payload.title, 'location': payload.location or '',
                'start': {'dateTime': occurrence.start.isoformat(), 'timeZone': payload.source_timezone},
                'end': {'dateTime': occurrence.end.isoformat(), 'timeZone': payload.source_timezone},
                'extendedProperties': {'private': {'stairAccount': session.user_id, 'stairKey': key}}}
            mapped.append({**template, 'key': key, 'body': body})
        rows = mapped
    allowed = {(o.start.astimezone(timezone.utc), o.end.astimezone(timezone.utc)): o for o in payload.occurrences}
    binding = {'decision_id': str(decision.decision_id), 'registration_id': decision.registration_id,
               'registration_signature': decision.registration_signature,
               'revision_digest': decision.revision_digest,
               'mapped_payload_digest': digest(payload.model_dump(mode='json'))}
    result = []
    for row in rows:
        if (row['revision_id'] != str(event.revision.revision_id) or
                (_instant(row['body']['start']['dateTime']), _instant(row['body']['end']['dateTime'])) not in allowed):
            raise ReviewError('The chosen dates or repeats exceed the accepted source schedule. '
                              'Use only dates supported by the original timetable.', 409)
        occurrence = allowed[(_instant(row['body']['start']['dateTime']), _instant(row['body']['end']['dateTime']))]
        key = digest({'account': session.user_id, 'stable_session_key': payload.stable_key,
                      'date': occurrence.source_local_date.isoformat()})
        body = {**row['body'], 'summary': payload.title, 'location': payload.location or '',
            'start': {'dateTime': occurrence.start.isoformat(), 'timeZone': payload.source_timezone},
            'end': {'dateTime': occurrence.end.isoformat(), 'timeZone': payload.source_timezone},
            'extendedProperties': {'private': {'stairAccount': session.user_id, 'stairKey': key}},
            'description': 'STAIR: current registered ACCEPT; explicitly approved sandbox output.'}
        result.append({**row, 'key': key, 'body': body, 'registered_accept': binding})
    return result
