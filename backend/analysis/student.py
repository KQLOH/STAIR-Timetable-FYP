"""Student-facing deterministic facts; printed slots never establish free time."""
from collections import defaultdict
import json
from backend.models.event import EventContent
from datetime import datetime, timedelta
from backend.analysis.service import analyze
from backend.reconstruction.models import digest


def printed_facts(request):
    """Summarize supported printed slots without asserting schedule completeness."""
    rows=[];excluded=[];duplicates=[];seen={};outside=[]
    scope=getattr(request,'scope',None)
    start=getattr(scope or request,'start',None);end=getattr(scope or request,'end',None)
    for event in request.packet.events:
        c=event.revision.content;observations={o.field_name:o for o in event.revision.observations}
        def supported(name):
            o=observations.get(name)
            value=getattr(c,name)
            if value is None or o is None or o.state!='PRESENT':return False
            for alternative in o.alternatives:
                if alternative.normalized_value is None:return False
                # M4 retains JSON-encoded values; source adapters may retain
                # plain normalized strings. Both must match the selected value.
                candidates=[alternative.normalized_value]
                try:candidates.append(json.loads(alternative.normalized_value))
                except (ValueError,TypeError):pass
                consistent=False
                for candidate in candidates:
                    try:consistent=getattr(EventContent.model_validate({name:candidate}),name)==value
                    except ValueError:continue
                    if consistent:break
                if not consistent:return False
            return True
        identity=c.course_code if supported('course_code') else c.course_name if supported('course_name') else None
        dated=c.date if supported('date') else None
        weekday=c.day_of_week if supported('day_of_week') else None
        if event.extraction_status in {'CONFLICTING','UNAVAILABLE'} or not identity or not (dated or weekday) or not supported('start_time') or not supported('end_time') or (dated and weekday and dated.isoweekday()!=weekday):
            excluded.append(event.candidate_id);continue
        left=int(c.start_time[:2])*60+int(c.start_time[3:]);right=int(c.end_time[:2])*60+int(c.end_time[3:])+1440*(c.end_day_offset or 0)
        if not 0<right-left<=1440:excluded.append(event.candidate_id);continue
        if dated and ((start and dated<start) or (end and dated>end)):
            outside.append(event.candidate_id);continue
        label=dated.isoformat() if dated else ['','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][weekday]
        key=digest({'content':c.model_dump(mode='json')})
        if key in seen:
            duplicates.append([seen[key],event.candidate_id]);continue
        seen[key]=event.candidate_id
        rows.append({'candidate_id':event.candidate_id,'revision_id':str(event.revision.revision_id),'day':label,'course':identity,
            'start':c.start_time,'end':c.end_time,'end_day_offset':c.end_day_offset,'duration_hours':(right-left)/60,
            'uncertain_details':event.extraction_status=='AMBIGUOUS','location':c.location if supported('location') else None,'lecturer':c.lecturer if supported('lecturer') else None,'human_corrected':bool(event.human_review and event.human_review.status=='CORRECTED'),
            'date':dated.isoformat() if dated else None,'weekday':weekday,'timezone':c.source_timezone if supported('source_timezone') else None,
            '_left':left,'_right':right,'_day':dated.toordinal() if dated else weekday-1})
    daily=defaultdict(float)
    for row in rows:
        left,right=row['_left'],row['_right']
        daily[row['day']]+=(min(right,1440)-left)/60
        if right>1440:
            next_day=(datetime.fromisoformat(row['date']).date()+timedelta(days=1)).isoformat() if row['date'] else ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][(row['weekday'])%7]
            daily[next_day]+=(right-1440)/60
    overlaps=[]
    for i,a in enumerate(rows):
        for b in rows[:i]:
            if bool(a['date'])!=bool(b['date']):continue
            al=a['_day']*1440+a['_left'];ar=a['_day']*1440+a['_right']
            bl=b['_day']*1440+b['_left'];br=b['_day']*1440+b['_right']
            if any(al<br+shift and bl+shift<ar for shift in ((0,) if a['date'] else (-10080,0,10080))):
                overlaps.append({'classes':[a['course'],b['course']],'day':a['day'],'status':'POSSIBLE_OVERLAP_NOT_VERIFIED'})
    dates=sorted(r['date'] for r in rows if r['date'])
    zones={r['timezone'] for r in rows}
    timezone=next(iter(zones)) if len(zones)==1 and None not in zones else None
    if timezone:
        from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
        try:ZoneInfo(timezone)
        except (ZoneInfoNotFoundError,ValueError):timezone=None
    for row in rows:
        for key in ('_left','_right','_day'):row.pop(key)
    return {'basis':'PRINTED_LOCAL_SLOTS_NOT_COMPLETE_OR_ELAPSED_TIME','class_count':len(rows),'printed_class_hours':sum(r['duration_hours'] for r in rows) if rows else None,
        'hours_by_day':dict(sorted(daily.items())),'busiest_observed_days':[d for d,h in daily.items() if h==max(daily.values())],
        'lightest_observed_active_days':[d for d,h in daily.items() if h==min(daily.values())], 'occupied_local_slots':rows,
        'possible_duplicates':duplicates,'possible_overlaps':overlaps,'excluded_unresolved_candidates':excluded,'outside_range_candidates':outside,
        'detected_date_range':{'start':dates[0],'end':dates[-1]} if dates else None,'source_timezone':timezone,
        'uncertain_details_class_count':sum(r['uncertain_details'] for r in rows),'undated_class_count':sum(not r['date'] for r in rows),'human_corrected_class_count':sum(r['human_corrected'] for r in rows),
        'limitations':['No recurrence or semester expansion was inferred. Printed dated slots start inside any requested dates; weekday-only slots are not assigned to dates.','Unknown timezone/DST can change elapsed hours.','Missing source classes can change these totals; exact duplicate readings are counted once.','Unlisted days are not established as free.']}


def observed_analyze(request):
    # This route has no authority dependency and cannot consume attestations.
    facts=printed_facts(request)
    return {'version':'fr7-observed-v1','student_facts':facts,'trusted_complete':False,'calendar_authorized':False,
        'recommendations':[],'recommendation_status':'COMPLETENESS_REQUIRED',
        'input_candidates':len(request.packet.events),'source_issues':len(request.packet.issues)}


def student_analyze(request, authority=None):
    result=analyze(request,authority)
    result['student_facts']=printed_facts(request)
    # Contact hours sum distinct normalized sessions; occupied hours use the
    # existing union. Overlapping classes must not collapse into one contact hour.
    occurrences=next(f['value'] for f in result['facts'] if f['type']=='normalized_occurrences')
    daily=defaultdict(float);weekly=defaultdict(float)
    for occurrence in occurrences:
        left=datetime.fromisoformat(occurrence['start']);right=datetime.fromisoformat(occurrence['end'])
        while left<right:
            from backend.calendar.mapping import _local
            from zoneinfo import ZoneInfo
            zone=ZoneInfo(request.scope.timezone)
            end=min(right,_local(left.astimezone(zone).date()+timedelta(days=1),'00:00',zone))
            hours=(end.timestamp()-left.timestamp())/3600
            day=left.astimezone(zone).date();daily[day.isoformat()]+=hours
            weekly[(day-timedelta(days=day.weekday())).isoformat()]+=hours
            left=end
    complete=result['completeness']['trusted_complete']
    if complete:
        day=request.scope.start
        while day<=request.scope.end:
            daily.setdefault(day.isoformat(),0.0)
            weekly.setdefault((day-timedelta(days=day.weekday())).isoformat(),0.0)
            day+=timedelta(days=1)
    result['verified_workload']={'status':'VERIFIED_COMPLETE_SCOPE' if complete else 'OBSERVED_NORMALIZED_ONLY',
        'class_contact_hours':sum(daily.values()) if occurrences or complete else None,
        'contact_hours_by_day':dict(sorted(daily.items())), 'contact_hours_by_calendar_week':dict(sorted(weekly.items())),
        'busiest_days':[d for d,h in daily.items() if h==max(daily.values())] if daily else [],
        'lightest_days':[d for d,h in daily.items() if h==min(daily.values())] if daily else [],
        'week_basis':'Calendar week commencing Monday; not an inferred teaching-week number.'}
    result['student_profile']='fr7-student-v1'
    return result
