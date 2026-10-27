"""M5.2 opt-in geometry association. Original M4 modules/configs stay unchanged.

Line boxes cannot establish physical cell borders or recover lost word boxes.
Ambiguous geometry is diagnosed, not resolved with decoder scores or gold labels.
"""
import json
import re
from itertools import combinations
from pathlib import Path
from statistics import median

from backend.models.event import FieldState, CanonicalTimetable
from backend.reconstruction.models import digest, development_settings, Relation, EventGroup
from backend.reconstruction.candidates import (build_graph, subject_matches, DAY_RE, TIME_RE, CLASS_RE,
    VENUE_RE, segments, _extract, _node)
from backend.reconstruction.decoder import decode
from backend.reconstruction.adapter import canonical_event
from backend.review.binding import BoundRun, recover_selection
from backend.review.policy import assess_event

CONFIG=json.loads((Path(__file__).resolve().parents[2]/'config/m52-layout-development-v1.json').read_text())
PROFILE=CONFIG['profile_id']
CONFIG_HASH=digest(CONFIG)
METADATA_LINE=re.compile(r'^\s*(?:room|venue|location|lecturer|cohort|group|section|timezone)\b',re.I)


def heading(span):
    return bool(re.search(r'\b(?:timetable|schedule)\b',span.raw_text,re.I) and
                not (TIME_RE.search(span.raw_text) or VENUE_RE.search(span.raw_text) or CLASS_RE.search(span.raw_text)))


def comparable(a,b):
    return a.page==b.page and a.bbox and b.bbox and a.bbox.coordinate_space==b.bbox.coordinate_space


def aligned(a,b,height):
    if not comparable(a,b): return False
    x,y=a.bbox,b.bbox
    overlap=max(0,min(x.x1,y.x1)-max(x.x0,y.x0))
    return abs(x.x0-y.x0)<=height*CONFIG['column_alignment_heights'] or overlap>=CONFIG['minimum_horizontal_overlap']*max(1,min(x.x1-x.x0,y.x1-y.x0))


def associations(packet):
    if packet.source.route!='ocr' or any(s.route!='ocr' for s in packet.evidence):
        raise ValueError('OCR_LAYOUT_ROUTE_REQUIRED: native PDF and structured sources use their original paths')
    if len(packet.evidence)>CONFIG['max_evidence_spans'] or sum(len(s.raw_text) for s in packet.evidence)>500000:
        raise ValueError('LAYOUT_EVIDENCE_CAP')
    spans=sorted(packet.evidence,key=lambda s:(s.page or 0,s.bbox.y0 if s.bbox else 0,s.bbox.x0 if s.bbox else 0,s.evidence_id))
    height=median([s.bbox.y1-s.bbox.y0 for s in spans if s.bbox and s.bbox.y1>s.bbox.y0] or [1])
    excluded={s.evidence_id for s in spans if heading(s)}
    anchors=[s for s in spans if s.evidence_id not in excluded and subject_matches(s.raw_text) and not METADATA_LINE.match(s.raw_text)]
    groups={s.evidence_id:{s.evidence_id} for s in anchors};edges=[];issues=[];headers=[]
    for s in spans:
        if s.evidence_id in excluded:
            issues.append({'code':'DOCUMENT_HEADING_EXCLUDED','evidence_ids':[s.evidence_id]});continue
        if s in anchors:continue
        if DAY_RE.fullmatch(s.raw_text.strip()) or TIME_RE.fullmatch(s.raw_text.strip()):headers.append(s);continue
        if re.fullmatch(r'\s*\d{1,2}:\d{2}\s*',s.raw_text):
            issues.append({'code':'SINGLE_TIME_AXIS_LABEL_UNSUPPORTED','evidence_ids':[s.evidence_id]});continue
        if not (TIME_RE.search(s.raw_text) or DAY_RE.search(s.raw_text) or VENUE_RE.search(s.raw_text) or CLASS_RE.search(s.raw_text) or re.search(r'lecturer\s*:',s.raw_text,re.I)):
            issues.append({'code':'UNCLASSIFIED_SOURCE_LINE','evidence_ids':[s.evidence_id]});continue
        choices=[]
        for a in anchors:
            if len(subject_matches(a.raw_text))!=1 or not comparable(a,s) or s.bbox.y0<a.bbox.y0:continue
            if any(other is not a and comparable(a,other) and aligned(a,other,height) and
                   a.bbox.y1<=other.bbox.y0<=s.bbox.y0 for other in anchors):continue
            members=[t for t in spans if t.evidence_id in groups[a.evidence_id]]
            if any(aligned(t,s,height) and 0<=s.bbox.y0-t.bbox.y1<=CONFIG['cross_line_max_gap_heights']*height for t in members): choices.append(a)
        if len(choices)==1:
            a=choices[0];groups[a.evidence_id].add(s.evidence_id)
            edges.append({'kind':'bounded_column_continuation','from':a.evidence_id,'to':s.evidence_id,'evidence_ids':[a.evidence_id,s.evidence_id]})
        else:issues.append({'code':'AMBIGUOUS_LINE_ASSOCIATION' if choices else 'UNASSOCIATED_FIELD_LINE','evidence_ids':[s.evidence_id]+[a.evidence_id for a in choices]})
    for a in anchors:
        for kind in ('day','time'):
            choices=[]
            for h in headers:
                if not comparable(a,h):continue
                b,c=a.bbox,h.bbox
                if kind=='day' and DAY_RE.fullmatch(h.raw_text.strip()) and c.x1<=b.x0 and min(b.y1,c.y1)>max(b.y0,c.y0) and b.x0-c.x1<=height*CONFIG['header_max_gap_heights']:choices.append(h)
                if kind=='time' and TIME_RE.fullmatch(h.raw_text.strip()) and c.y1<=b.y0 and min(b.x1,c.x1)>max(b.x0,c.x0) and b.y0-c.y1<=height*CONFIG['header_max_gap_heights']:choices.append(h)
            # Repeated headers supersede only when the same column/row geometry proves order.
            if kind=='time' and choices:
                nearest=max(h.bbox.y0 for h in choices);choices=[h for h in choices if h.bbox.y0==nearest]
            if len(choices)==1:
                h=choices[0];groups[a.evidence_id].add(h.evidence_id)
                edges.append({'kind':kind+'_axis_scope','from':h.evidence_id,'to':a.evidence_id,'evidence_ids':[h.evidence_id,a.evidence_id]})
                if kind=='time' and ambiguous_clock(h,packet.evidence):
                    issues.append({'code':'TIME_AXIS_MERIDIEM_UNRESOLVED','evidence_ids':[h.evidence_id]})
            elif len(choices)>1:
                issues.append({'code':'AMBIGUOUS_SPANNING_HEADER','evidence_ids':[a.evidence_id]+[h.evidence_id for h in choices]})
    if len(anchors)>1 and headers:
        issues.append({'code':'TABLE_AXIS_REGION_CANDIDATE','evidence_ids':[s.evidence_id for s in anchors+headers]})
    if any(len(subject_matches(s.raw_text))>1 for s in spans):issues.append({'code':'MULTI_COURSE_LINE_BOUNDARIES_UNCONFIRMED','evidence_ids':[s.evidence_id for s in spans if len(subject_matches(s.raw_text))>1]})
    issues.append({'code':'LINE_BOX_ONLY_NO_PIXEL_CELL_SEGMENTATION','evidence_ids':[]})
    return groups,edges,issues,excluded


def relations(domains):
    nodes=sorted([n for d in domains.values() for n in d if n.state!=FieldState.MISSING],key=lambda n:n.node_id)
    result=[]
    for a,b in combinations(nodes,2):
        if a.field==b.field:continue
        compatible=True
        if {a.field,b.field}=={'start_time','end_time'}:
            compatible=bool({t for t in a.transformations if t.startswith('time-pair:')}&{t for t in b.transformations if t.startswith('time-pair:')})
        result.append(Relation(left=a.node_id,right=b.node_id,kind='same_source_region' if set(a.evidence_ids)&set(b.evidence_ids) else 'explicit_continuation',compatible=compatible))
    return tuple(r for kind in sorted({r.kind for r in result}) for r in [x for x in result if x.kind==kind][:64])


def layout_graph(packet):
    clusters,edges,issues,excluded=associations(packet)
    settings=development_settings(profile_id=PROFILE)
    graph=build_graph(packet,settings)
    aliases=graph.capture_aliases;spans={s.evidence_id:s for s in graph.evidence}
    # Match M4's capture-alias convention: random extractor span IDs belong in
    # execution_metadata, not in the deterministic reconstruction digest.
    edges=[{**e,'from':aliases[e['from']],'to':aliases[e['to']],
            'evidence_ids':[aliases[i] for i in e['evidence_ids']]} for e in edges]
    issues=[{**i,'evidence_ids':[aliases[v] for v in i['evidence_ids']]} for i in issues]
    # Identify each raw group by its original source range, not inherited evidence.
    owner={'group-'+digest({'evidence':sid,'source_range':[a,b]}):sid
           for sid,s in spans.items() for a,b in segments(s.raw_text)}
    clusters={aliases[key]:{aliases[v] for v in values} for key,values in clusters.items()}
    omitted={aliases[v] for v in excluded};used=set();groups=[]
    for anchor,allowed in sorted(clusters.items()):
        roots=[g for g in graph.groups if owner.get(g.group_id)==anchor]
        if len(roots)!=1:continue # Compound cells retain original distinct session groups.
        members=[g for g in graph.groups if owner.get(g.group_id) in allowed]
        group=_combine(members,allowed,graph,spans[anchor]);groups.append(group);used.update(g.group_id for g in members)
    for g in graph.groups:
        if g.group_id in used or owner.get(g.group_id) in omitted:continue
        sid=owner.get(g.group_id)
        groups.append(_combine([g],{sid} if sid else set(),graph,spans.get(sid)))
    if len(groups)>CONFIG['max_layout_groups']:raise ValueError('LAYOUT_GROUP_CAP')
    result=graph.model_copy(update={'groups':tuple(sorted(groups,key=lambda g:g.group_id)),
        'diagnostics':tuple(sorted(set(graph.diagnostics+tuple(i['code'] for i in issues))))})
    return result,settings,{'profile':PROFILE,'config_digest':CONFIG_HASH,'edges':edges,'issues':issues,
        'groups':{k:sorted(v) for k,v in clusters.items()},'coordinate_policy':'Original source coordinate space; scale-relative geometry, no guessed DPI'}


def _combine(members,allowed,graph,anchor):
    domains={};extra=[]
    for sid in sorted(allowed):
        s=next(s for s in graph.evidence if s.evidence_id==sid)
        if DAY_RE.fullmatch(s.raw_text.strip()) or TIME_RE.fullmatch(s.raw_text.strip()):
            nodes=_extract(graph.source,s,0,len(s.raw_text),graph.context)
            if TIME_RE.fullmatch(s.raw_text.strip()) and ambiguous_clock(s,graph.evidence):
                nodes=[n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED,'transformations':n.transformations+('m52-axis-meridiem-unresolved',)}) for n in nodes]
            extra.extend(n.model_copy(update={'shared':True,'transformations':n.transformations+('m52-unique-axis-scope',)}) for n in nodes)
    if anchor and len(subject_matches(anchor.raw_text))==1:
        match=subject_matches(anchor.raw_text)[0];tail=anchor.raw_text[match.end():]
        stop=re.search(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Lecture|Tutorial|Practical|Room|Venue|Lecturer|Group|Section|Cohort|Timezone|Weekly)\b|\d{1,2}:\d{2}|[;|]',tail,re.I)
        end=match.end()+(stop.start() if stop else len(tail));raw=anchor.raw_text[match.end():end].strip(' :-')
        if raw and re.search(r'[A-Za-z]',raw) and not raw.lower().startswith('course name'):
            start=anchor.raw_text.index(raw,match.end());extra.append(_node(graph.source,anchor,'course_name',raw,start,start+len(raw),graph.context))
    for field in members[0].domains if members else ():
        choices={n.node_id:n for g in members for n in g.domains[field] if n.state!=FieldState.MISSING and set(n.evidence_ids)<=allowed}
        if field=='course_code':
            choices={key:n for key,n in choices.items() if not any(METADATA_LINE.match(s.raw_text) for s in graph.evidence if s.evidence_id in n.evidence_ids)}
        for key,n in list(choices.items()):
            if field in {'start_time','end_time'} and any(TIME_RE.fullmatch(s.raw_text.strip()) and ambiguous_clock(s,graph.evidence)
                    for s in graph.evidence if s.evidence_id in n.evidence_ids):
                choices[key]=n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED,'transformations':n.transformations+('m52-axis-meridiem-unresolved',)})
        # Do not recombine compound source segments; extra lexical nodes only fill absent fields.
        if not choices:
            choices.update({n.node_id:n for n in extra if n.field==field})
        options=sorted(choices.values(),key=lambda n:n.node_id)
        unique={}
        for n in options:unique.setdefault(digest({'value':n.value,'state':n.state.value}),n)
        if len(unique)>8:raise ValueError('LAYOUT_FIELD_VALUE_CAP: original evidence retained; association not emitted')
        domains[field]=tuple(list(unique.values())[:8]+[members[0].domains[field][-1]])
    gid='layout-'+digest({'profile':PROFILE,'config':CONFIG_HASH,'members':sorted(g.group_id for g in members),'evidence':sorted(allowed)})
    diagnostics={'PROVISIONAL_LAYOUT_ASSOCIATION'}|{d for g in members for d in g.diagnostics}
    possible=[n for domain in domains.values() for n in domain if n.state!=FieldState.MISSING]
    if len(possible)*(len(possible)-1)//2>64:diagnostics.add('RELATION_CAP_POSSIBLE')
    return EventGroup(group_id=gid,evidence_ids=tuple(sorted(allowed)),domains=domains,relations=relations(domains),diagnostics=tuple(sorted(diagnostics)))


def ambiguous_clock(span,evidence):
    return not re.search(r'\b[AP]\.?M\.?\b',span.raw_text,re.I) and not any(s.page==span.page and re.search(r'\b24[ -]hour\b',s.raw_text,re.I) for s in evidence) and any(1<=int(h)<=12 for h in re.findall(r'\b(\d{1,2}):\d{2}',span.raw_text))


def reconstruct_layout(packet,mode='constrained'):
    graph,settings,layout=layout_graph(packet)
    result=decode(graph,settings,mode)
    canonical=result['canonical_output']
    if canonical is None:return {**result,'layout':layout},graph,{}
    mapping={};events=[];partials=[]
    for raw in canonical['packet']['events']:
        from backend.models.event import CanonicalEvent
        event=CanonicalEvent.model_validate(raw)
        g=next(g for g in graph.groups if recover_selection(g,event,graph,mode) is not None)
        mapping[event.candidate_id]=g.group_id;events.append(_label(event,mode))
    selected=set(mapping.values())
    for g in graph.groups:
        if g.group_id in selected:continue
        selection={field:next((n for n in domain if n.state==FieldState.PRESENT),domain[-1]) if len([n for n in domain if n.state!=FieldState.MISSING])==1 else domain[-1]
                   for field,domain in g.domains.items()}
        event=canonical_event(g,selection,graph,mode,unresolved=True)
        mapping[event.candidate_id]=g.group_id;partials.append(_label(event,mode))
    canonical['packet']=CanonicalTimetable.model_validate(canonical['packet']).model_copy(update={'events':tuple(events)}).model_dump(mode='json')
    canonical['unresolved_candidates']=[e.model_dump(mode='json') for e in partials]
    canonical['method_id']='P1-layout-m52-v1' if mode=='constrained' else 'B3-matched-layout-m52-v1'
    canonical['layout']=layout;canonical['candidate_groups']=mapping
    result['canonical_digest']=digest(canonical)
    return result,graph,mapping


def _label(event,mode):
    revision=event.revision.model_copy(update={'method_id':'P1-layout-m52-v1' if mode=='constrained' else 'B3-matched-layout-m52-v1'})
    return event.model_copy(update={'revision':revision})


def assess_layout(packet,envelope,mode='constrained'):
    expected,graph,mapping=reconstruct_layout(packet,mode)
    if envelope.get('canonical_digest')!=expected.get('canonical_digest') or digest(envelope.get('canonical_output'))!=digest(expected.get('canonical_output')):
        raise ValueError('LAYOUT_SOURCE_OR_RECONSTRUCTION_MISMATCH')
    canonical=expected['canonical_output']
    if canonical is None:raise ValueError('LAYOUT_EXECUTION_ABORTED_NO_ASSESSABLE_CANDIDATES')
    from backend.models.event import CanonicalEvent
    from backend.reconstruction.models import Finding
    events=tuple(CanonicalEvent.model_validate(e) for e in canonical['packet']['events']+canonical['unresolved_candidates'])
    findings=tuple(Finding.model_validate(f) for f in canonical['findings'])
    run=BoundRun(json.dumps(envelope),digest({'packet':packet.model_dump(mode='json'),'layout_config':CONFIG_HASH,'settings':graph.settings_digest,'mode':mode}),expected['canonical_digest'],
        packet.model_dump_json(),graph.model_dump_json(),mode,PROFILE,canonical['status'],canonical['base_score_digest'],tuple(mapping.items()),
        tuple(e.model_dump_json() for e in events),tuple(e['candidate_id'] for e in canonical['unresolved_candidates']),
        tuple((gid,c['status']) for c in canonical['components'] for gid in c['group_ids']),json.dumps(canonical['findings']))
    return tuple(assess_event(run,e,findings) for e in events) or (assess_event(run,None,findings),)
