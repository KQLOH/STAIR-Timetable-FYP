"""S2a opt-in application graph. Registered and M5.2 graphs are never mutated.

Supported family: one axis-aligned ruled table per page, full-width row rules,
printed weekday cells at the left and printed range cells above courses. Missing
interior vertical rules permit horizontal spans only between intact row borders.
No rowspan, rotated table, arbitrary orientation or implicit clock interpretation.
"""
import json
import re
from collections import defaultdict
from itertools import combinations

from backend.models.evidence import EvidenceSpan, BoundingBox
from backend.models.event import CanonicalTimetable, CanonicalEvent, FieldState
from backend.reconstruction.models import (digest, development_settings, Context,
    EventGroup, FieldNode, Finding)
from backend.reconstruction.candidates import (build_graph, _extract, _node, FIELDS,
    DAY_RE, TIME_RE, CLASS_RE, subject_matches)
from backend.reconstruction.decoder import decode
from backend.reconstruction.adapter import canonical_event
from backend.review.binding import recover_selection, BoundRun
from backend.review.policy import assess_event
from backend.layout.service import layout_graph, relations, ambiguous_clock
from backend.layout.ruled_models import PROFILE, RuledEvidence, MAX_CELLS, MAX_GROUPS

CONFIG={'profile_id':PROFILE,'version':'s2a.1','status':'APPLICATION_ONLY_NOT_REGISTERED',
        'family':'axis-aligned-day-rows-time-range-columns','max_cells':MAX_CELLS,
        'max_groups':MAX_GROUPS,'closed_separator_coverage':.96,'absent_separator_coverage':.05,
        'pixel_threshold':150,'max_pixels_per_page':8000000,'max_total_pixels':16000000,
        'max_pages':8,'max_words_per_page':1600,'max_operations':300000,
        'word_ownership':'whole box containment, 2px tolerance; competing overlaps unresolved'}
CONFIG_HASH=digest(CONFIG)


def ordered(words):
    # Geometry determines within-cell reading order; original OCR order is retained separately.
    lines=[]
    for w in sorted(words,key=lambda w:(w.bbox.y0,w.bbox.x0,w.evidence_id)):
        matches=[line for line in lines if abs(line[0].bbox.y0-w.bbox.y0)<=max(2,(w.bbox.y1-w.bbox.y0)*.4)]
        if len(matches)==1:matches[0].append(w)
        else:lines.append([w])
    return [w for line in lines for w in sorted(line,key=lambda w:(w.bbox.x0,w.evidence_id))]


def validate_binding(packet, supplement):
    if packet.source.route!='ocr' or (packet.source.source_document_id,packet.source.content_sha256)!=(supplement.source_document_id,supplement.content_sha256):
        raise ValueError('S2A_SOURCE_MISMATCH')
    spans={s.evidence_id:s for s in packet.evidence}
    if set(p.page for p in supplement.pages)!={s.page for s in packet.evidence}:
        raise ValueError('S2A_PAGE_MISMATCH')
    for p in supplement.pages:
        for w in p.words:
            s=spans.get(w.parent_line_id);b=w.bbox
            if not s or s.page!=p.page or s.route!='ocr' or not s.bbox or s.bbox.coordinate_space!='PIXELS':
                raise ValueError('S2A_PARENT_MISMATCH')
            a=s.bbox
            if not (a.x0<=b.x0<b.x1<=a.x1 and a.y0<=b.y0<b.y1<=a.y1 and w.text.strip() in s.raw_text):
                raise ValueError('S2A_WORD_PARENT_OWNERSHIP')
            seed={'source':packet.source.source_document_id,'page':p.page,'parent':w.parent_line_id,
                  'text':w.text,'box':[b.x0,b.y0,b.x1,b.y1]}
            if w.evidence_id!='s2a-word-'+digest(seed):raise ValueError('S2A_WORD_ID_MISMATCH')


def word_spans(packet,supplement):
    return tuple(EvidenceSpan(evidence_id=w.evidence_id,source_document_id=packet.source.source_document_id,
        route='ocr',page=p.page,raw_text=w.text,bbox=w.bbox,confidence=w.confidence,
        locator=f'word:{w.evidence_id};parent:{w.parent_line_id};block:{w.block};paragraph:{w.paragraph};line:{w.line}',
        transformations=(PROFILE,'supplementary-original-ocr-word',f'adapter:{p.route}',
                         'source-transform:'+json.dumps(p.transform,sort_keys=True)))
        for p in supplement.pages for w in p.words)


def cells(page,budget=None):
    g=page.grid;xs=g.get('xs',[]);ys=g.get('ys',[]);coverage=g.get('vertical_coverage',[])
    issues=[];result=[]
    if g.get('status')!='MEASURED':return result,[{'code':'NO_SUPPORTED_RULED_GRID','page':page.page}]
    if not (3<=len(xs)<=32 and 3<=len(ys)<=32 and xs==sorted(set(xs)) and ys==sorted(set(ys)) and
            0<=xs[0]<xs[-1]<page.width and 0<=ys[0]<ys[-1]<page.height and len(coverage)==len(ys)-1 and
            all(len(row)==len(xs) and all(isinstance(v,(int,float)) and 0<=v<=1 for v in row) for row in coverage)):
        raise ValueError('S2A_GRID_MEASUREMENTS_INVALID')
    if (len(xs)-1)*(len(ys)-1)>MAX_CELLS:raise ValueError('S2A_CELL_CAP')
    budget=budget if budget is not None else [0]
    def charge(n):
        budget[0]+=n
        if budget[0]>CONFIG['max_operations']:raise ValueError('S2A_OPERATION_CAP')
    for row,(y0,y1) in enumerate(zip(ys,ys[1:])):
        if coverage[row][0]<.96 or coverage[row][-1]<.96:
            issues.append({'code':'UNSUPPORTED_OPEN_ROW_BOUNDARY','page':page.page,'row':row});continue
        boundaries=[0]+[i for i in range(1,len(xs)-1) if coverage[row][i]>=.96]+[len(xs)-1]
        for a,b in zip(boundaries,boundaries[1:]):
            uncertain=any(.05<coverage[row][i]<.96 for i in range(a+1,b))
            box=BoundingBox(x0=xs[a],y0=y0,x1=xs[b],y1=y1,coordinate_space='PIXELS')
            result.append({'row':row,'col':a,'end_col':b,'bbox':box,'ambiguous':uncertain,'words':[]})
    for w in page.words:
        charge(len(result)*2)
        b=w.bbox
        owners=[c for c in result if c['bbox'].x0-2<=b.x0 and b.x1<=c['bbox'].x1+2 and
                c['bbox'].y0-2<=b.y0 and b.y1<=c['bbox'].y1+2]
        if len(owners)==1:owners[0]['words'].append(w)
        elif re.search(r'[A-Za-z0-9]',w.text):
            issues.append({'code':'WORD_BOUNDARY_AMBIGUOUS' if owners else 'WORD_OUTSIDE_CELLS',
                           'page':page.page,'word_id':w.evidence_id})
            for c in result:
                a=c['bbox']
                if min(a.x1,b.x1)>max(a.x0,b.x0) and min(a.y1,b.y1)>max(a.y0,b.y0):c['ambiguous']=True
    for c in result:
        c['words']=ordered(c['words']);c['text']=' '.join(w.text.strip() for w in c['words'])
        charge(len(c['words'])*(len(c['words'])-1)//2)
        for a,b in combinations(c['words'],2):
            x,y=a.bbox,b.bbox;inter=max(0,min(x.x1,y.x1)-max(x.x0,y.x0))*max(0,min(x.y1,y.y1)-max(x.y0,y.y0))
            if inter>.3*min((x.x1-x.x0)*(x.y1-x.y0),(y.x1-y.x0)*(y.y1-y.y0)):
                c['ambiguous']=True;issues.append({'code':'OVERLAPPING_WORD_OWNERSHIP','page':page.page,'word_ids':[a.evidence_id,b.evidence_id]})
    return result,issues


def cell_span(packet,page,cell):
    ids=[w.evidence_id for w in cell['words']]
    return EvidenceSpan(evidence_id='s2a-cell-'+digest({'source':packet.source.content_sha256,'page':page.page,
        'box':cell['bbox'].model_dump(),'words':ids}),source_document_id=packet.source.source_document_id,
        route='ocr',raw_text=cell['text'],page=page.page,bbox=cell['bbox'],confidence=None,
        locator=f"cell:row:{cell['row']};columns:{cell['col']}:{cell['end_col']}",
        transformations=(PROFILE,'derived-word-concatenation-not-raw-ocr-line',
                         'source-words:'+','.join(ids)))


def refs(node,span,words):
    # Locate exact raw lexical substring in derived cell text; no neighboring cell links.
    hits=[];cursor=0
    for w in words:
        start=cursor;cursor+=len(w.text.strip())+1;hits.append((start,cursor-1,w.evidence_id))
    raw=node.raw or '';matches=list(re.finditer(re.escape(raw),span.raw_text,re.I)) if raw else []
    selected={wid for m in matches for a,b,wid in hits if min(b,m.end())>max(a,m.start())}
    return node.model_copy(update={'evidence_ids':tuple(sorted(set(node.evidence_ids)|selected)),
        'transformations':node.transformations+(PROFILE,'field-source-word-links')})


def domain_group(group,nodes,diagnostics=()):
    domains={}
    for field in FIELDS:
        options={digest({'v':n.value,'s':n.state.value}):n for n in nodes if n.field==field}
        if len(options)>8:raise ValueError('S2A_FIELD_VALUE_CAP')
        missing=FieldNode(candidate_id='~MISSING:'+field,node_id='~MISSING:'+field,field=field,state=FieldState.MISSING)
        domains[field]=tuple(sorted(options.values(),key=lambda n:n.node_id))+ (missing,)
    ids=tuple(sorted({i for n in nodes for i in n.evidence_ids}))
    return group.model_copy(update={'domains':domains,'evidence_ids':ids,'relations':relations(domains),
        'diagnostics':tuple(sorted(set(group.diagnostics+tuple(diagnostics)+('APPLICATION_S2A_NOT_REGISTERED',))))})


def _name(source,span,context):
    codes=subject_matches(span.raw_text)
    if len(codes)!=1:return []
    m=codes[0];tail=span.raw_text[m.end():]
    stop=re.search(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Lecture|Tutorial|Practical|Room|Venue|Lecturer|Group|Section|Cohort|Timezone|Weekly)\b|\d{1,2}:\d{2}|[;|]',tail,re.I)
    raw=tail[:stop.start() if stop else len(tail)].strip(' :-')
    if not raw or not re.search('[A-Za-z]',raw):return []
    a=span.raw_text.index(raw,m.end())
    return [_node(source,span,'course_name',raw,a,a+len(raw),context)]


def ruled_graph(packet,supplement):
    if not isinstance(supplement,RuledEvidence):supplement=RuledEvidence.model_validate(supplement)
    validate_binding(packet,supplement)
    settings=development_settings(profile_id=PROFILE)
    extras=list(word_spans(packet,supplement)); derived=[]; allgroups=[];issues=[];cellmeta=[];budget=[0]
    for page in supplement.pages:
        pagecells,diagnostics=cells(page,budget);issues.extend(diagnostics)
        courses=[c for c in pagecells if subject_matches(c['text']) and c['col']>0]
        if not courses:
            # Original M5.2 behavior for non-grid multiline evidence, plus bounded
            # same-line compound context. No guessed table axes in this branch.
            sub=packet.model_copy(update={'evidence':tuple(s for s in packet.evidence if s.page==page.page),'events':()})
            graph,_,_=layout_graph(sub)
            spans={s.evidence_id:s for s in graph.evidence}
            for group in graph.groups:
                nodes=[n for d in group.domains.values() for n in d if n.state!=FieldState.MISSING]
                allowed=[s for s in graph.evidence if s.evidence_id in group.evidence_ids]
                for s in allowed:
                    if len(subject_matches(s.raw_text))==1 and len(TIME_RE.findall(s.raw_text))>=2 and len(DAY_RE.findall(s.raw_text))==1:
                        day=DAY_RE.search(s.raw_text)
                        if day.start()<TIME_RE.search(s.raw_text).start():
                            nodes.extend(n.model_copy(update={'shared':True,'transformations':n.transformations+('s2a-single-parent-line-shared-day',)})
                                for n in _extract(packet.source,s,0,len(s.raw_text),Context()) if n.field=='day_of_week')
                linked=[]
                for n in nodes:
                    for sid in n.evidence_ids:
                        if sid not in spans:continue
                        ws=sorted([w for w in page.words if graph.capture_aliases.get(w.parent_line_id)==sid],key=lambda w:w.reading_order)
                        n=refs(n,spans[sid],ws)
                    linked.append(n)
                allgroups.append(domain_group(group,linked))
            derived.extend(graph.evidence)
            continue
        derived_cells={id(c):cell_span(packet,page,c) for c in pagecells if c['words']}
        derived.extend(derived_cells.values())
        temp=CanonicalTimetable(source=packet.source,evidence=tuple(derived_cells[id(c)] for c in courses))
        rawgraph=build_graph(temp,settings);aliases=rawgraph.capture_aliases
        derived.extend(rawgraph.evidence)
        bysid={aliases[derived_cells[id(c)].evidence_id]:c for c in courses}
        for group in rawgraph.groups:
            budget[0]+=len(pagecells)*3
            if budget[0]>CONFIG['max_operations']:raise ValueError('S2A_OPERATION_CAP')
            cell=bysid[next(iter(group.evidence_ids))]; span=next(s for s in rawgraph.evidence if s.evidence_id in group.evidence_ids)
            nodes=[refs(n,span,cell['words']) for d in group.domains.values() for n in d if n.state!=FieldState.MISSING]
            # One unambiguous printed day before both explicit session ranges may
            # cover the compound cell; distinct local days always remain local.
            day=DAY_RE.search(span.raw_text);first_time=TIME_RE.search(span.raw_text)
            if len(subject_matches(span.raw_text))==1 and len(DAY_RE.findall(span.raw_text))==1 and len(TIME_RE.findall(span.raw_text))>=2 and day and first_time and day.start()<first_time.start():
                nodes.extend(refs(n.model_copy(update={'shared':True,'transformations':n.transformations+('s2a-single-cell-shared-day',)}),span,cell['words'])
                    for n in _extract(packet.source,span,0,len(span.raw_text),Context()) if n.field=='day_of_week')
            nodes.extend(refs(n,span,cell['words']) for n in _name(packet.source,span,Context()))
            days=[c for c in pagecells if c['row']==cell['row'] and c['end_col']<=cell['col'] and DAY_RE.fullmatch(c['text'].strip())]
            times=[c for c in pagecells if c['row']<cell['row'] and c['col']>=cell['col'] and c['end_col']<=cell['end_col'] and TIME_RE.fullmatch(c['text'].strip())]
            unsupported_axis=[c for c in pagecells if c['row']<cell['row'] and
                c['col']>=cell['col'] and c['end_col']<=cell['end_col'] and
                not subject_matches(c['text']) and not TIME_RE.fullmatch(c['text'].strip()) and
                re.search(r'\d{1,2}:\d{2}|\?{2,}|\b(?:TBA|UNKNOWN|UNREADABLE)\b',c['text'],re.I)]
            # A nearer unreadable repeated header must not resurrect an older
            # readable range. Unsupported labels retain raw evidence, no values.
            if unsupported_axis and (not times or max(c['row'] for c in unsupported_axis)>=max(c['row'] for c in times)):
                nearest=max(c['row'] for c in unsupported_axis)
                unsupported_axis=[c for c in unsupported_axis if c['row']==nearest]
                times=[]
            else:unsupported_axis=[]
            if times:
                nearest=max(c['row'] for c in times);times=[c for c in times if c['row']==nearest]
            diagnostics=[]
            if len(days)==1 and not days[0]['ambiguous']:
                h=derived_cells[id(days[0])]
                nodes.extend(refs(n.model_copy(update={'shared':True}),h,days[0]['words']) for n in _extract(packet.source,h,0,len(h.raw_text),Context()) if n.field=='day_of_week')
            else:diagnostics.append('DAY_HEADER_OWNERSHIP_UNRESOLVED')
            # Full coverage of the spanned columns and contiguous printed ranges,
            # intact boundaries, one course context, and explicit clock authority.
            times.sort(key=lambda c:c['col'])
            valid=bool(times) and times[0]['col']==cell['col'] and times[-1]['end_col']==cell['end_col'] and all(a['end_col']==b['col'] for a,b in zip(times,times[1:]))
            time_nodes=[]
            for c in times:
                h=derived_cells[id(c)];ns=_extract(packet.source,h,0,len(h.raw_text),Context())
                if c['ambiguous'] or ambiguous_clock(h,packet.evidence):valid=False
                time_nodes.append([refs(n.model_copy(update={'shared':True}),h,c['words']) for n in ns if n.field in {'start_time','end_time'}])
            if any(len(ns)!=2 or any(n.state!=FieldState.PRESENT for n in ns) for ns in time_nodes):valid=False
            if valid and any(a[1].value!=b[0].value for a,b in zip(time_nodes,time_nodes[1:])):valid=False
            if cell['ambiguous'] or len(subject_matches(cell['text']))!=1:valid=False
            if not any(n.field=='start_time' for n in nodes):
                if valid:
                    pair='time-pair:s2a-'+digest({'cell':span.evidence_id,'headers':[derived_cells[id(c)].evidence_id for c in times]})
                    nodes.extend(n.model_copy(update={'transformations':n.transformations+(pair,'s2a-visible-cell-printed-range-scope')}) for n in (time_nodes[0][0],time_nodes[-1][1]))
                else:
                    diagnostics.append('TIME_SPAN_OWNERSHIP_UNRESOLVED')
                    nodes.extend(n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED}) for ns in time_nodes for n in ns)
                    for c in unsupported_axis:
                        h=derived_cells[id(c)]
                        for field in ('start_time','end_time'):
                            key=digest({'source':packet.source.content_sha256,'page':page.page,
                                'evidence':h.evidence_id,'field':field,'unsupported_raw_axis':h.raw_text})
                            node=FieldNode(candidate_id=key,node_id='s2a-unresolved-'+key,field=field,
                                value=None,state=FieldState.UNRESOLVED,raw=h.raw_text,
                                evidence_ids=(h.evidence_id,),ownership_units=(h.evidence_id,),shared=True,
                                transformations=(PROFILE,'unsupported-axis-no-interval-inference'))
                            nodes.append(refs(node,h,c['words']))
            if cell['ambiguous']:
                diagnostics.append('AMBIGUOUS_CELL_BOUNDARY')
                nodes=[n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED}) if n.field not in {'course_code','course_name'} else n for n in nodes]
            allgroups.append(domain_group(group,nodes,diagnostics))
        cellmeta.extend({'page':page.page,'row':c['row'],'columns':[c['col'],c['end_col']],
            'bbox':c['bbox'].model_dump(),'word_ids':[w.evidence_id for w in c['words']],
            'ambiguous':c['ambiguous'],'text':c['text']} for c in pagecells)
    if len(allgroups)>MAX_GROUPS:raise ValueError('S2A_GROUP_CAP')
    base=build_graph(packet,settings)
    evidence={s.evidence_id:s for s in (*base.evidence,*derived,*extras)}
    # Canonicalized original lines and supplementary words/cells coexist.
    graph=base.model_copy(update={'evidence':tuple(evidence[k] for k in sorted(evidence)),
        'groups':tuple(sorted(allgroups,key=lambda g:g.group_id)),
        'diagnostics':base.diagnostics+('S2A_APPLICATION_ONLY',)})
    layout={'profile':PROFILE,'version':'s2a.1','config_digest':CONFIG_HASH,'cells':cellmeta,'issues':issues,'association_operations':budget[0],
            'trust':'CALLER_SUPPLIED_PIXEL_AND_OCR_EVIDENCE_NOT_AUTHENTICATED','registered_method':False}
    return graph,settings,layout


def reconstruct_ruled(packet,supplement,mode='constrained'):
    graph,settings,layout=ruled_graph(packet,supplement);result=decode(graph,settings,mode)
    c=result['canonical_output'];mapping={}
    if c is None:return result,graph,mapping
    method='P1-app-s2a-v1' if mode=='constrained' else 'B3-matched-app-s2a-v1'
    def label(e):return e.model_copy(update={'revision':e.revision.model_copy(update={'method_id':method})})
    events=[];partials=[]
    for raw in c['packet']['events']:
        event=CanonicalEvent.model_validate(raw);g=next(g for g in graph.groups if recover_selection(g,event,graph,mode) is not None)
        mapping[event.candidate_id]=g.group_id;events.append(label(event))
    for g in graph.groups:
        if g.group_id in mapping.values():continue
        selection={f:next((n for n in d if n.state==FieldState.PRESENT),d[-1]) if len([n for n in d if n.state!=FieldState.MISSING])==1 else d[-1] for f,d in g.domains.items()}
        event=label(canonical_event(g,selection,graph,mode,unresolved=True));mapping[event.candidate_id]=g.group_id;partials.append(event)
    c['packet']=CanonicalTimetable.model_validate(c['packet']).model_copy(update={'events':tuple(events)}).model_dump(mode='json')
    c.update(unresolved_candidates=[e.model_dump(mode='json') for e in partials],method_id=method,layout=layout,candidate_groups=mapping)
    result['canonical_digest']=digest(c)
    return result,graph,mapping


def assess_ruled(packet,supplement,envelope,mode='constrained'):
    expected,graph,mapping=reconstruct_ruled(packet,supplement,mode)
    if envelope.get('canonical_digest')!=expected.get('canonical_digest') or digest(envelope.get('canonical_output'))!=digest(expected.get('canonical_output')):
        raise ValueError('S2A_SOURCE_OR_RECONSTRUCTION_MISMATCH')
    c=expected['canonical_output']
    if c is None:raise ValueError('S2A_EXECUTION_ABORTED')
    events=tuple(CanonicalEvent.model_validate(e) for e in c['packet']['events']+c['unresolved_candidates'])
    findings=tuple(Finding.model_validate(f) for f in c['findings'])
    run=BoundRun(json.dumps(envelope),digest({'packet':packet.model_dump(mode='json'),'config':CONFIG_HASH}),
        expected['canonical_digest'],packet.model_dump_json(),graph.model_dump_json(),mode,PROFILE,c['status'],
        c['base_score_digest'],tuple(mapping.items()),tuple(e.model_dump_json() for e in events),
        tuple(e['candidate_id'] for e in c['unresolved_candidates']),tuple((gid,comp['status']) for comp in c['components'] for gid in comp['group_ids']),json.dumps(c['findings']))
    return tuple(assess_event(run,e,findings) for e in events) or (assess_event(run,None,findings),)
