"""Opt-in S2b colored-block application graph; no registered-method mutation.

Supports axis-aligned day/date rows and two-row clock headers. Pixel components
propose ownership; they are not events. Missing/competing context stays explicit.
"""
from collections import defaultdict
from datetime import date
import json
import re

from backend.models.evidence import BoundingBox, EvidenceSpan
from backend.models.event import CanonicalTimetable, CanonicalEvent, FieldState
from backend.reconstruction.models import (digest, development_settings, Context,
    FieldNode, EventGroup, Finding)
from backend.reconstruction.candidates import (build_graph, _extract, _node, FIELDS,
    DAY_RE, DATE_RE, TIME_RE, subject_matches)
from backend.reconstruction.decoder import decode
from backend.reconstruction.adapter import canonical_event
from backend.services.normalizer import normalize_time, normalize_class_type
from backend.review.binding import BoundRun, recover_selection
from backend.review.policy import assess_event
from backend.layout.service import relations
from backend.layout.block_models import PROFILE, BlockEvidence, MAX_OPERATIONS
from backend.layout.block_capture import DETECTION_CONFIG

CONFIG={'profile_id':PROFILE,'version':'s2b.1','status':'APPLICATION_ONLY_SYNTHETIC_DEVELOPMENT',
    'family':'axis-aligned-colored-blocks-day-date-rows-two-clock-rows',
    'detection':DETECTION_CONFIG,'max_groups':40,'max_operations':MAX_OPERATIONS,
    'temporal_authority':'own explicit AM/PM range, explicit header AM/PM, or printed 24-hour statement',
    'no_implicit_axis_rollover':True,'no_unseen_region_split':True}
CONFIG_HASH=digest(CONFIG)
CLOCK=re.compile(r'^(\d{1,2}):(\d{2})\s*([AP]M)?$',re.I)
CLOCK_SHAPE=re.compile(r'^(?:\d{1,2}|\?{2})[:.](?:\d{2}|\?{2})\s*(?:[AP]M)?$',re.I)
MARKER=re.compile(r'\b([A-Z]{2,}\s*\d{3,4}[A-Z]?)\s*[\(\[]\s*([LPT])\s*[\)\]}]',re.I)
TYPE_MARKER=re.compile(r'[\(\[]\s*([LPT])\s*[\)\]}]',re.I)
ROLE_LABEL=re.compile(r'\b(?:room|venue|location|lecturer|instructor|teacher|staff|group|section|cohort)\b',re.I)


def _box(words):
    return BoundingBox(x0=min(w.bbox.x0 for w in words),y0=min(w.bbox.y0 for w in words),
        x1=max(w.bbox.x1 for w in words),y1=max(w.bbox.y1 for w in words),coordinate_space='PIXELS')


def _span(packet,page,words,kind,box=None):
    words=[w for line in _lines(words) for w in line];text=' '.join(w.text.strip() for w in words)
    key=digest({'source':packet.source.content_sha256,'page':page.page,'kind':kind,
                'words':[w.evidence_id for w in words]})
    return EvidenceSpan(evidence_id='s2b-derived-'+key,source_document_id=packet.source.source_document_id,
        route='ocr',page=page.page,raw_text=text,bbox=box or _box(words),confidence=None,
        locator=kind,transformations=(PROFILE,'derived-original-coordinate-word-join',
                                     'source-words:'+','.join(w.evidence_id for w in words)))


def validate_binding(packet,supplement):
    if packet.source.route!='ocr' or (packet.source.source_document_id,packet.source.content_sha256)!=(supplement.source_document_id,supplement.content_sha256):
        raise ValueError('S2B_SOURCE_MISMATCH')
    spans={s.evidence_id:s for s in packet.evidence}
    if {p.page for p in supplement.pages}!={s.page for s in packet.evidence}:raise ValueError('S2B_PAGE_MISMATCH')
    for p in supplement.pages:
        regions={r.region_id:r for r in p.regions}
        for w in p.words:
            s=spans.get(w.parent_line_id);b=w.bbox
            if not s or s.page!=p.page or s.route!='ocr' or not s.bbox or s.bbox.coordinate_space!='PIXELS':
                raise ValueError('S2B_PARENT_MISMATCH')
            a=s.bbox
            if not (a.x0<=b.x0<b.x1<=a.x1 and a.y0<=b.y0<b.y1<=a.y1 and w.text.strip() in s.raw_text):
                raise ValueError('S2B_WORD_PARENT_OWNERSHIP')
            seed={'source':packet.source.source_document_id,'page':p.page,'parent':w.parent_line_id,
                  'text':w.text,'box':[b.x0,b.y0,b.x1,b.y1],'region':w.region_id}
            if w.evidence_id!='s2b-word-'+digest(seed):raise ValueError('S2B_WORD_ID_MISMATCH')
            if w.region_id:
                a=regions[w.region_id].bbox
                if not (a.x0<=b.x0<b.x1<=a.x1 and a.y0<=b.y0<b.y1<=a.y1):raise ValueError('S2B_CROP_OWNERSHIP')


def word_spans(packet,supplement):
    return [EvidenceSpan(evidence_id=w.evidence_id,source_document_id=packet.source.source_document_id,
        route='ocr',page=p.page,raw_text=w.text,bbox=w.bbox,confidence=w.confidence,
        locator=f'word:{w.evidence_id};parent:{w.parent_line_id};region:{w.region_id or "global"}',
        transformations=(PROFILE,'recognized-word-not-verified-source',f'adapter:{w.capture_route}',
                         'source-transform:'+json.dumps(w.transform,sort_keys=True)))
        for p in supplement.pages for w in p.words]


def _inside(word,region):
    a,b=word.bbox,region.bbox
    return min(a.x1,b.x1)>max(a.x0,b.x0) and min(a.y1,b.y1)>max(a.y0,b.y0)


def header_axis(page):
    """Two actual clock rows; no conversion of clock order into meridiem."""
    outside=[w for w in page.words if w.region_id is None and not any(_inside(w,r) for r in page.regions)]
    preferred=[w for w in outside if w.capture_route=='s2b-axis-crop-ocr-v1']
    clocks=[w for w in (preferred or outside) if CLOCK_SHAPE.fullmatch(w.text.strip())]
    top=min((r.bbox.y0 for r in page.regions),default=page.height)
    clocks=[w for w in clocks if w.bbox.y1<=top]
    bands=[]
    for w in sorted(clocks,key=lambda w:(w.bbox.y0,w.bbox.x0)):
        height=w.bbox.y1-w.bbox.y0
        matches=[b for b in bands if abs(b[0].bbox.y0-w.bbox.y0)<=max(2,height*.6)]
        if len(matches)==1:matches[0].append(w)
        else:bands.append([w])
    bands=[b for b in bands if len(b)>=3]
    if len(bands)!=2:return {'supported':False,'columns':[],'issues':['TWO_CLOCK_ROWS_NOT_ESTABLISHED']},outside
    bands.sort(key=lambda b:b[0].bbox.y0)
    columns=[];used=set();issues=[]
    edges=sorted(set(page.vertical_rules+tuple(v for r in page.regions for v in (r.bbox.x0,r.bbox.x1))))
    for a in sorted(bands[0],key=lambda w:w.bbox.x0):
        tolerance=max(3,(a.bbox.y1-a.bbox.y0)*.8)
        options=[b for b in bands[1] if abs(a.bbox.x0-b.bbox.x0)<=tolerance and b.evidence_id not in used]
        if len(options)!=1:issues.append('AMBIGUOUS_CLOCK_COLUMN_PAIR');continue
        b=options[0];used.add(b.evidence_id)
        lefts=[x for x in edges if x<=min(a.bbox.x0,b.bbox.x0) and min(a.bbox.x0,b.bbox.x0)-x<=tolerance]
        left=max(lefts) if lefts else None
        columns.append({'upper':a,'lower':b,'left':left,'right':None})
    for a,b in zip(columns,columns[1:]):a['right']=b['left']
    # The terminal interval is not extended beyond an unobserved outer boundary.
    return {'supported':len(columns)>=3,'columns':columns,'issues':issues},outside


def _lines(words):
    grouped=defaultdict(list)
    for w in words:grouped[(w.parent_line_id,w.block,w.paragraph,w.line)].append(w)
    return sorted((sorted(ws,key=lambda w:(w.bbox.x0,w.reading_order,w.evidence_id)) for ws in grouped.values()),key=lambda ws:(min(w.bbox.y0 for w in ws),min(w.bbox.x0 for w in ws)))


def _raw_nodes(source,span,words):
    words=[w for line in _lines(words) for w in line]
    return [field_refs(n,span,words) for n in _extract(source,span,0,len(span.raw_text),Context())]


def field_refs(node,span,words):
    ranges=[]
    for unit in node.ownership_units:
        parts=unit.rsplit(':',2)
        if len(parts)==3 and parts[0]==span.evidence_id and parts[1].isdigit() and parts[2].isdigit():
            ranges.append((int(parts[1]),int(parts[2])))
    if not ranges and node.raw:
        ranges=[m.span() for m in re.finditer(re.escape(node.raw),span.raw_text)]
    cursor=0;ids=set(node.evidence_ids)
    for w in words:
        start=cursor;cursor+=len(w.text.strip())+1
        if any(min(cursor-1,b)>max(start,a) for a,b in ranges):ids.add(w.evidence_id)
    return node.model_copy(update={'evidence_ids':tuple(sorted(ids)),
        'transformations':node.transformations+(PROFILE,'exact-source-word-ranges')})


def _field_node(packet,span,words,field,raw,shared=False,start=None):
    start=span.raw_text.find(raw) if start is None else start
    if start<0:raise ValueError('S2B_FIELD_SOURCE_RANGE')
    return field_refs(_node(packet.source,span,field,raw,start,start+len(raw),Context(),shared),span,[w for line in _lines(words) for w in line])


def _unresolved(packet,span,words,field,raw,reason):
    key=digest({'source':packet.source.content_sha256,'span':span.evidence_id,'field':field,'raw':raw,'reason':reason})
    n=FieldNode(candidate_id=key,node_id='s2b-unresolved-'+key,field=field,state=FieldState.UNRESOLVED,
        raw=raw,evidence_ids=(span.evidence_id,),transformations=(PROFILE,reason),
        ownership_units=(span.evidence_id+':'+field,))
    return field_refs(n,span,[w for line in _lines(words) for w in line])


def block_fields(packet,page,region,words):
    span=_span(packet,page,words,'region:'+region.region_id,region.bbox)
    nodes=_raw_nodes(packet.source,span,words);issues=[]
    lines=_lines(words);texts=[' '.join(w.text.strip() for w in ws) for ws in lines]
    line_fields={'location','lecturer','section','cohort_id'}
    nodes=[n for n in nodes if n.field not in line_fields]
    cursor=0
    for text in texts:
        for n in _extract(packet.source,span,cursor,cursor+len(text),Context()):
            if n.field in line_fields:nodes.append(field_refs(n,span,[w for line in lines for w in line]))
        cursor+=len(text)+1
    markers=list(MARKER.finditer(span.raw_text))
    # Parenthesized type establishes a course anchor, excluding code-like venues.
    if markers:
        nodes=[n for n in nodes if n.field not in {'course_code','session_type'}]
        for m in markers:
            nodes.append(_field_node(packet,span,words,'course_code',m[1],start=m.start(1)))
            nodes.append(_field_node(packet,span,words,'session_type',m[2],start=m.start(2)))
    elif not any(n.field=='course_code' for n in nodes):
        issues.append('COURSE_IDENTITY_UNRESOLVED')
        nodes.append(_unresolved(packet,span,words,'course_code',texts[0] if texts else span.raw_text,'unreadable-course-marker'))
    course_lines=[i for i,t in enumerate(texts) if MARKER.search(t) or (i==0 and subject_matches(t))]
    venue_lines=[]
    for i,text in enumerate(texts):
        if i in course_lines or re.search(r'\d{1,2}:\d{2}|[AP]M\b',text,re.I):continue
        if re.fullmatch(r'(?:[A-Z0-9]+(?:[-_/][A-Z0-9]+)+|[A-Z]{1,5}\d+[A-Z]?|TBA|UNKNOWN)',text):
            venue_lines.append(i)
    if not any(n.field=='location' for n in nodes):
        for i in venue_lines:
            nodes.append(_field_node(packet,span,words,'location',texts[i]))
        if len(venue_lines)>1:issues.append('COMPETING_BARE_VENUE_LINES')
    if len(course_lines)==1:
        i=course_lines[0]+1
        if i<len(lines) and i not in venue_lines and not ROLE_LABEL.search(texts[i]) and not re.search(r'\d{1,2}:\d{2}|[AP]M\b',texts[i],re.I) and not MARKER.search(texts[i]):
            nodes.append(_field_node(packet,span,words,'course_name',texts[i]))
    if not any(n.field=='lecturer' for n in nodes) and len(venue_lines)==1:
        index=venue_lines[0]+1
        if index<len(lines) and index not in course_lines and not re.search(r'\d{1,2}:\d{2}|[AP]M\b|\b(?:group|section|cohort)\b',texts[index],re.I):
            nodes.append(_field_node(packet,span,words,'lecturer',texts[index]))
            nodes[-1]=nodes[-1].model_copy(update={'transformations':nodes[-1].transformations+('s2b-declared-template-line-after-venue',)})
    # Unknown intervals are retained, rather than quietly becoming MISSING.
    if not any(n.field in {'start_time','end_time'} for n in nodes):
        possible=[t for t in texts if re.search(r'\b[AP]M\b|\btime\b|\?\?|unreadable',t,re.I)]
        for text in possible:
            for field in ('start_time','end_time'):nodes.append(_unresolved(packet,span,words,field,text,'unreadable-or-unsupported-block-time'))
    times=[n for n in nodes if n.field in {'start_time','end_time'}]
    if times and any(n.state==FieldState.PRESENT and n.raw and not re.search(r'[AP]\.?M\.?',n.raw,re.I) and
                     int(n.value.split(':')[0])<=12 for n in times):
        nodes=[n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED,
            'transformations':n.transformations+('block-meridiem-unresolved',)})
            if n.field in {'start_time','end_time'} else n for n in nodes]
        issues.append('BLOCK_MERIDIEM_UNRESOLVED')
    return span,nodes,issues


def _day_context(packet,page,region,outside,axis):
    left=min((c['left'] for c in axis['columns'] if c['left'] is not None),default=None)
    if left is None:return None,[],['DAY_COLUMN_BOUNDARY_UNRESOLVED']
    b=region.bbox
    eligible=[w for w in outside if w.bbox.x1<=left and b.y0<=w.bbox.y0 and w.bbox.y1<=b.y1]
    preferred=[w for w in eligible if w.capture_route=='s2b-row-header-crop-ocr-v1']
    if preferred:eligible=preferred
    unique=[]
    for w in sorted(eligible,key=lambda w:w.evidence_id):
        a=w.bbox
        duplicate=False
        for old in unique:
            b=old.bbox
            area=max(0,min(a.x1,b.x1)-max(a.x0,b.x0))*max(0,min(a.y1,b.y1)-max(a.y0,b.y0))
            if old.text.casefold()==w.text.casefold() and area>=.8*min((a.x1-a.x0)*(a.y1-a.y0),(b.x1-b.x0)*(b.y1-b.y0)):
                duplicate=True;break
        if not duplicate:unique.append(w)
    eligible=unique
    days=[w for w in eligible if DAY_RE.fullmatch(w.text.strip())]
    dates=[w for w in eligible if DATE_RE.fullmatch(w.text.strip())]
    selected=(days if len(days)==1 else [])+(dates if len(dates)==1 else [])
    if not selected:return None,[],['DAY_DATE_ROW_UNRESOLVED']
    span=_span(packet,page,selected,'day-date-row')
    nodes=[n.model_copy(update={'shared':True,'transformations':n.transformations+('s2b-same-row-left-header',)})
           for n in _raw_nodes(packet.source,span,selected) if n.field in {'date','day_of_week'}]
    values={n.field:n.value for n in nodes if n.state==FieldState.PRESENT}
    issues=[]
    if values.get('date') and values.get('day_of_week') and date.fromisoformat(values['date']).isoweekday()!=values['day_of_week']:
        nodes=[n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED}) for n in nodes];issues.append('PRINTED_DAY_DATE_CONFLICT')
    return span,nodes,issues


def _geometry_span(region,axis):
    heights=[c['upper'].bbox.y1-c['upper'].bbox.y0 for c in axis['columns']]
    tolerance=max(2,min(heights or [4])*.5)
    starts=[i for i,c in enumerate(axis['columns']) if c['left'] is not None and abs(c['left']-region.bbox.x0)<=tolerance]
    ends=[i for i,c in enumerate(axis['columns']) if c['right'] is not None and abs(c['right']-region.bbox.x1)<=tolerance]
    if len(starts)==len(ends)==1 and starts[0]<=ends[0]:return axis['columns'][starts[0]:ends[0]+1]
    return []


def _clock_compatible(raw,value):
    match=CLOCK.fullmatch(raw.strip())
    if not match or value is None:return None
    h,m=map(int,value.split(':'))
    if match[3]:return normalize_time(raw)==value
    return int(match[2])==m and int(match[1])%12==h%12


def associate_time(packet,page,region,span,words,nodes,axis,outside):
    occupied=_geometry_span(region,axis);derived=[];issues=[]
    if not occupied:return nodes,derived,['BLOCK_HEADER_BOUNDARIES_UNRESOLVED']
    upper,lower=occupied[0]['upper'],occupied[-1]['lower']
    starts=[n for n in nodes if n.field=='start_time' and n.state==FieldState.PRESENT]
    ends=[n for n in nodes if n.field=='end_time' and n.state==FieldState.PRESENT]
    if len(starts)==len(ends)==1:
        compatibility=(_clock_compatible(upper.text,starts[0].value),_clock_compatible(lower.text,ends[0].value))
        if False in compatibility:
            issues.append('PRINTED_TIME_GEOMETRY_CONFLICT')
            nodes=[n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED,'transformations':n.transformations+('s2b-time-content-geometry-conflict',)}) if n.field in {'start_time','end_time'} else n for n in nodes]
        if None in compatibility:issues.append('HEADER_CLOCK_READING_UNRESOLVED_BLOCK_RANGE_RETAINED')
        return nodes,derived,issues
    # No replacing an unreadable/conflicting printed interval with guessed values.
    if any(n.field in {'start_time','end_time'} and n.state==FieldState.UNRESOLVED and
           'block-meridiem-unresolved' not in n.transformations for n in nodes):
        return nodes,derived,['PRINTED_INTERVAL_UNRESOLVED_HEADER_NOT_A_REPAIR']
    authority=[w for w in outside if re.fullmatch(r'24[ -]?HOUR',w.text.strip(),re.I)]
    a,b=CLOCK.fullmatch(upper.text.strip()),CLOCK.fullmatch(lower.text.strip())
    if not a or not b:return nodes,derived,['HEADER_CLOCK_UNREADABLE']
    explicit=bool(a[3] and b[3])
    if not explicit and not authority:return nodes,derived,['AXIS_MERIDIEM_UNRESOLVED']
    pair='time-pair:s2b-'+digest({'region':region.region_id,'upper':upper.evidence_id,'lower':lower.evidence_id})
    for field,word in [('start_time',upper),('end_time',lower)]:
        h=_span(packet,page,[word,*authority],'clock-column-'+field);derived.append(h)
        node=_field_node(packet,h,[word,*authority],field,word.text,shared=True)
        nodes.append(node.model_copy(update={'transformations':node.transformations+(pair,'s2b-printed-axis-authority',)}))
    return nodes,derived,issues


def _group(region,span,nodes,issues,anchor=None):
    domains={}
    for field in FIELDS:
        options={digest({'value':n.value,'state':n.state.value}):n for n in nodes if n.field==field}
        if len(options)>8:raise ValueError('S2B_FIELD_VALUE_CAP')
        missing=FieldNode(candidate_id='~MISSING:'+field,node_id='~MISSING:'+field,field=field,state=FieldState.MISSING)
        domains[field]=tuple(sorted(options.values(),key=lambda n:n.node_id))+(missing,)
    gid='s2b-group-'+digest({'region':region.region_id,'span':span.evidence_id,'anchor':anchor})
    return EventGroup(group_id=gid,evidence_ids=tuple(sorted({i for n in nodes for i in n.evidence_ids})),
        domains=domains,relations=relations(domains),diagnostics=tuple(sorted(set(issues+['S2B_APPLICATION_ONLY']))))


def block_graph(packet,supplement):
    if not isinstance(supplement,BlockEvidence):supplement=BlockEvidence.model_validate(supplement)
    validate_binding(packet,supplement)
    settings=development_settings(profile_id=PROFILE);base=build_graph(packet,settings)
    evidence={s.evidence_id:s for s in base.evidence}
    evidence.update({s.evidence_id:s for s in word_spans(packet,supplement)})
    groups=[];details=[];issues=[];operations=0
    for page in supplement.pages:
        axis,outside=header_axis(page)
        issues.extend({'code':code,'page':page.page} for code in (*page.diagnostics,*axis['issues']))
        columns=[{'upper':c['upper'].text,'lower':c['lower'].text,'upper_word_id':c['upper'].evidence_id,
                  'lower_word_id':c['lower'].evidence_id,'left':c['left'],'right':c['right']} for c in axis['columns']]
        for region in page.regions:
            operations+=len(page.words)+len(axis['columns'])
            if operations>MAX_OPERATIONS:raise ValueError('S2B_ASSOCIATION_OPERATION_CAP')
            words=[w for w in page.words if w.region_id==region.region_id]
            detail={'page':page.page,'region_id':region.region_id,'bbox':region.bbox.model_dump(),
                    'word_ids':[w.evidence_id for w in words],'axis_supported':axis['supported'],'columns':columns}
            if not words:
                detail['status']='REGION_WITHOUT_READABLE_WORDS';details.append(detail);continue
            span,nodes,local_issues=block_fields(packet,page,region,words);evidence[span.evidence_id]=span
            if not axis['supported']:
                local_issues.append('UNSUPPORTED_TABLE_AXES')
                if not MARKER.search(span.raw_text):
                    detail.update(status='NON_TIMETABLE_COLORED_REGION',issues=local_issues)
                    details.append(detail);continue
            else:
                h,extra,why=_day_context(packet,page,region,outside,axis);nodes.extend(extra);local_issues.extend(why)
                if h:evidence[h.evidence_id]=h
                nodes,extra,why=associate_time(packet,page,region,span,words,nodes,axis,outside);local_issues.extend(why)
                for h in extra:evidence[h.evidence_id]=h
            anchors=[n for n in nodes if n.field=='course_code' and n.state==FieldState.PRESENT]
            if region.boundary_status=='AMBIGUOUS_COMPONENT':
                local_issues.append('NON_RECTANGULAR_COLORED_COMPONENT')
                nodes=[n if n.field in {'course_code','day_of_week','date'} else n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED}) for n in nodes]
            if len(anchors)>1:
                local_issues.append('MULTIPLE_COURSES_WITHOUT_VISIBLE_REGION_SEPARATOR')
                for anchor in anchors:
                    # Preserve each code but do not force a column split or assign
                    # its neighbors' time/type/venue fields from an unseen border.
                    uncertain=[anchor]+[n if n.field in {'day_of_week','date'} else n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED}) for n in nodes if n.field!='course_code']
                    groups.append(_group(region,span,uncertain,local_issues,anchor.node_id))
            else:groups.append(_group(region,span,nodes,local_issues))
            detail.update(status='AMBIGUOUS_OWNERSHIP' if len(anchors)>1 else 'SOURCE_HYPOTHESIS',issues=local_issues)
            details.append(detail)
        if len(groups)>CONFIG['max_groups']:raise ValueError('S2B_GROUP_CAP')
    graph=base.model_copy(update={'evidence':tuple(evidence[k] for k in sorted(evidence)),
        'groups':tuple(sorted(groups,key=lambda g:g.group_id)),
        'diagnostics':base.diagnostics+('S2B_APPLICATION_ONLY_SYNTHETIC_DEVELOPMENT',)})
    layout={'profile':PROFILE,'version':'s2b.1','config_digest':CONFIG_HASH,'regions':details,'issues':issues,
        'association_operations':operations,'registered_method':False,
        'trust':'CALLER_SUPPLIED_PIXEL_AND_OCR_EVIDENCE_NOT_AUTHENTICATED'}
    return graph,settings,layout


def reconstruct_blocks(packet,supplement,mode='constrained'):
    graph,settings,layout=block_graph(packet,supplement);result=decode(graph,settings,mode)
    c=result['canonical_output'];mapping={}
    if c is None:return result,graph,mapping
    method='P1-app-s2b-v1' if mode=='constrained' else 'B3-matched-app-s2b-v1'
    def label(e):return e.model_copy(update={'revision':e.revision.model_copy(update={'method_id':method})})
    events=[];partials=[]
    for raw in c['packet']['events']:
        event=CanonicalEvent.model_validate(raw)
        g=next(g for g in graph.groups if recover_selection(g,event,graph,mode) is not None)
        mapping[event.candidate_id]=g.group_id;events.append(label(event))
    for g in graph.groups:
        if g.group_id in mapping.values():continue
        selection={f:next((n for n in d if n.state==FieldState.PRESENT),d[-1]) if len([n for n in d if n.state!=FieldState.MISSING])==1 else d[-1] for f,d in g.domains.items()}
        event=label(canonical_event(g,selection,graph,mode,unresolved=True));mapping[event.candidate_id]=g.group_id;partials.append(event)
    c['packet']=CanonicalTimetable.model_validate(c['packet']).model_copy(update={'events':tuple(events)}).model_dump(mode='json')
    c.update(unresolved_candidates=[e.model_dump(mode='json') for e in partials],method_id=method,layout=layout,candidate_groups=mapping)
    result['canonical_digest']=digest(c)
    return result,graph,mapping


def assess_blocks(packet,supplement,envelope,mode='constrained'):
    expected,graph,mapping=reconstruct_blocks(packet,supplement,mode)
    if envelope.get('canonical_digest')!=expected.get('canonical_digest') or digest(envelope.get('canonical_output'))!=digest(expected.get('canonical_output')):
        raise ValueError('S2B_SOURCE_OR_RECONSTRUCTION_MISMATCH')
    c=expected['canonical_output']
    if c is None:raise ValueError('S2B_EXECUTION_ABORTED')
    events=tuple(CanonicalEvent.model_validate(e) for e in c['packet']['events']+c['unresolved_candidates'])
    findings=tuple(Finding.model_validate(f) for f in c['findings'])
    run=BoundRun(json.dumps(envelope),digest({'packet':packet.model_dump(mode='json'),'config':CONFIG_HASH}),
        expected['canonical_digest'],packet.model_dump_json(),graph.model_dump_json(),mode,PROFILE,c['status'],
        c['base_score_digest'],tuple(mapping.items()),tuple(e.model_dump_json() for e in events),
        tuple(e['candidate_id'] for e in c['unresolved_candidates']),tuple((gid,comp['status']) for comp in c['components'] for gid in comp['group_ids']),json.dumps(c['findings']))
    return tuple(assess_event(run,e,findings) for e in events) or (assess_event(run,None,findings),)
