"""Source-backed FR7 context projections. Original predictions remain immutable."""
from datetime import date,datetime
import re
from uuid import uuid5
from pydantic import Field,model_validator
from backend.models.evidence import ContractModel,EvidenceSpan
from backend.models.event import EventContent,RecurrenceRule,FieldObservation,CanonicalEvent
from backend.analysis.models import Scope
from backend.calendar.models import CalendarContext,WeekAuthority
from backend.reconstruction.models import TermAuthority,digest
from backend.review.models import ReviewError


class SourceReading(ContractModel):
    page:int=Field(ge=1,le=100)
    evidence_ids:tuple[str,...]=Field(min_length=1,max_length=30)
    raw_reading:str=Field(min_length=1,max_length=6000)
    note:str=Field(min_length=1,max_length=2000)
    source_confirmed:bool


class DateRange(ContractModel):
    start:date
    end:date
    @model_validator(mode='after')
    def ordered(self):
        if not 0<=(self.end-self.start).days<=366:raise ValueError('Explicit bounded source term required')
        return self


class TeachingWeek(DateRange):
    week:int=Field(ge=1,le=512)
    reading:SourceReading
    @model_validator(mode='after')
    def one_week(self):
        if (self.end-self.start).days>6:raise ValueError('A week spans at most seven dates')
        return self


class EventTemporal(ContractModel):
    candidate_id:str=Field(min_length=1,max_length=200)
    recurrence:RecurrenceRule|None=None
    teaching_weeks:tuple[int,...]|None=Field(default=None,max_length=100)
    excluded_weeks:tuple[int,...]|None=Field(default=None,max_length=100)
    section:str|None=Field(default=None,max_length=100)
    reading:SourceReading|None=None


class TemporalSubmission(ContractModel):
    expected_version:int=Field(ge=1)
    scope:Scope
    timezone:str|None=Field(default=None,max_length=100)
    term:DateRange|None=None
    cohort_id:str|None=Field(default=None,max_length=100)
    weeks:tuple[TeachingWeek,...]=Field(default=(),max_length=100)
    events:tuple[EventTemporal,...]=Field(default=(),max_length=100)
    reading:SourceReading


def _contains(raw,value):
    if isinstance(value,date):
        return any(v in raw for v in (value.isoformat(),value.strftime('%d/%m/%Y'),value.strftime('%d-%m-%Y'),value.strftime('%d %B %Y')))
    if isinstance(value,int):
        return bool(re.search(r'(?<!\d)'+str(value)+r'(?!\d)',raw)) or any(int(a)<=value<=int(b) for a,b in re.findall(r'(?<!\d)(\d+)\s*-\s*(\d+)(?!\d)',raw))
    return str(value).casefold() in raw.casefold()


def _reading(reading,packet,manifest,values=()):
    spans={s.evidence_id:s for s in packet.evidence}
    if not reading.source_confirmed or not reading.raw_reading.strip() or not reading.note.strip() or reading.page>manifest['page_count'] or any(ref not in spans or (spans[ref].page or 1)!=reading.page for ref in reading.evidence_ids):
        raise ReviewError('TEMPORAL_SOURCE_READING_REQUIRED',422)
    if not all(_contains(reading.raw_reading,v) for v in values):raise ReviewError('TEMPORAL_VALUE_READING_MISMATCH',422)


def validate(body,packet,manifest):
    _reading(body.reading,packet,manifest,[v for v in (body.timezone,body.cohort_id) if v])
    if body.timezone and body.timezone!=body.scope.timezone:raise ReviewError('TEMPORAL_TIMEZONE_SCOPE_MISMATCH',422)
    if body.cohort_id and body.cohort_id!=body.scope.cohort_id:raise ReviewError('TEMPORAL_GROUP_SCOPE_MISMATCH',422)
    if body.term:
        _reading(body.reading,packet,manifest,(body.term.start,body.term.end))
        if not body.term.start<=body.scope.start<=body.scope.end<=body.term.end:raise ReviewError('TEMPORAL_SCOPE_OUTSIDE_SOURCE_TERM',422)
    elif body.weeks:raise ReviewError('TEMPORAL_TERM_REQUIRED_FOR_WEEKS',422)
    else:_reading(body.reading,packet,manifest,(body.scope.start,body.scope.end))
    numbers=set();ranges=[]
    for week in body.weeks:
        _reading(week.reading,packet,manifest,(week.week,week.start,week.end))
        if week.week in numbers or not body.term.start<=week.start<=week.end<=body.term.end or any(max(week.start,a)<=min(week.end,b) for a,b in ranges):raise ReviewError('TEMPORAL_WEEK_MAPPING_INVALID',422)
        numbers.add(week.week);ranges.append((week.start,week.end))
    known={e.candidate_id:e for e in packet.events};seen=set()
    for item in body.events:
        if item.candidate_id not in known or item.candidate_id in seen:raise ReviewError('TEMPORAL_EVENT_SCOPE_MISMATCH',422)
        seen.add(item.candidate_id);reading=item.reading or body.reading;values=[]
        if item.section:values.append(item.section)
        for weeks in (item.teaching_weeks,item.excluded_weeks):
            if weeks is not None:
                if len(weeks)!=len(set(weeks)) or any(w<1 for w in weeks):raise ReviewError('TEMPORAL_WEEK_MAPPING_INVALID',422)
                if weeks==() and not re.search(r'\b(none|no exclusions|no teaching weeks)\b',reading.raw_reading,re.I):raise ReviewError('EXPLICIT_EMPTY_WEEK_READING_REQUIRED',422)
                values.extend(weeks)
        if item.recurrence:
            rec=item.recurrence
            if rec.frequency=='WEEKLY' and not re.search(r'\b(weekly|every week|each week)\b',reading.raw_reading,re.I):raise ReviewError('SOURCE_WEEKLY_RULE_REQUIRED',422)
            if rec.frequency=='ONCE' and not re.search(r'\b(once|single|one.off)\b',reading.raw_reading,re.I):raise ReviewError('SOURCE_ONE_OFF_RULE_REQUIRED',422)
            values.extend((rec.included_dates or ())+(rec.excluded_dates or ())+(rec.replacement_dates or ()))
            if rec.until:values.append(rec.until)
            if rec.count:values.append(rec.count)
            if rec.frequency=='WEEKLY' and (not body.term or rec.term_start!=body.term.start or rec.term_end!=body.term.end):raise ReviewError('TEMPORAL_RECURRENCE_TERM_MISMATCH',422)
        _reading(reading,packet,manifest,values)
    # Existing values are never silently replaced. Field repair stays in review.
    for event in packet.events:
        item=next((i for i in body.events if i.candidate_id==event.candidate_id),None)
        changes={k:v for k,v in {'source_timezone':body.timezone,'cohort_id':body.cohort_id,**(item.model_dump(exclude={'candidate_id','reading'}) if item else {})}.items() if v is not None}
        current=event.revision.content.model_dump(mode='json')
        for name,value in changes.items():
            normalized=EventContent.model_validate({name:value}).model_dump(mode='json')[name]
            if current[name] is not None and current[name]!=normalized:
                if name!='recurrence' or any(v is not None and normalized.get(k)!=v for k,v in current[name].items()):raise ReviewError('USE_FIELD_REVIEW_TO_CHANGE_EXISTING_CONTEXT',409)


def project(check,packet):
    """Analysis-only descendants; no review, Gold, ACCEPT or calendar mutation."""
    record=check.get('temporal')
    if not record:return packet,CalendarContext()
    body=TemporalSubmission.model_validate(record['submission']);context_digest=digest(record);spans=list(packet.evidence)
    def span(reading,label,values):
        eid='fr7-context:'+digest({'context':context_digest,'label':label})
        spans.append(EvidenceSpan(evidence_id=eid,source_document_id=packet.source.source_document_id,route='authority',page=reading.page,
            raw_text=reading.raw_reading+'\nExplicit normalized human source assertion: '+str(values),
            transformations=('fr7-source-context-not-gold',*('source-ref:'+ref for ref in reading.evidence_ids))))
        return eid
    main=span(body.reading,'scope',body.model_dump(mode='json',exclude={'events','weeks'}))
    week_authority=[]
    for week in body.weeks:
        eid=span(week.reading,'week:'+str(week.week),week.model_dump(mode='json',exclude={'reading'}))
        week_authority.append(WeekAuthority(week=week.week,start=week.start,end=week.end,evidence_ids=(eid,)))
    events=[];exceptions=[]
    for event in packet.events:
        item=next((i for i in body.events if i.candidate_id==event.candidate_id),None)
        reading=item.reading if item and item.reading else body.reading
        changes={k:v for k,v in {'source_timezone':body.timezone,'cohort_id':body.cohort_id,**(item.model_dump(exclude={'candidate_id','reading'}) if item else {})}.items() if v is not None}
        if not changes:events.append(event);continue
        eid=span(reading,'event:'+event.candidate_id,changes);exceptions.append(eid)
        content=EventContent.model_validate({**event.revision.content.model_dump(mode='json'),**changes})
        observations=tuple(FieldObservation(field_name=o.field_name,state='PRESENT',raw_value=reading.raw_reading,origin='authority',evidence_ids=(eid,),transformations=('fr7-analysis-context-only',)) if o.field_name in changes else o for o in event.revision.observations)
        revision=event.revision.model_copy(update={'revision_id':uuid5(event.revision.revision_id,context_digest),'version':event.revision.version+1,'previous_revision_id':event.revision.revision_id,'method_id':'fr7-source-context-projection','configuration_digest':context_digest,'content':content,'observations':observations})
        events.append(CanonicalEvent(candidate_id=event.candidate_id,identity=event.identity,revision=revision,extraction_status=event.extraction_status))
    context=CalendarContext(timezone_evidence_ids=(main,) if body.timezone else (),term=TermAuthority(start=body.term.start,end=body.term.end,authority_record_id=record['record_id'],evidence_ids=(main,)) if body.term else None,weeks=tuple(week_authority),exception_evidence_ids=tuple(exceptions))
    return packet.model_copy(update={'events':tuple(events),'evidence':tuple(spans)}),context
