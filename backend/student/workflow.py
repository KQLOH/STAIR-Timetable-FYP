"""Revision-bound human planning for account-owned timetables.

Human-confirmed mode is an application sandbox, not registered M5 ACCEPT.
The opt-in registered-accept mode requires current authentic M5 receipts and
evidence-backed M6 temporal mapping. Neither establishes independent M7 analysis.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal
from secrets import token_hex
import json

from pydantic import Field, model_validator
from backend.models.evidence import ContractModel
from backend.models.event import EventContent
from backend.reconstruction.models import digest
from backend.review.models import ReviewError


class Schedule(ContractModel):
    task_id: str = Field(min_length=1, max_length=100)
    revision_id: str = Field(min_length=1, max_length=100)
    frequency: Literal['ONCE', 'WEEKLY']
    first_date: date
    last_date: date | None = None
    excluded_dates: tuple[date, ...] = Field(default=(), max_length=60)
    timezone: str = Field(min_length=1, max_length=80)
    source_note: str = Field(default='', max_length=1000)
    checked_original: Literal[True]

    @model_validator(mode='after')
    def bounded(self):
        if self.frequency == 'WEEKLY' and self.last_date is None:
            raise ValueError('Weekly classes need an explicit last date.')
        end = self.last_date or self.first_date
        if end < self.first_date or (end - self.first_date).days > 366:
            raise ValueError('Use a date range of at most one year.')
        if self.frequency == 'ONCE' and (end != self.first_date or self.excluded_dates):
            raise ValueError('One-time classes use one date and no exclusions.')
        if len(set(self.excluded_dates)) != len(self.excluded_dates) or any(
                d < self.first_date or d > end or d.isoweekday() != self.first_date.isoweekday()
                for d in self.excluded_dates):
            raise ValueError('Excluded dates must be distinct class dates in this range.')
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError('Choose a valid IANA timezone.') from None
        return self


class BatchClass(ContractModel):
    task_id: str = Field(min_length=1, max_length=100)
    revision_id: str = Field(min_length=1, max_length=100)
    # Only needed when a one-time class has no date in its source.
    first_date: date | None = None


class BatchSchedule(ContractModel):
    classes: tuple[BatchClass, ...] = Field(min_length=1, max_length=100)
    frequency: Literal['ONCE', 'WEEKLY']
    timezone: str = Field(min_length=1, max_length=80)
    starts_on: date | None = None
    ends_on: date | None = None
    excluded_dates: tuple[date, ...] = Field(default=(), max_length=60)
    source_note: str = Field(default='', max_length=1000)
    checked_original: Literal[True]

    @model_validator(mode='after')
    def common_settings(self):
        if len({c.task_id for c in self.classes}) != len(self.classes):
            raise ValueError('Select each class only once.')
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError('Choose a valid timezone.') from None
        if self.frequency == 'WEEKLY':
            if self.starts_on is None or self.ends_on is None:
                raise ValueError('Choose when weekly repeats start and end.')
            if not 0 <= (self.ends_on - self.starts_on).days <= 366:
                raise ValueError('Use a repeat range of at most one year, ending after it starts.')
            if any(d < self.starts_on or d > self.ends_on for d in self.excluded_dates):
                raise ValueError('Dates with no class must be inside the repeat range.')
        elif self.starts_on is not None or self.ends_on is not None or self.excluded_dates:
            raise ValueError('One-time classes use their own dates, without a repeat range or exclusions.')
        if len(set(self.excluded_dates)) != len(self.excluded_dates):
            raise ValueError('Enter each excluded date only once.')
        return self


def current_tasks(accounts, session):
    store = accounts.store(session)
    if not session.principal:
        return store, []
    tasks, offset = [], 0
    while True:
        page = store.queue_summary(session.principal, offset, 100, include_source=True)
        tasks.extend(page['tasks'])
        offset = page['next_offset']
        if offset is None:
            return store, tasks
        if len(tasks) > 2000:
            raise ReviewError('Too many classes for one planning session.', 413)


def owned_task(accounts, session, task_id):
    if not session.principal:
        raise ReviewError('Connect your timetable before continuing.', 409)
    store = accounts.store(session)
    task = store.get(task_id, session.principal)
    if task.current_event is None:
        raise ReviewError('This candidate needs source repair before planning.', 409)
    return store, task



def supported_value(event, name):
    """A planning checkbox cannot resolve competing field readings.

    Match the current selected value against every retained alternative, using
    the same typed JSON/plain-reading semantics as observed workload analysis.
    Explicit source-linked corrections replace these observations normally.
    """
    value = getattr(event.revision.content, name)
    observation = next((o for o in event.revision.observations if o.field_name == name), None)
    if value is None or observation is None or observation.state.value != 'PRESENT':
        return False
    for alternative in observation.alternatives:
        if alternative.normalized_value is None:
            return False
        readings = [alternative.normalized_value]
        try:
            readings.append(json.loads(alternative.normalized_value))
        except (ValueError, TypeError):
            pass
        for reading in readings:
            try:
                if getattr(EventContent.model_validate({name: reading}), name) == value:
                    break
            except ValueError:
                continue
        else:
            return False
    return True


def occurrences(task, schedule, user_id):
    event = task.current_event
    content = event.revision.content
    if str(event.revision.revision_id) != schedule.revision_id:
        raise ReviewError('Class details changed. Check the latest version again.', 409)
    if task.state.value in {'REVALIDATION_REQUIRED', 'ESCALATED'}:
        raise ReviewError('A correction still needs checking. Use Check saved class before scheduling.', 409)
    if event.extraction_status.value in {'CONFLICTING', 'UNAVAILABLE'}:
        raise ReviewError('Resolve conflicting source readings before scheduling.', 409)
    for name in ('start_time', 'end_time'):
        if not supported_value(event, name):
            raise ReviewError('Check and correct the class start and end times first. Resolve uncertain source readings in Edit class.', 409)
    if not (supported_value(event, 'course_code') or supported_value(event, 'course_name')):
        raise ReviewError('A class needs a source-supported course name or code. Resolve uncertain readings in Edit class.', 409)
    for name in ('course_code', 'course_name', 'session_type', 'date', 'day_of_week', 'source_timezone', 'location'):
        if getattr(content, name) is not None and not supported_value(event, name):
            raise ReviewError('Resolve the uncertain ' + name.replace('_', ' ') + ' reading in Edit class before scheduling.', 409)
    # Proven hard failures may never be cleared merely by ticking a checkbox.
    if any(f.severity == 'HARD' and f.passed is False for f in task.decision.findings):
        raise ReviewError('This class has a failed safety check. Correct it and run Check changes first.', 409)
    if content.day_of_week and schedule.first_date.isoweekday() != content.day_of_week:
        raise ReviewError('The first date does not match the class weekday. Correct the source reading first.', 422)
    if content.date and schedule.frequency == 'ONCE' and content.date != schedule.first_date:
        raise ReviewError('The chosen date differs from the saved class date. Correct the class date first.', 422)
    if content.source_timezone and content.source_timezone != schedule.timezone:
        raise ReviewError('The selected timezone differs from the saved source timezone.', 422)
    zone = ZoneInfo(schedule.timezone)
    left = int(content.start_time[:2]) * 60 + int(content.start_time[3:])
    right = int(content.end_time[:2]) * 60 + int(content.end_time[3:]) + (content.end_day_offset or 0) * 1440
    if not 0 < right - left <= 1440:
        raise ReviewError('The end must be after the start, within one day.', 422)
    day, end = schedule.first_date, schedule.last_date or schedule.first_date
    result = []
    while day <= end:
        if day not in schedule.excluded_dates:
            start = datetime.combine(day, time()) + timedelta(minutes=left)
            finish = datetime.combine(day, time()) + timedelta(minutes=right)
            instants = []
            for value in (start, finish):
                aware = value.replace(tzinfo=zone)
                if (aware.utcoffset() != value.replace(tzinfo=zone, fold=1).utcoffset() or
                        aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value):
                    raise ReviewError('This date has an ambiguous or missing local time. Review it explicitly.', 422)
                instants.append(aware.isoformat())
            key = digest({'account': user_id, 'source': event.revision.source_document_id,
                          'candidate': event.candidate_id, 'date': day.isoformat()})
            body = {'summary': ' · '.join(filter(None, (content.course_code, content.course_name, content.session_type))),
                    'location': content.location or '',
                    'start': {'dateTime': instants[0], 'timeZone': schedule.timezone},
                    'end': {'dateTime': instants[1], 'timeZone': schedule.timezone},
                    'description': 'STAIR: personally checked class. Application confirmation; not independent Gold.',
                    'extendedProperties': {'private': {'stairAccount': user_id, 'stairKey': key}}}
            result.append({'key': key, 'body': body, 'candidate_id': event.candidate_id,
                           'task_id': str(task.task_id), 'revision_id': schedule.revision_id,
                           'source_note': schedule.source_note})
        day += timedelta(days=7 if schedule.frequency == 'WEEKLY' else 1)
    if not result:
        raise ReviewError('All class dates were excluded. No dates remain to synchronize.', 422)
    return result


def save_schedule(accounts, session, schedule):
    _, task = owned_task(accounts, session, schedule.task_id)
    occurrences(task, schedule, session.user_id)
    with accounts.transaction(write=True) as state:
        workspace = state['workspaces'][session.user_id]
        workspace['checks'][schedule.task_id] = schedule.model_dump(mode='json')
        workspace['audit'].append({'operation': 'PERSONAL_SCHEDULE_CONFIRMATION', 'at': accounts.clock(),
                                   'task_id': schedule.task_id, 'revision_id': schedule.revision_id})
        workspace['plans'] = {key: plan for key, plan in workspace['plans'].items() if plan['outcome'] is not None}


def save_batch(accounts, session, batch):
    """Confirm a common schedule once; retain individual revision/safety checks.

Source dates are never replaced in one-time mode. Weekly expansion is an
explicit application choice, not evidence of an independently verified term.
"""
    ready, blocked, intervals = [], [], []
    source_binding = None
    for item in batch.classes:
        store, task = owned_task(accounts, session, item.task_id)
        source = store.source(item.task_id, session.principal)
        binding = (source.source_document_id, source.content_sha256)
        if source_binding is not None and binding != source_binding:
            raise ReviewError('Select classes from the same timetable for one batch.', 422)
        source_binding = binding
        content = task.current_event.revision.content
        try:
            if str(task.current_event.revision.revision_id) != item.revision_id:
                raise ReviewError('This class changed. Refresh and check its latest details.', 409)
            if batch.frequency == 'ONCE':
                first = content.date or item.first_date
                if first is None:
                    raise ReviewError('The class date is missing. Enter its date, or choose a known weekly range.', 422)
                if content.date and item.first_date and item.first_date != content.date:
                    raise ReviewError('This date differs from the saved source. Correct the class date first.', 422)
                excluded = ()
            else:
                weekday = content.day_of_week
                anchor = content.date or item.first_date
                if weekday is None and anchor is not None:
                    weekday = anchor.isoweekday()
                if weekday is None:
                    raise ReviewError('The class weekday is missing. Correct it or supply a source-checked date.', 422)
                first = batch.starts_on + timedelta(days=(weekday - batch.starts_on.isoweekday()) % 7)
                if first > batch.ends_on:
                    raise ReviewError('This class has no occurrence in the selected repeat range.', 422)
                excluded = tuple(d for d in batch.excluded_dates if d >= first and d.isoweekday() == weekday)
            schedule = Schedule(task_id=item.task_id, revision_id=item.revision_id,
                frequency=batch.frequency, first_date=first,
                last_date=batch.ends_on if batch.frequency == 'WEEKLY' else None,
                timezone=batch.timezone, excluded_dates=excluded, source_note=batch.source_note,
                checked_original=True)
            rows = occurrences(task, schedule, session.user_id)
            intervals.extend((datetime.fromisoformat(r['body']['start']['dateTime']),
                              datetime.fromisoformat(r['body']['end']['dateTime']), item.task_id) for r in rows)
            ready.append(schedule)
        except (ReviewError, ValueError) as error:
            blocked.append({'task_id': item.task_id, 'message': error.code if isinstance(error, ReviewError) else str(error)})
    # Flag both sides of a clash; never arbitrarily choose a user's class group.
    active, conflicting = [], set()
    for start, end, task_id in sorted(intervals):
        active = [other for other in active if other[0] > start]
        for _, other_id in active:
            if other_id != task_id:
                conflicting.update((task_id, other_id))
        active.append((end, task_id))
    ready = [schedule for schedule in ready if schedule.task_id not in conflicting]
    blocked.extend({'task_id': task_id, 'message': 'Overlaps another selected class. Select the correct group or correct its time.'}
                   for task_id in sorted(conflicting))
    with accounts.transaction(write=True) as state:
        workspace = state['workspaces'][session.user_id]
        for schedule in ready:
            workspace['checks'][schedule.task_id] = schedule.model_dump(mode='json')
        for item in blocked:
            workspace['checks'].pop(item['task_id'], None)
        workspace['plans'] = {key: plan for key, plan in workspace['plans'].items() if plan['outcome'] is not None}
        workspace['audit'].append({'operation': 'PERSONAL_BATCH_SCHEDULE_CONFIRMATION', 'at': accounts.clock(),
            'confirmed_revisions': {s.task_id: s.revision_id for s in ready}, 'blocked': blocked})
    return {'ready_task_ids': [s.task_id for s in ready], 'blocked': blocked,
            'independently_verified': False, 'write_approved': False}


def collect(accounts, session, task_ids):
    if not 1 <= len(task_ids) <= 100 or len(set(task_ids)) != len(task_ids):
        raise ReviewError('Select between 1 and 100 distinct classes.', 422)
    with accounts.transaction() as state:
        checks = state['workspaces'][session.user_id]['checks'].copy()
    rows = []
    for task_id in task_ids:
        if task_id not in checks:
            raise ReviewError('Confirm the dates and repeat schedule for every selected class.', 409)
        store, task = owned_task(accounts, session, task_id)
        schedule = Schedule.model_validate(checks[task_id])
        selected_rows = occurrences(task, schedule, session.user_id)
        if accounts.calendar_policy == 'registered-accept':
            from backend.student.registered_calendar import bind_registered_rows
            options = {'schedule': schedule}
            if accounts.calendar_context is not None: options['context_provider'] = accounts.calendar_context
            selected_rows = bind_registered_rows(accounts.registry, store, session, task, selected_rows, **options)
        rows.extend(selected_rows)
    if len(rows) > 500:
        raise ReviewError('Select fewer classes or a shorter range (maximum 500 occurrences).', 413)
    ordered = sorted(rows, key=lambda r: r['body']['start']['dateTime'])
    # Compare absolute instants, including overnight and differing IANA zones.
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if (datetime.fromisoformat(a['body']['start']['dateTime']) < datetime.fromisoformat(b['body']['end']['dateTime']) and
                    datetime.fromisoformat(b['body']['start']['dateTime']) < datetime.fromisoformat(a['body']['end']['dateTime'])):
                raise ReviewError('Selected classes overlap or are duplicated. Resolve the clash or select the correct group.', 409)
    return rows


def local_preview(accounts, session, task_ids, target='local', remote=None):
    if accounts.calendar_policy == 'registered-accept' and (target != 'local' or remote is not None):
        raise ReviewError('Strict FYP output uses the project-local sandbox only.', 409)
    rows = collect(accounts, session, task_ids)
    with accounts.transaction() as state:
        workspace = state['workspaces'][session.user_id]
        entries = workspace['entries'].copy()
    remote_entries = remote.owned_entries() if remote else None
    target_entries = {key: remote.content(value) for key, value in remote_entries.items()} if remote else entries
    selected_keys = {r['key'] for r in rows}
    for row in rows:
        proposed = row['body']
        for key, existing in target_entries.items():
            if key in selected_keys:
                continue
            # A new capture cannot silently create a second copy of an already
            # saved occurrence, nor overlap another STAIR class in this target.
            if (datetime.fromisoformat(proposed['start']['dateTime']) < datetime.fromisoformat(existing['end']['dateTime']) and
                    datetime.fromisoformat(existing['start']['dateTime']) < datetime.fromisoformat(proposed['end']['dateTime'])):
                raise ReviewError('A saved STAIR class already occupies this time. Open its saved timetable to update it; do not import a second copy.', 409)
    operations = []
    for row in rows:
        previous = remote.read(row['key']) if remote else entries.get(row['key'])
        previous_body = remote.content(previous) if remote else previous
        desired_body = remote.content(row['body']) if remote else row['body']
        action = 'CREATE' if previous is None else 'NO_OP' if previous_body == desired_body else 'UPDATE'
        operations.append({**row, 'action': action, 'before': previous_body,
                           'etag': previous.get('etag') if remote and previous else None})
    plan = {'id': token_hex(16), 'target': target, 'created': accounts.clock(), 'expires': accounts.clock() + 600,
            'calendar_policy': accounts.calendar_policy,
            'binding': digest(session.csrf), 'operations': operations, 'task_ids': task_ids,
            'rows_digest': digest(rows), 'target_digest': digest(target_entries),
            'approved': False, 'outcome': None}
    with accounts.transaction(write=True) as state:
        plans = state['workspaces'][session.user_id]['plans']
        for key in list(plans):
            if plans[key]['expires'] < accounts.clock() and plans[key]['outcome'] is None:
                plans.pop(key)
        # Completed results remain available for history/reconciliation. Only
        # outstanding previews consume the bounded active-preview allowance.
        if sum(plan['outcome'] is None for plan in plans.values()) >= 50:
            raise ReviewError('Too many previews. Start a new session later or use existing results.', 409)
        plans[plan['id']] = plan
    return plan


def get_plan(accounts, session, plan_id, *, active=True):
    with accounts.transaction() as state:
        plan = state['workspaces'][session.user_id]['plans'].get(plan_id)
        if not plan:
            raise ReviewError('Preview was not found. Create a fresh preview.', 404)
        if active and plan.get('calendar_policy', 'human-confirmed') != accounts.calendar_policy:
            raise ReviewError('The calendar policy changed. Create a fresh preview and approval.', 409)
        if active and (plan['binding'] != digest(session.csrf) or plan['expires'] <= accounts.clock()):
            raise ReviewError('This preview expired or belongs to an earlier session. Preview again.', 409)
        import copy
        return copy.deepcopy(plan)


def approve(accounts, session, plan_id):
    plan = get_plan(accounts, session, plan_id)
    if digest(collect(accounts, session, plan['task_ids'])) != plan['rows_digest']:
        raise ReviewError('The timetable changed. Preview the latest classes.', 409)
    with accounts.transaction(write=True) as state:
        stored = state['workspaces'][session.user_id]['plans'][plan_id]
        if stored['outcome'] is not None:
            raise ReviewError('This preview was already applied. Check its result.', 409)
        stored['approved'] = True
        state['workspaces'][session.user_id]['audit'].append(
            {'operation': 'APPROVE_EXACT_PREVIEW', 'plan_id': plan_id, 'at': accounts.clock()})
    return get_plan(accounts, session, plan_id)


def execute(accounts, session, plan_id, remote=None):
    plan = get_plan(accounts, session, plan_id)
    if not plan['approved']:
        raise ReviewError('Review and approve this preview before applying it.', 409)
    if plan['outcome'] is not None:
        return plan['outcome']
    if digest(collect(accounts, session, plan['task_ids'])) != plan['rows_digest']:
        raise ReviewError('The timetable changed. A new preview and approval are required.', 409)
    if remote is not None and digest({key: remote.content(value) for key, value in remote.owned_entries().items()}) != plan['target_digest']:
        raise ReviewError('The Google test calendar changed. Preview the latest target before applying.', 409)
    with accounts.transaction(write=True) as state:
        workspace = state['workspaces'][session.user_id]
        if remote is None:
            if plan['target'] != 'local' or digest(workspace['entries']) != plan['target_digest']:
                raise ReviewError('The target calendar changed. Preview again.', 409)
            for operation in plan['operations']:
                workspace['entries'][operation['key']] = operation['body']
            outcome = {'status': 'SUCCEEDED', 'target': 'local', 'applied': len(plan['operations']),
                       'operations': [{'key': op['key'], 'action': op['action'], 'status': 'CONFIRMED'} for op in plan['operations']]}
        else:
            if plan['target'] != remote.target:
                raise ReviewError('The Google target changed. Preview again.', 409)
            # Commit intent BEFORE networking. Crash/timeout never silently resends.
            outcome = {'status': 'CHECK_REQUIRED', 'target': plan['target'], 'operations': []}
        workspace['plans'][plan_id]['outcome'] = outcome
        workspace['audit'].append({'operation': 'APPLY_APPROVED_PREVIEW', 'plan_id': plan_id, 'at': accounts.clock()})
    if remote is not None:
        results = []
        for op in plan['operations']:
            try:
                result = remote.apply(op)
            except ReviewError:
                result = 'CHECK_REQUIRED'
            results.append({'key': op['key'], 'action': op['action'], 'status': result})
            if result != 'CONFIRMED':
                break
        outcome = {'status': 'SUCCEEDED' if len(results) == len(plan['operations']) and all(
            r['status'] == 'CONFIRMED' for r in results) else 'CHECK_REQUIRED',
                   'target': plan['target'], 'operations': results}
        with accounts.transaction(write=True) as state:
            state['workspaces'][session.user_id]['plans'][plan_id]['outcome'] = outcome
    return outcome
