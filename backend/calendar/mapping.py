"""Finite evidence-backed occurrences; no timezone, term or holiday defaults."""
from datetime import datetime,date,time,timedelta,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

from backend.models.event import FieldState
from backend.calendar.models import CalendarError,CalendarPayload,MappingEvidence,Occurrence
from backend.reconstruction.models import digest


def _supported(ids,bundle,texts=()):
    spans={s.evidence_id:s for s in bundle.packet.evidence}
    if not ids or any(i not in spans for i in ids): return False
    text=' '.join(spans[i].raw_text for i in ids)
    return all(str(t) in text for t in texts)


def _local(day,label,zone,extra_days=0):
    hours,minutes=map(int,label.split(':'))
    naive=datetime.combine(day,time(0))+timedelta(days=extra_days,hours=hours,minutes=minutes)
    candidates=[]
    for fold in (0,1):
        value=naive.replace(tzinfo=zone,fold=fold)
        if value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)==naive:
            candidates.append(value)
    if not candidates: raise CalendarError('NONEXISTENT_SOURCE_LOCAL_TIME')
    if len({v.utcoffset() for v in candidates})>1: raise CalendarError('AMBIGUOUS_SOURCE_LOCAL_TIME')
    return candidates[0]


def map_event(bundle,event):
    c=event.revision.content; obs={o.field_name:o for o in event.revision.observations}
    if event.identity.status!='CONFIRMED' or not event.identity.stable_key: raise CalendarError('MISSING_STABLE_KEY')
    if not event.revision.core_fields_present: raise CalendarError('MISSING_CORE_FIELDS')
    if not any(v and v.strip() for v in (c.course_code,c.course_name)): raise CalendarError('MISSING_COURSE_IDENTITY')
    if c.source_class_status and c.source_class_status.casefold() not in {'normal','scheduled'}:
        raise CalendarError('SOURCE_STATUS_REVIEW_REQUIRED')
    for field,o in obs.items():
        if o.state in {FieldState.UNRESOLVED,FieldState.CONFLICTING} or len(o.alternatives)>1:
            raise CalendarError('UNRESOLVED_FIELD:'+field)
        if o.state==FieldState.PRESENT and not _supported(o.evidence_ids,bundle): raise CalendarError('MISSING_FIELD_EVIDENCE')
    occurrences,excluded=normalize_occurrences(bundle,event)
    term=bundle.context.term
    recurrence=c.recurrence
    title=' · '.join(v for v in (c.course_code,c.course_name,c.session_type,c.section) if v)
    fields=[('title',('course_code','course_name','session_type','section'),'join evidenced display fields'),
            ('identity',(), 'M1 immutable family/timetable/session stable key'),
            ('occurrences',('date','day_of_week','start_time','end_time','end_day_offset','recurrence','teaching_weeks','excluded_weeks'),'explicit source dates or authority week mapping'),
            ('source_timezone',('source_timezone',),'authoritative timezone; reject ambiguous/nonexistent local times'),
            ('session_metadata',('course_code','course_name','session_type','section','cohort_id','lecturer','location_detail','source_class_status'),'copy source-backed metadata'),
            ('location',('location',),'preserve source value; no room inference')]
    evidence=[]
    for target,names,rule in fields:
        refs={i for f in names for i in obs[f].evidence_ids}
        if target=='identity': refs.update(event.identity.evidence_ids)
        if target=='source_timezone': refs.update(bundle.context.timezone_evidence_ids)
        if target=='occurrences':
            if term: refs.update(term.evidence_ids)
            refs.update(bundle.context.exception_evidence_ids)
            refs.update(i for w in bundle.context.weeks for i in w.evidence_ids)
        evidence.append(MappingEvidence(calendar_field=target,source_fields=names,evidence_ids=tuple(sorted(refs)),transformation=rule))
    return CalendarPayload(stable_key=event.identity.stable_key,source_family_id=event.identity.source_family_id,
        timetable_id=event.identity.timetable_id,source_session_id=event.identity.stable_source_session_id,
        candidate_id=event.candidate_id,revision_id=event.revision.revision_id,revision_digest=digest(event.revision.model_dump(mode='json')),
        source_document_id=bundle.packet.source.source_document_id,source_content_sha256=bundle.packet.source.content_sha256,
        title=title,source_timezone=c.source_timezone,location=c.location,course_code=c.course_code,course_name=c.course_name,
        session_type=c.session_type,section=c.section,cohort_id=c.cohort_id,recurrence=recurrence,
        lecturer=c.lecturer,location_detail=c.location_detail,source_class_status=c.source_class_status,
        teaching_weeks=c.teaching_weeks,excluded_weeks=c.excluded_weeks,occurrences=tuple(occurrences),excluded_dates=tuple(sorted(excluded)),mapping_evidence=tuple(evidence))


def normalize_occurrences(bundle,event):
    """Shared finite temporal mapping. Does not confer identity or verification.

    Callers must validate field states/evidence and source status separately.
    Calendar map_event retains its original stricter gates.
    """
    c=event.revision.content
    obs={o.field_name:o for o in event.revision.observations}
    if not c.source_timezone or not _supported(bundle.context.timezone_evidence_ids,bundle,(c.source_timezone,)):
        raise CalendarError('TIMEZONE_UNRESOLVED')
    if not all(next(s for s in bundle.packet.evidence if s.evidence_id==i).route=='authority' for i in bundle.context.timezone_evidence_ids):
        raise CalendarError('TIMEZONE_AUTHORITY_UNCONFIRMED')
    try: zone=ZoneInfo(c.source_timezone)
    except ZoneInfoNotFoundError: raise CalendarError('TIMEZONE_RUNTIME_UNAVAILABLE') from None
    if c.date and c.day_of_week and c.date.isoweekday()!=c.day_of_week: raise CalendarError('DATE_WEEKDAY_CONFLICT')
    if c.end_day_offset and not _supported(obs['end_day_offset'].evidence_ids,bundle): raise CalendarError('OVERNIGHT_UNSUPPORTED')
    if c.excluded_weeks and (not c.teaching_weeks or not set(c.excluded_weeks)<=set(c.teaching_weeks)):
        raise CalendarError('EXCLUDED_WEEK_SCOPE_INVALID')
    recurrence=c.recurrence; term=bundle.context.term; excluded=set()
    dates=set()
    if recurrence and recurrence.frequency=='WEEKLY':
        if not term or not _supported(term.evidence_ids,bundle,(term.start.isoformat(),term.end.isoformat())):
            raise CalendarError('TERM_AUTHORITY_UNRESOLVED')
        if not all(next(s for s in bundle.packet.evidence if s.evidence_id==i).route=='authority' for i in term.evidence_ids):
            raise CalendarError('TERM_AUTHORITY_UNCONFIRMED')
        if not recurrence.term_start or not recurrence.term_end or not term.start<=recurrence.term_start<=recurrence.term_end<=term.end:
            raise CalendarError('RECURRENCE_OUT_OF_TERM')
        if (recurrence.term_end-recurrence.term_start).days>3660: raise CalendarError('RECURRENCE_LIMIT')
        if not c.day_of_week: raise CalendarError('RECURRENCE_DAY_UNRESOLVED')
        if recurrence.by_day and set(recurrence.by_day)!={c.day_of_week}: raise CalendarError('RECURRENCE_DAY_CONFLICT')
        if recurrence.interval not in (None,1): raise CalendarError('RECURRENCE_INTERVAL_UNSUPPORTED')
        if recurrence.interval is None and (recurrence.source_rule or '').lower() not in {'weekly','every week','each week'}:
            raise CalendarError('RECURRENCE_INTERVAL_UNRESOLVED')
        if c.teaching_weeks:
            weeks={w.week:w for w in bundle.context.weeks}
            if len(weeks)!=len(bundle.context.weeks): raise CalendarError('DUPLICATE_WEEK_MAPPING')
            for number in c.teaching_weeks:
                week=weeks.get(number)
                if not week or not _supported(week.evidence_ids,bundle,(week.start.isoformat(),week.end.isoformat())):
                    raise CalendarError('WEEK_MAPPING_UNRESOLVED')
                day=week.start+timedelta(days=(c.day_of_week-week.start.isoweekday())%7)
                if not week.start<=day<=week.end or not recurrence.term_start<=day<=recurrence.term_end:
                    raise CalendarError('WEEK_MAPPING_OUT_OF_TERM')
                if number in (c.excluded_weeks or ()): excluded.add(day)
                else: dates.add(day)
        else:
            if not c.date: raise CalendarError('RECURRENCE_START_UNRESOLVED')
            day=c.date
            while day<=recurrence.term_end:
                if day<recurrence.term_start: raise CalendarError('RECURRENCE_START_OUT_OF_TERM')
                dates.add(day); day+=timedelta(days=7)
        extras=tuple(recurrence.included_dates or ())+tuple(recurrence.excluded_dates or ())+tuple(recurrence.replacement_dates or ())
        if extras and not _supported(bundle.context.exception_evidence_ids,bundle,(d.isoformat() for d in extras)):
            raise CalendarError('UNSUPPORTED_EXCEPTION')
        if any(not recurrence.term_start<=d<=recurrence.term_end for d in extras): raise CalendarError('EXCEPTION_OUT_OF_TERM')
        dates.update(recurrence.included_dates or ()); dates.update(recurrence.replacement_dates or ())
        excluded.update(recurrence.excluded_dates or ())
        dates-=excluded
        if recurrence.until:
            if not recurrence.term_start<=recurrence.until<=recurrence.term_end: raise CalendarError('UNTIL_OUT_OF_TERM')
            dates={d for d in dates if d<=recurrence.until}
        if recurrence.count:
            if recurrence.count>len(dates): raise CalendarError('RECURRENCE_COUNT_UNSUPPORTED_BY_TERM')
            dates=set(sorted(dates)[:recurrence.count])
    else:
        if c.teaching_weeks or c.excluded_weeks: raise CalendarError('WEEKS_WITHOUT_SUPPORTED_RECURRENCE')
        if not c.date: raise CalendarError('DATE_UNRESOLVED')
        if recurrence and (recurrence.included_dates or recurrence.excluded_dates or recurrence.replacement_dates):
            raise CalendarError('ONE_OFF_EXCEPTION_UNSUPPORTED')
        dates.add(c.date)
    if not dates or len(dates)>512: raise CalendarError('NO_OCCURRENCES_OR_LIMIT')
    occurrences=[]
    for day in sorted(dates):
        start=_local(day,c.start_time,zone)
        end=_local(day,c.end_time,zone,c.end_day_offset or 0)
        if start.astimezone(timezone.utc)>=end.astimezone(timezone.utc): raise CalendarError('INVALID_TEMPORAL_ORDER')
        occurrences.append(Occurrence(source_local_date=day,start=start,end=end))
    return occurrences,excluded
