"""Source-backed field domains. Regex/graph foundations adapted from baseline event_graph.py."""
from decimal import Decimal, ROUND_HALF_EVEN
from hashlib import sha256
from itertools import combinations
import re

from backend.models.event import EventContent, FieldState
from backend.services.normalizer import normalize_day, normalize_time, normalize_date, normalize_class_type
from backend.reconstruction.models import FieldNode, EventGroup, Graph, Relation, Context, digest

# Selected baseline lexical rules, with documented M4 field-state handling.
SUBJECT_RE = re.compile(r'\b([A-Z]{2,}\s*\d{3,4}[A-Z]?)\b', re.I)
DAY_RE = re.compile(r'\b(Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:rs(?:day)?)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b', re.I)
DATE_RE = re.compile(r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b', re.I)
TIME_RE = re.compile(r'\b(\d{1,2}(?::?\d{2})?\s*(?:[AP]\.?M\.?)?)\s*(?:-|–|—|to)\s*(\d{1,2}(?::?\d{2})?\s*(?:[AP]\.?M\.?)?)\b', re.I)
CLASS_RE = re.compile(r'\b(Lecture|Lec|Practical|Prac|Tutorial|Tut|Laboratory|Lab)\b', re.I)
VENUE_RE = re.compile(r'\b(?:venue|room|location)\s*[:=-]?\s*([^;|\n\[\]]+?)(?=\s+(?:Lecture|Lec|Practical|Prac|Tutorial|Tut|Lab|weekly|weeks?|except|excluded|group|cohort|timezone|term|lecturer|course\s+name)\b|[;|\n\[\]]|$)', re.I)
UNKNOWN_RE = re.compile(r'\b(TBA|TBD|UNKNOWN|UNREADABLE)\b|\?{2,}', re.I)
WEEK_RE = re.compile(r'\b(?:(excluded|except)\s+)?weeks?\s*:?\s*(TBA|UNKNOWN|[0-9]+(?:\s*(?:-|,|&|to)\s*[0-9]+)*)', re.I)
FIELDS = tuple(sorted(EventContent.model_fields))


def clean(value):
    return ' '.join(value.split())


def subject_matches(text):
    # Prevent lexical course anchors inside explicit dates (e.g. "to 2026-03-01").
    date_ranges=[m.span() for m in DATE_RE.finditer(text)]
    return [m for m in SUBJECT_RE.finditer(text) if not any(m.start()<b and m.end()>a for a,b in date_ranges)]


def normalize(field, raw):
    if UNKNOWN_RE.search(raw):
        return None
    if field == 'course_code':
        return re.sub(r'\s+', '', raw).upper() if SUBJECT_RE.fullmatch(raw) else None
    if field == 'day_of_week':
        day = normalize_day(raw)
        return ('MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY','SUNDAY').index(day)+1 if day else None
    if field in {'start_time','end_time'}:
        return normalize_time(raw, allow_end_of_day=field == 'end_time')
    if field == 'session_type':
        return 'Practical' if raw.lower() == 'laboratory' else normalize_class_type(raw)
    if field == 'date':
        return normalize_date(raw)
    if field in {'teaching_weeks','excluded_weeks'}:
        weeks = set()
        for part in re.split(r'\s*[, &]\s*', raw):
            match = re.fullmatch(r'(\d+)(?:\s*(?:-|to)\s*(\d+))?',part)
            if not match:
                return None
            start,end = int(match[1]),int(match[2] or match[1])
            if start < 1 or end < start or end-start > 1000:
                return None
            weeks.update(range(start,end+1))
        return tuple(sorted(weeks)) or None
    if field == 'end_day_offset':
        return 1 if re.fullmatch(r'overnight|\+1\s*day',raw,re.I) else None
    if field == 'recurrence':
        return {'frequency':'WEEKLY','source_rule':raw} if re.fullmatch(r'weekly|every\s+week|each\s+week',raw,re.I) else None
    return clean(raw) or None


def segments(text):
    cuts = {0,len(text)}
    for match in re.finditer(r'[;|\n]|\s+&\s+',text):
        cuts.add(match.start()); cuts.add(match.end())
    for a,b in zip(sorted(cuts),sorted(cuts)[1:]):
        part=text[a:b]
        if not part.strip() or re.fullmatch(r'[;|\n\s&]+',part):
            continue
        codes=subject_matches(part); types=list(CLASS_RE.finditer(part))
        inner=[m.start() for m in codes[1:]] if len(codes)>1 and len(TIME_RE.findall(part))>=len(codes) else (
            [m.start() for m in types[1:]] if len(types)>1 and len(TIME_RE.findall(part))>=len(types) else [])
        bounds=[a]+[a+value for value in inner]+[b]
        for start,end in zip(bounds,bounds[1:]):
            yield start,end


def coordinates(span, context):
    if span.bbox is None:
        return None, 'GEOMETRY_UNAVAILABLE'
    values=(span.bbox.x0,span.bbox.y0,span.bbox.x1,span.bbox.y1)
    unit=span.bbox.coordinate_space
    meta=next((m for m in context.coordinates if m.page==span.page),None)
    if unit=='PIXELS' and meta and meta.dpi:
        values=tuple(v*72/meta.dpi for v in values); unit='PDF_POINTS'
    if unit!='PDF_POINTS':
        return {'space':unit,'values':list(values)},'COORDINATE_MAPPING_UNFROZEN'
    quantized=[str(Decimal(str(v)).quantize(Decimal('0.1'),rounding=ROUND_HALF_EVEN)) for v in values]
    return {'space':unit,'values':quantized},None if meta and meta.rotation_normalized else 'ROTATION_AUTHORITY_UNCONFIRMED'


def _node(source,span,field,raw,start,end,context,shared=False,value=None):
    if value is None:
        value=normalize(field,raw)
    coord,_=coordinates(span,context)
    seed={'source_digest':source.content_sha256,'page':span.page,'route':span.route,
          'field':field,'value':value,'coordinates':coord,'source_text_range':[start,end]}
    cid=digest(seed)
    return FieldNode(candidate_id=cid,node_id=sha256((cid+'\x1f'+field).encode()).hexdigest(),field=field,
                     value=value,state=FieldState.PRESENT if value is not None else FieldState.UNRESOLVED,
                     raw=raw,evidence_ids=(span.evidence_id,),confidence=span.confidence,
                     ownership_units=(f'{span.evidence_id}:{start}:{end}',),shared=shared,
                     transformations=('m4-source-lexical-normalization-v1',))


def _extract(source,span,start,end,context):
    text=span.raw_text[start:end]; nodes=[]
    def emit(field,match,index=0,shared=False):
        raw=match.group(index); a,b=match.span(index)
        value=normalize(field,raw)
        if field=='recurrence' and value:
            term=re.search(r'\bterm\s*:\s*(\d{4}-\d{2}-\d{2})\s*(?:to|\.\.)\s*(\d{4}-\d{2}-\d{2})',text,re.I)
            if term:
                value={**value,'term_start':normalize_date(term[1]),'term_end':normalize_date(term[2])}
        nodes.append(_node(source,span,field,raw,start+a,start+b,context,shared,value))
    weeks=list(WEEK_RE.finditer(text))
    dates=list(DATE_RE.finditer(text))
    for m in subject_matches(text): emit('course_code',m,1)
    for m in DAY_RE.finditer(text): emit('day_of_week',m,1)
    for m in dates: emit('date',m,1)
    for m in TIME_RE.finditer(text):
        if any(w.start()<=m.start()<w.end() for w in weeks+dates): continue
        emit('start_time',m,1); emit('end_time',m,2)
        pair=f'time-pair:{span.evidence_id}:{start+m.start()}:{start+m.end()}'
        nodes[-2:]=[n.model_copy(update={'transformations':n.transformations+(pair,)}) for n in nodes[-2:]]
    for m in CLASS_RE.finditer(text): emit('session_type',m,1)
    for m in VENUE_RE.finditer(text): emit('location',m,1)
    for m in weeks: emit('excluded_weeks' if m[1] else 'teaching_weeks',m,2)
    for field,pattern in (
        ('course_name',r'\b(?:course\s+name|name)\s*:\s*([^\[\];|]+)'),
        ('section',r'\b(?:tutorial\s+group|lab\s+group|group|section)\s*:?\s*(TBA|UNKNOWN|[A-Z0-9_-]+)'),
        ('cohort_id',r'\bcohort\s*:?\s*(TBA|UNKNOWN|[A-Z0-9_-]+)'),
        ('source_timezone',r'\btimezone\s*:\s*([A-Za-z_]+/[A-Za-z_]+)'),
        ('recurrence',r'\b(weekly|every\s+week|each\s+week|biweekly|daily)\b'),
        ('end_day_offset',r'(overnight|\+1\s*day)'),
        ('lecturer',r'\blecturer\s*:\s*([^\];|]+)')):
        for m in re.finditer(pattern,text,re.I): emit(field,m,1)
    for m in re.finditer(r'\btime\s*:\s*(TBA|UNKNOWN|UNREADABLE)',text,re.I):
        emit('start_time',m,1); emit('end_time',m,1)
    term=re.search(r'\bterm\s*:\s*(\d{4}-\d{2}-\d{2})\s*(?:to|\.\.)\s*(\d{4}-\d{2}-\d{2})',text,re.I)
    if term:
        nodes=[n for n in nodes if n.field!='date' or not any(f':{start+term.start(i)}:{start+term.end(i)}' in u
                                                          for i in (1,2) for u in n.ownership_units)]
    if not any(n.field in {'course_code','course_name'} for n in nodes) and any(n.field in {'start_time','session_type'} for n in nodes):
        if UNKNOWN_RE.search(text):
            m=UNKNOWN_RE.search(text); emit('course_name',m)
    return nodes


def _source_group(span):
    return next((t.split(':',1)[1] for t in span.transformations if t.startswith('source-group:')),None)


def build_graph(packet,settings,context=Context()):
    if any(s.route not in {packet.source.route,'authority'} for s in packet.evidence):
        raise ValueError('Mixed evidence routes require a separately approved evidence contract')
    if settings.base_weights['extraction'] and any(s.route not in settings.route_support_tables for s in packet.evidence if s.route!='authority'):
        raise ValueError('Evidence route lacks an explicitly configured support table')
    spans={}; aliases={}; diagnostics=['INPUT_ISSUE:'+i.code for i in packet.issues]
    for span in packet.evidence:
        coord,warning=coordinates(span,context)
        sid='ev-'+digest({'source':packet.source.content_sha256,'page':span.page,'route':span.route,
                         'text':span.raw_text,'bbox':coord,'locator':span.locator})
        aliases[span.evidence_id]=sid
        if sid in spans:
            diagnostics.append('DUPLICATE_EVIDENCE_COLLAPSED')
        else:
            spans[sid]=span.model_copy(update={'evidence_id':sid})
        if warning: diagnostics.append(warning)
    def remap(ids):
        if any(i not in aliases for i in ids): raise ValueError('Unknown context evidence reference')
        return tuple(aliases[i] for i in ids)
    links=tuple(link.model_copy(update={'source_evidence_id':remap((link.source_evidence_id,))[0],
                                        'target_evidence_id':remap((link.target_evidence_id,))[0],
                                        'reason_evidence_ids':remap(link.reason_evidence_ids)}) for link in context.links)
    term=context.term.model_copy(update={'evidence_ids':remap(context.term.evidence_ids)}) if context.term else None
    context=context.model_copy(update={'links':links,'term':term,'timezone_authority_ids':remap(context.timezone_authority_ids),
                                       'alternative_group_evidence_ids':remap(context.alternative_group_evidence_ids)})
    raw_groups=[]; headers=[]
    for span in sorted(spans.values(),key=lambda s:s.evidence_id):
        if span.route=='authority':
            continue # Authority records are not timetable session observations.
        nodes=_extract(packet.source,span,0,len(span.raw_text),context)
        pure_day=DAY_RE.fullmatch(span.raw_text.strip()) is not None
        pure_time=TIME_RE.fullmatch(span.raw_text.strip()) is not None
        context_only=bool(re.match(r'\s*(?:cohort|course name|timezone)\s*:',span.raw_text,re.I))
        continuation_target=any(l.kind=='event_continuation' and l.target_evidence_id==span.evidence_id for l in links)
        if (pure_day or pure_time or context_only) and not continuation_target:
            headers.extend(n.model_copy(update={'shared':True}) for n in nodes)
            continue
        for start,end in segments(span.raw_text):
            selected=_extract(packet.source,span,start,end,context)
            if not selected: continue
            gid='group-'+digest({'evidence':span.evidence_id,'source_range':[start,end]})
            raw_groups.append({'id':gid,'span':span,'nodes':selected,'evidence':{span.evidence_id},'diagnostics':[]})
        if not nodes:
            diagnostics.append('UNPARSED_SOURCE_REGION:'+span.evidence_id)
    # Explicit, evidenced continuation can join a unique source and target mention.
    for link in links:
        sources=[g for g in raw_groups if link.source_evidence_id in g['evidence']]
        targets=[g for g in raw_groups if link.target_evidence_id in g['evidence']]
        if link.kind=='event_continuation' and len(sources)==len(targets)==1 and sources[0] is not targets[0]:
            left,right=sources[0],targets[0]
            left['nodes'].extend(right['nodes']); left['evidence'].update(right['evidence'])
            left['diagnostics'].append('EXPLICIT_CROSS_PAGE_CONTINUATION' if left['span'].page!=right['span'].page else 'EXPLICIT_CONTINUATION')
            raw_groups.remove(right)
        elif link.kind=='event_continuation':
            diagnostics.append('AMBIGUOUS_CONTINUATION_SCOPE')
    groups=[]
    for group in raw_groups:
        span=group['span']; current=list(group['nodes'])
        # Unique course context within a compound source region is shareable.
        same_span_nodes=_extract(packet.source,span,0,len(span.raw_text),context)
        subjects=[n for n in same_span_nodes if n.field=='course_code']
        if len(subjects)==1 and not any(n.field=='course_code' for n in current):
            current.append(subjects[0].model_copy(update={'shared':True,'transformations':subjects[0].transformations+('same-cell-course-context',)}))
        if len(subjects)==1 and len([g for g in raw_groups if g['span'].evidence_id==span.evidence_id])>1:
            current=[n.model_copy(update={'shared':True}) if n.field=='course_code' else n for n in current]
        for header in headers:
            hspan=spans[header.evidence_ids[0]]
            same_group=_source_group(span) and _source_group(span)==_source_group(hspan)
            scoped=False
            if span.page==hspan.page and span.bbox and hspan.bbox and span.bbox.coordinate_space==hspan.bbox.coordinate_space:
                b,h=span.bbox,hspan.bbox
                row=min(b.y1,h.y1)>max(b.y0,h.y0) and h.x1<=b.x0 and b.x0-h.x1<=settings.header_max_gap
                column=min(b.x1,h.x1)>max(b.x0,h.x0) and h.y1<=b.y0 and b.y0-h.y1<=settings.header_max_gap
                scoped=row or column
            if (same_group or scoped) and not any(n.field==header.field for n in group['nodes']):
                current.append(header.model_copy(update={'transformations':header.transformations+('source-header-scope',)}))
                group['evidence'].update(header.evidence_ids)
        for link in links:
            if link.kind=='shared_context' and link.target_evidence_id in group['evidence']:
                origin=spans[link.source_evidence_id]
                for n in _extract(packet.source,origin,0,len(origin.raw_text),context):
                    if n.field in {'course_code','course_name','cohort_id','section','source_timezone'} and not any(o.field==n.field for o in group['nodes']):
                        current.append(n.model_copy(update={'shared':True,'transformations':n.transformations+('explicit-source-context-link',)}))
                        group['evidence'].update(n.evidence_ids)
        domains={}; local=list(group['diagnostics'])
        for field in FIELDS:
            nodes=sorted({n.node_id:n for n in current if n.field==field}.values(),key=lambda n:n.node_id)
            if len(nodes)>settings.max_field_spans: local.append('FIELD_SPAN_CAP')
            nodes=nodes[:settings.max_field_spans]
            values=[]; seen=set()
            for n in nodes:
                key=digest({'value':n.value,'state':n.state.value})
                if key not in seen:
                    values.append(n); seen.add(key)
            if len(values)>settings.max_field_values: local.append('FIELD_VALUE_CAP')
            values=values[:settings.max_field_values]
            missing=FieldNode(candidate_id='~MISSING:'+field,node_id='~MISSING:'+field,field=field,state=FieldState.MISSING)
            domains[field]=tuple(values+[missing])
        relations=[]
        present=[n for values in domains.values() for n in values if n.state!=FieldState.MISSING]
        for left,right in combinations(sorted(present,key=lambda n:n.node_id),2):
            if left.field==right.field: continue
            same=bool(set(left.evidence_ids)&set(right.evidence_ids))
            kind='same_source_region' if same else 'header_scope' if left.shared or right.shared else 'explicit_continuation'
            compatible=True
            if {left.field,right.field}=={'start_time','end_time'}:
                pairs=lambda n:{t for t in n.transformations if t.startswith('time-pair:')}
                compatible=bool(pairs(left)&pairs(right))
            relations.append(Relation(left=left.node_id,right=right.node_id,kind=kind,compatible=compatible))
        retained=[]
        for kind in sorted({r.kind for r in relations}):
            typed=[r for r in relations if r.kind==kind]
            if len(typed)>settings.max_edges_per_group: local.append('RELATION_CAP')
            retained.extend(typed[:settings.max_edges_per_group])
        if not span.bbox: local.append('GEOMETRY_UNAVAILABLE')
        if len(domains['start_time'])>2 or len(domains['end_time'])>2: local.append('AMBIGUOUS_TEMPORAL_PAIRING')
        groups.append(EventGroup(group_id=group['id'],evidence_ids=tuple(sorted(group['evidence'])),domains=domains,
                                 relations=tuple(retained),diagnostics=tuple(sorted(set(local)))))
    return Graph(source=packet.source,evidence=tuple(sorted(spans.values(),key=lambda s:s.evidence_id)),
                 groups=tuple(sorted(groups,key=lambda g:g.group_id)),context=context,
                 input_issues=tuple(i.model_copy(update={'evidence_ids':remap(i.evidence_ids),'candidate_id':None}) for i in packet.issues),
                 settings_digest=digest(settings.model_dump(mode='json')),diagnostics=tuple(sorted(set(diagnostics))),capture_aliases=aliases,
                 input_revision_provenance=tuple({'candidate_id':e.candidate_id,'revision':e.revision.model_dump(mode='json'),
                                                  'input_policy_not_transferred':e.policy.model_dump(mode='json')}
                                                 for e in packet.events))
