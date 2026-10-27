"""C1-style development arithmetic and separate deterministic suggestions.

All durations use UTC elapsed seconds; date buckets use the explicit scope zone.
Candidate arithmetic is not a claim of independent source correctness.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path

from backend.analysis.authority import CompletenessAuthority
from backend.analysis.models import binding
from backend.calendar.mapping import normalize_occurrences, _local
from backend.calendar.models import CalendarError
from backend.reconstruction.models import digest

VERSION = 'm7-development-v1'
SETTINGS = json.loads((Path(__file__).resolve().parents[2]/'config/m7-development-v1.json').read_text())
REGISTERED_BLOCKERS = ('PRE01_NOT_READY', 'PRE02_NOT_FROZEN', 'REGISTERED_WINDOW_POLICY_NOT_FROZEN',
                       'P1A_PROVIDER_PRIVACY_CONFIGURATION_UNAVAILABLE')
TEMPORAL_FIELDS = {'date', 'day_of_week', 'start_time', 'end_time', 'end_day_offset', 'source_timezone',
                   'teaching_weeks', 'excluded_weeks', 'recurrence', 'cohort_id', 'section', 'course_code', 'course_name'}


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]: result[-1] = (result[-1][0], max(end, result[-1][1]))
        else: result.append((start, end))
    return result


def minutes(intervals):
    return sum((end-start).total_seconds()/60 for start, end in union(intervals))


def event_ref(packet, event):
    return {'candidate_id': event.candidate_id, 'event_id': str(event.identity.event_id),
            'revision_id': str(event.revision.revision_id), 'revision_digest': digest(event.revision.model_dump(mode='json')),
            'revision_version':event.revision.version, 'previous_revision_id':str(event.revision.previous_revision_id) if event.revision.previous_revision_id else None,
            'source_document_id': packet.source.source_document_id, 'source_sha256': packet.source.content_sha256,
            'method_id': event.revision.method_id, 'evidence_route': packet.source.route,
            'evidence_ids': sorted({i for o in event.revision.observations for i in o.evidence_ids})}


def analyze(request, authority=None):
    if request.profile == 'registered': raise ValueError('REGISTERED_ANALYSIS_BLOCKED')
    authority = authority or CompletenessAuthority()
    attested, reason = authority.assess(request)
    zone = ZoneInfo(request.scope.timezone)
    horizon = (_local(request.scope.start, '00:00', zone).astimezone(timezone.utc),
               _local(request.scope.end+timedelta(days=1), '00:00', zone).astimezone(timezone.utc))
    issues = []
    occurrences = []
    accepted = []
    seen = {}
    stable = defaultdict(list)
    for e in request.packet.events:
        if e.identity.stable_key: stable[e.identity.stable_key].append(e)
    contradictory = {e.candidate_id for group in stable.values()
                     if len({digest(e.revision.content.model_dump(mode='json')) for e in group}) > 1 for e in group}
    for e in request.packet.events:
        c = e.revision.content
        ref = event_ref(request.packet, e)
        def issue(code): issues.append({'reason_code': code, 'event_refs': [ref]})
        if request.scope.cohort_id and c.cohort_id and request.scope.cohort_id != c.cohort_id: continue
        if not e.revision.core_fields_present: issue('MISSING_CORE_FIELDS')
        if e.candidate_id in contradictory:
            issue('CONFLICTING_REVISIONS_OF_SOURCE_SESSION'); continue
        if e.extraction_status in {'AMBIGUOUS', 'CONFLICTING', 'UNAVAILABLE'}:
            issue('UNRESOLVED_CANDIDATE_ALTERNATIVES'); continue
        if any(o.field_name in TEMPORAL_FIELDS and (o.state in {'UNRESOLVED', 'CONFLICTING'} or len(o.alternatives)>1)
               for o in e.revision.observations):
            issue('UNRESOLVED_ANALYSIS_FIELD'); continue
        if not c.start_time or not c.end_time:
            issue('MISSING_TIME'); continue
        if c.source_class_status and c.source_class_status.casefold() not in {'normal', 'scheduled'}:
            issue('SOURCE_STATUS_UNRESOLVED'); continue
        # One explicit date is not expanded into a guessed weekly recurrence.
        try: mapped, _ = normalize_occurrences(request, e)
        except (CalendarError, ValueError, TypeError) as exc:
            issue(exc.code if isinstance(exc, CalendarError) else 'UNSUPPORTED_TEMPORAL_EVIDENCE'); continue
        key = e.identity.stable_key or digest({'content': c.model_dump(mode='json'), 'evidence': ref['evidence_ids']})
        if key in seen:
            issue('DUPLICATE_SOURCE_SESSION_COLLAPSED')
            for row in occurrences:
                if row['key'] == key: row['event_refs'].append(ref)
            continue
        seen[key] = e
        rows = []
        for item in mapped:
            start = max(item.start.astimezone(timezone.utc), horizon[0])
            end = min(item.end.astimezone(timezone.utc), horizon[1])
            if start < end:
                rows.append({'start': start, 'end': end, 'key': key, 'cohort': c.cohort_id, 'section': c.section,
                             'course': c.course_code or c.course_name, 'session_type': c.session_type,
                             'week': next((w.week for w in request.context.weeks if w.start<=item.source_local_date<=w.end), None),
                             'event_refs': [ref]})
        if rows: accepted.append(e); occurrences.extend(rows)
        if len(occurrences)>SETTINGS['occurrence_cap']:
            occurrences=[]
            issue('ANALYSIS_OCCURRENCE_LIMIT_EXCEEDED')
            break
    # Different interpretations of one source region at the same time are alternatives,
    # not a confirmed conflict. Distinct cells/sessions retain their own evidence.
    ambiguous = set()
    for i, a in enumerate(occurrences):
        for j, b in enumerate(occurrences[:i]):
            if (a['key'] != b['key'] and a['start'] == b['start'] and a['end'] == b['end'] and
                a['event_refs'][0]['evidence_ids'] == b['event_refs'][0]['evidence_ids'] and
                a['course'] != b['course']): ambiguous.update((i, j))
    if ambiguous:
        issues.append({'reason_code': 'COMPETING_SOURCE_INTERPRETATIONS',
                       'event_refs': [r for i in sorted(ambiguous) for r in occurrences[i]['event_refs']]})
        occurrences = [r for i, r in enumerate(occurrences) if i not in ambiguous]
    if any(not r['cohort'] for r in occurrences):
        issues.append({'reason_code': 'COHORT_SCOPE_UNRESOLVED', 'event_refs': []})
    if len({r['section'] for r in occurrences})>1:
        issues.append({'reason_code': 'SECTION_MEMBERSHIP_UNRESOLVED', 'event_refs': []})
    if not request.scope.cohort_id: issues.append({'reason_code': 'SINGLE_COHORT_SCOPE_REQUIRED_FOR_COMPLETENESS', 'event_refs': []})
    if request.packet.issues: issues.append({'reason_code': 'SOURCE_PROCESSING_ISSUES_REQUIRE_ADJUDICATION', 'event_refs': []})
    if request.declared_kind == 'EXPLICITLY_INCOMPLETE': attested = False
    # Even a trusted record cannot override a failed executable normalization gate.
    complete = attested and not issues
    blockers = ([] if complete else [reason if not attested else 'NORMALIZATION_OR_COVERAGE_UNRESOLVED'])
    blockers += sorted({i['reason_code'] for i in issues})
    label = 'COMPLETE SCHEDULE VERIFIED' if complete else 'OBSERVED EVENTS ONLY'
    all_refs = [event_ref(request.packet, e) for e in request.packet.events]
    refs = list({r['candidate_id']:r for row in occurrences for r in row['event_refs']}.values())
    facts = []
    def fact(kind, value=None, *, status='COMPUTED', reasons=(), supporting=None):
        item = {'type': kind, 'status': status, 'value': value, 'reason_codes': list(reasons),
                'event_refs': refs if supporting is None else supporting, 'scope': request.scope.model_dump(mode='json'),
                'evidence_basis': label}
        item['fact_id'] = 'fact:'+digest({'binding': binding(request), **item})
        facts.append(item)
        return item
    fact('observed_candidate_count', len(request.packet.events), supporting=all_refs,
         reasons=('SUBMITTED_RECORD_COUNT_NOT_COMPLETENESS_OR_SCOPED_OCCURRENCE_COUNT',))
    fact('normalized_session_count', len({r['key'] for r in occurrences}))
    intervals = [(r['start'], r['end']) for r in occurrences]
    has_intervals = bool(intervals)
    fact('observed_scheduled_hours', minutes(intervals)/60 if has_intervals else None,
         status='COMPUTED' if has_intervals else 'INSUFFICIENT_EVIDENCE', reasons=() if has_intervals else ('NO_SUPPORTED_DATED_OCCURRENCES',))
    breakdown = {k: defaultdict(list) for k in ('day', 'course', 'session_type', 'teaching_week')}
    daily_counts = defaultdict(int)
    for row in occurrences:
        start, end = row['start'], row['end']
        cursor = start
        while cursor < end:
            day = cursor.astimezone(zone).date()
            stop = min(end, _local(day+timedelta(days=1), '00:00', zone).astimezone(timezone.utc))
            breakdown['day'][day.isoformat()].append((cursor, stop)); cursor = stop
        daily_counts[start.astimezone(zone).date().isoformat()] += 1
        for k, val in (('course', row['course']), ('session_type', row['session_type']), ('teaching_week', row['week'])):
            if val is not None: breakdown[k][str(val)].append((start, end))
    for key, groups in breakdown.items():
        missing = any(r[{'course':'course', 'session_type':'session_type', 'teaching_week':'week'}.get(key, 'start')] is None for r in occurrences)
        fact('hours_by_'+key, {k: minutes(v)/60 for k,v in sorted(groups.items())} if groups else None,
             status='INSUFFICIENT_EVIDENCE' if missing or not groups else 'COMPUTED',
             reasons=('UNASSIGNED_OR_UNSUPPORTED_BREAKDOWN_VALUES',) if missing or not groups else ())
    fact('day_event_distribution', dict(sorted(daily_counts.items())), status='COMPUTED' if occurrences else 'NOT_ASSESSED',
         reasons=() if occurrences else ('NO_SUPPORTED_DATED_OCCURRENCES',))
    day_minutes = {d: minutes(v) for d, v in breakdown['day'].items()}
    fact('tied_busiest_active_days', sorted(d for d,v in day_minutes.items() if v == max(day_minutes.values())) if day_minutes else None,
         status='COMPUTED' if day_minutes else 'INSUFFICIENT_EVIDENCE')
    fact('tied_lightest_active_days', sorted(d for d,v in day_minutes.items() if v == min(day_minutes.values())) if day_minutes else None,
         status='COMPUTED' if day_minutes else 'INSUFFICIENT_EVIDENCE')
    def window(start, end): return {'start': start.astimezone(zone).isoformat(), 'end': end.astimezone(zone).isoformat()}
    fact('earliest_latest_observed', {'earliest':min(s for s,e in intervals).astimezone(zone).isoformat(),
                                    'latest':max(e for s,e in intervals).astimezone(zone).isoformat()} if intervals else None,
         status='COMPUTED' if intervals else 'INSUFFICIENT_EVIDENCE')
    fact('occupancy_intervals', [window(s,e) for s,e in union(intervals)], status='COMPUTED' if intervals else 'NOT_ASSESSED',
         reasons=() if intervals else ('NO_SUPPORTED_DATED_OCCURRENCES',))
    fact('normalized_occurrences', [{**window(r['start'],r['end']), 'event_refs':r['event_refs'], 'teaching_week':r['week']}
                                    for r in occurrences])
    conflicts = []; consecutive = []; unknown_pairs = 0
    for i,a in enumerate(occurrences):
        for b in occurrences[:i]:
            common = a['cohort'] and a['cohort'] == b['cohort'] and (a['section'] == b['section'] or not a['section'] and not b['section'])
            overlap = max(a['start'],b['start']) < min(a['end'],b['end'])
            pair_refs = a['event_refs']+b['event_refs']
            if common and overlap: conflicts.append({**window(max(a['start'],b['start']),min(a['end'],b['end'])), 'event_refs':pair_refs})
            elif overlap and not common: unknown_pairs += 1
            if common and (a['end'] == b['start'] or b['end'] == a['start']):
                consecutive.append({'event_refs': pair_refs})
    pair_status='COMPUTED' if occurrences and all(r['cohort'] for r in occurrences) and not unknown_pairs else 'NOT_ASSESSED'
    pair_reasons=() if pair_status=='COMPUTED' else ('OCCURRENCE_OR_COHORT_RELATIONSHIPS_NOT_ASSESSED',)
    fact('confirmed_conflicts', conflicts, status=pair_status, reasons=pair_reasons)
    fact('observed_consecutive_sessions', consecutive, status=pair_status, reasons=pair_reasons)
    fact('long_scheduled_blocks', [window(s,e) for s,e in union(intervals) if (e-s).total_seconds()/60>=request.long_block_minutes]
         if request.long_block_minutes else None, status='COMPUTED' if request.long_block_minutes and intervals else 'NOT_ASSESSED',
         reasons=() if request.long_block_minutes else ('EXPLICIT_DEVELOPMENT_THRESHOLD_REQUIRED',))
    free = []; available_valid = bool(request.availability)
    bounds = sorted((a.start.astimezone(timezone.utc), a.end.astimezone(timezone.utc)) for a in request.availability)
    for i,(start,end) in enumerate(bounds):
        if start<horizon[0] or end>horizon[1] or (i and start<bounds[i-1][1]): available_valid=False
    # Explicit availability offsets must agree with the declared source-local zone.
    for a in request.availability:
        if any(v.utcoffset()!=v.astimezone(zone).utcoffset() for v in (a.start,a.end)): available_valid=False
    if complete and available_valid:
        occupied = union(intervals)
        for start,end in bounds:
            cursor = start
            for left,right in occupied:
                if right<=cursor or left>=end: continue
                if left>cursor: free.append((cursor,min(left,end)))
                cursor=max(cursor,right)
                if cursor>=end: break
            if cursor<end: free.append((cursor,end))
    free_ok = complete and available_valid
    free_reason = () if free_ok else ('TRUSTED_COMPLETE_SCOPE_AND_VALID_EXPLICIT_AVAILABILITY_REQUIRED',)
    free_fact = fact('verified_available_windows', [window(s,e) for s,e in free] if free_ok else None,
                     status='COMPUTED' if free_ok else 'NOT_ASSESSED', reasons=free_reason)
    fact('total_scope_workload_hours', minutes(intervals)/60 if complete else None,
         status='COMPUTED' if complete else 'NOT_ASSESSED', reasons=() if complete else ('COMPLETE_SCHEDULE_REQUIRED',))
    # A whole free day requires full midnight-to-midnight explicit availability.
    full_days=[]
    for s,e in bounds if free_ok else ():
        day=s.astimezone(zone).date()
        while day<=e.astimezone(zone).date():
            left=_local(day,'00:00',zone).astimezone(timezone.utc);right=_local(day+timedelta(days=1),'00:00',zone).astimezone(timezone.utc)
            if s<=left and right<=e and not any(max(left,a)<min(right,b) for a,b in intervals):full_days.append(day.isoformat())
            day+=timedelta(days=1)
    fact('verified_free_days', sorted(set(full_days)) if free_ok else None, status='COMPUTED' if free_ok else 'NOT_ASSESSED',
         reasons=free_reason+('ONLY_FULL_DAY_EXPLICIT_AVAILABILITY_IS_ASSESSED',))
    recs=[]
    def recommend(kind, supporting_fact, explanation, value=None):
        item={'version':VERSION, 'type':kind, 'fact_ids':[supporting_fact['fact_id']], 'event_refs':supporting_fact['event_refs'],
              'scope':request.scope.model_dump(mode='json'), 'preconditions':['TRUSTED_COMPLETE_SCHEDULE', 'VALIDATED_C1_DEVELOPMENT_FACT'],
              'reason_codes':['DETERMINISTIC_DEVELOPMENT_RULE'], 'limitations':['NOT_REGISTERED_P1A','NO_PERSONAL_PREFERENCE_RANKING'],
              'explanation':explanation, 'window':value}
        item['recommendation_id']='rec:'+digest(item);recs.append(item)
    if complete:
        cf=next(f for f in facts if f['type']=='confirmed_conflicts')
        if conflicts:recommend('CONFIRMED_CONFLICT_NOTICE',cf,'The referenced source sessions overlap in the same evidenced cohort and occurrence scope.')
        lf=next(f for f in facts if f['type']=='long_scheduled_blocks')
        if lf['status']=='COMPUTED' and lf['value']:recommend('LONG_BLOCK_NOTICE',lf,'An occupied block reaches the explicitly requested development duration threshold.')
    # Separate from registered external P1-A: fixed development buffer/grid/duration.
    truncated=False
    if free_ok:
        slot=SETTINGS['slot_minutes']; grid=SETTINGS['grid_minutes']; buffer=SETTINGS['buffer_minutes']; cap=SETTINGS['recommendation_cap']
        buffered=union((s-timedelta(minutes=buffer),e+timedelta(minutes=buffer)) for s,e in intervals)
        for start,end in free:
            local_start=start.astimezone(zone)
            midnight=_local(local_start.date(),'00:00',zone).astimezone(timezone.utc)
            steps=int((start-midnight).total_seconds()//(60*grid))
            cursor=midnight+timedelta(minutes=grid*steps)
            if cursor<start:cursor+=timedelta(minutes=grid)
            while cursor+timedelta(minutes=slot)<=end and len(recs)<cap:
                stop=cursor+timedelta(minutes=slot)
                if not any(max(cursor,a)<min(stop,b) for a,b in buffered):
                    recommend('OPTIONAL_STUDY_WINDOW',free_fact,'Optional 60-minute study/project window within explicit availability, outside all attested classes and the development 15-minute buffer.',window(cursor,stop))
                    cursor=stop
                else:cursor+=timedelta(minutes=grid)
            if cursor+timedelta(minutes=slot)<=end: truncated=True
    return {'version':VERSION, 'research_status':'PROVISIONAL_APPLICATION_ONLY', 'registered_c1_ready':False,
            'duration_basis':'UTC_ELAPSED_UNION_WITHIN_EXPLICIT_SCOPE',
            'category_breakdown_note':'Category unions may overlap; do not add them to obtain total workload.',
            'registered_p1a_status':'NOT_EXECUTED', 'registered_blockers':list(REGISTERED_BLOCKERS),
            'event_set_id':request.event_set_id, 'event_set_digest':binding(request),
            'input_digest':digest(request.model_dump(mode='json')), 'configuration_digest':digest(SETTINGS),
            'scope':request.scope.model_dump(mode='json'), 'completeness':{'label':label, 'declared_kind':request.declared_kind,
                'effective_kind':'INDEPENDENTLY_ATTESTED_COMPLETE' if complete else request.declared_kind,
                'trusted_complete':complete, 'reason_codes':sorted(set(blockers)), 'synthetic':request.declared_kind=='SYNTHETIC_DEMO',
                'proof':authority.proof(request) if complete else None},
            'facts':facts, 'recommendations':recs, 'recommendation_status':'DEVELOPMENT_ONLY' if complete else 'BLOCKED_INCOMPLETE_SCHEDULE',
            'window_status':('OUTPUT_CAP_REACHED' if truncated else 'WINDOWS_AVAILABLE' if any(r['type']=='OPTIONAL_STUDY_WINDOW' for r in recs)
                             else 'NO_FEASIBLE_WINDOW' if free_ok else 'NOT_ASSESSED'),
            'diagnostics':issues, 'external_calls':False, 'calendar_operations':False}
