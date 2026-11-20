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
    DAY_RE, DATE_RE, TIME_RE, CLASS_RE, subject_matches)
from backend.reconstruction.decoder import decode
from backend.reconstruction.adapter import canonical_event
from backend.review.binding import recover_selection, BoundRun
from backend.review.policy import assess_event
from backend.layout.service import layout_graph, relations, ambiguous_clock
from backend.layout.ruled_models import PROFILE, RuledEvidence, MAX_CELLS, MAX_GROUPS

CONFIG={'profile_id':PROFILE,'version':'s2a.3','status':'APPLICATION_ONLY_NOT_REGISTERED',
        'family':'axis-aligned-day-rows-time-range-columns','max_cells':MAX_CELLS,
        'max_groups':MAX_GROUPS,'closed_separator_coverage':.96,'absent_separator_coverage':.05,
        'pixel_threshold':150,'max_pixels_per_page':8000000,'max_total_pixels':16000000,
        'max_pages':8,'max_words_per_page':1600,'max_operations':300000,
        'word_ownership':'whole box containment, 2px tolerance; competing overlaps unresolved',
        'glyph_aware_separator_measurements':True,'local_horizontal_cell_splits':True,'empty_header_ocr_retry':True}
CONFIG_HASH=digest(CONFIG)


def ordered(words):
    # Geometry determines within-cell reading order; original OCR order is retained separately.
    lines=[]
    for w in sorted(words,key=lambda w:(w.bbox.y0,w.bbox.x0,w.evidence_id)):
        matches=[line for line in lines if abs(line[0].bbox.y0-w.bbox.y0)<=max(2,(w.bbox.y1-w.bbox.y0)*.4) or min(line[0].bbox.y1,w.bbox.y1)-max(line[0].bbox.y0,w.bbox.y0)>=.5*min(line[0].bbox.y1-line[0].bbox.y0,w.bbox.y1-w.bbox.y0)]
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
    local=g.get('local_horizontal_rules',[])
    if not isinstance(local,list) or len(local)>256:raise ValueError('S2A_LOCAL_SEPARATOR_CAP')
    for rule in local:
        if not isinstance(rule,dict) or any(type(rule.get(k)) is not int for k in ('y','x0','x1')) or not (0<=rule['x0']<rule['x1']<page.width and 0<rule['y']<page.height):raise ValueError('S2A_LOCAL_GRID_MEASUREMENTS_INVALID')
    split=[]
    for cell in result:
        a=cell['bbox'];inside=[v for v in local if a.y0+6<v['y']<a.y1-6 and min(v['x1'],a.x1)-max(v['x0'],a.x0)>2]
        covers=[v for v in inside if v['x0']<=a.x0+2 and v['x1']>=a.x1-2]
        if len(covers)!=len(inside):
            cell['ambiguous']=True;issues.append({'code':'UNSUPPORTED_PARTIAL_CELL_SPLIT','page':page.page,'row':cell['row'],'col':cell['col']})
        cuts=sorted({v['y'] for v in covers})
        boundaries=[a.y0]+cuts+[a.y1]
        for index,(top,bottom) in enumerate(zip(boundaries,boundaries[1:])):
            split.append({**cell,'bbox':a.model_copy(update={'y0':top,'y1':bottom}),'row_fragment':index,'words':[]})
    result=split
    if len(result)>MAX_CELLS:raise ValueError('S2A_CELL_CAP')
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


# These aliases apply only to a corroborated left-hand weekday axis.
SHORT_DAYS={'mo':1,'tu':2,'we':3,'th':4,'fr':5,'sa':6,'su':7}
ROLE_LINE=re.compile(r'^(?:room|venue|location|lecturer|instructor|teacher|staff|group|section|cohort|timezone|dr[.]?|prof[.]?)\b',re.I)
NON_SESSION=re.compile(r'^(?:free(?: time| period)?|lunch(?: break)?|break|holiday|no class(?:es)?|office hours)\s*$',re.I)


def row_header_text(cell):
    # A printed date can share the weekday cell. No unrelated row text is
    # discarded, and lexical dates are normalized by the existing field parser.
    raw=cell['text'].strip();dates=list(DATE_RE.finditer(raw))
    remaining=DATE_RE.sub('',raw).strip(' ,;:()')
    standard=DAY_RE.fullmatch(remaining)
    short=re.sub(r'\s+','',remaining).lower().rstrip('.')
    valid=len(dates)<=1 and (standard is not None or short in SHORT_DAYS or (len(dates)==1 and not remaining))
    return remaining,valid


def weekday_cells(pagecells):
    left=[c for c in pagecells if c['col']==0 and not c['ambiguous']]
    text={id(c):row_header_text(c) for c in left}
    short=[c for c in left if text[id(c)][1] and re.sub(r'\s+','',text[id(c)][0]).lower().rstrip('.') in SHORT_DAYS]
    values=[SHORT_DAYS[re.sub(r'\s+','',text[id(c)][0]).lower().rstrip('.')] for c in sorted(short,key=lambda c:c['row'])]
    corroborated=len(values)>=3 and len(values)==len(set(values)) and values==sorted(values)
    result={id(c):None for c in left if text[id(c)][1] and (DAY_RE.fullmatch(text[id(c)][0]) or not text[id(c)][0])}
    if corroborated:result.update({id(c):SHORT_DAYS[re.sub(r'\s+','',text[id(c)][0]).lower().rstrip('.')] for c in short})
    return result


def time_header(cell):
    raw=cell['text'].strip()
    if raw.endswith('.') and not raw.endswith('..'):raw=raw[:-1].rstrip()
    exact=TIME_RE.fullmatch(raw)
    if exact:return exact
    ranges=list(TIME_RE.finditer(raw))
    if len(ranges)!=1:return None
    match=ranges[0];prefix=raw[:match.start()].strip()
    if not re.fullmatch(r'\d{1,2}',prefix) or raw[match.end():].strip():return None
    # A period number is metadata only when its own baseline is above the range.
    before=[];clock=[];cursor=0
    for word in cell.get('words',[]):
        end=cursor+len(word.text.strip())
        if end<=match.start():before.append(word)
        elif cursor<match.end():clock.append(word)
        cursor=end+1
    if before and clock and max(w.bbox.y1 for w in before)<min(w.bbox.y0 for w in clock):return match
    return None


def name_title(cell,pagecells,days):
    if cell['col']==0 or cell['ambiguous']:return None
    if not days or not any(c['col']==0 and c['text'].strip() and c['row']==cell['row'] and c['end_col']<=cell['col'] for c in pagecells):return None
    if not any(c['row']<cell['row'] and c['col']>=cell['col'] and c['end_col']<=cell['end_col'] and time_header(c) for c in pagecells):return None
    lines=[]
    for word in cell['words']:
        if not lines or abs(lines[-1][0].bbox.y0-word.bbox.y0)>max(2,(word.bbox.y1-word.bbox.y0)*.4):lines.append([])
        lines[-1].append(word)
    title=[];previous=None
    for line in lines:
        if previous and min(w.bbox.y0 for w in line)-max(w.bbox.y1 for w in previous)>max(w.bbox.y1-w.bbox.y0 for w in previous):break
        text=' '.join(w.text.strip() for w in line)
        if ROLE_LINE.match(text) or TIME_RE.search(text) or DAY_RE.fullmatch(text.strip()) or CLASS_RE.fullmatch(text.strip()):break
        if NON_SESSION.fullmatch(text.strip()):return None
        if not re.search('[A-Za-z]',text):break
        title.extend(w.text.strip() for w in line);previous=line
    raw=re.sub(r'^(?:course\s+name|subject)\s*:\s*','',' '.join(title),flags=re.I)
    if subject_matches(raw) or not re.search(r'[A-Za-z]{2,}',raw) or len(raw)>200 or re.fullmatch(r'[A-Za-z_-]*\d+[A-Za-z0-9_-]*',raw):return None
    return raw


def ruled_graph(packet,supplement):
    if not isinstance(supplement,RuledEvidence):supplement=RuledEvidence.model_validate(supplement)
    validate_binding(packet,supplement)
    settings=development_settings(profile_id=PROFILE)
    extras=list(word_spans(packet,supplement)); derived=[]; allgroups=[];issues=[];cellmeta=[];budget=[0]
    for page in supplement.pages:
        pagecells,diagnostics=cells(page,budget);issues.extend(diagnostics)
        day_axis=weekday_cells(pagecells)
        titles={id(c):title for c in pagecells if (title:=name_title(c,pagecells,day_axis))}
        courses=[c for c in pagecells if c['col']>0 and (subject_matches(c['text']) or id(c) in titles)]
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
        # Name-only cells previously vanished into the non-grid fallback. Add a
        # group over the unchanged source text, never a fabricated course code.
        named_ids={aliases[derived_cells[id(c)].evidence_id]:c for c in courses if id(c) in titles}
        groups=[g for g in rawgraph.groups if not set(g.evidence_ids)&set(named_ids)]
        spans={span.evidence_id:span for span in rawgraph.evidence}
        for sid,c in named_ids.items():
            span=spans[sid];raw=titles[id(c)];a=span.raw_text.index(raw)
            nodes=[_node(packet.source,span,'course_name',raw,a,a+len(raw),Context())]
            # Explicit field-role lines remain separate from the wrapped title.
            for match in re.finditer(r'\b(?:room|venue|location|lecturer|instructor|group|section|cohort|timezone)\s*[:=-]?',span.raw_text,re.I):
                stop=next((m.start() for m in re.finditer(r'\b(?:room|venue|location|lecturer|instructor|group|section|cohort|timezone)\s*[:=-]?',span.raw_text[match.end():],re.I)),None)
                end=match.end()+stop if stop is not None else len(span.raw_text)
                nodes.extend(n for n in _extract(packet.source,span,match.start(),end,Context()) if n.field!='course_code')
            groups.append(domain_group(EventGroup(group_id='s2a-name-'+digest({'cell':sid,'title':raw}),evidence_ids=(sid,),domains={}),[refs(n,span,c['words']) for n in nodes],('SOURCE_NAME_ONLY_CELL',)))
        rawgraph=rawgraph.model_copy(update={'groups':tuple(groups)})
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
            days=[c for c in pagecells if c['row']==cell['row'] and c['end_col']<=cell['col'] and id(c) in day_axis]
            times=[c for c in pagecells if c['row']<cell['row'] and c['col']>=cell['col'] and c['end_col']<=cell['end_col'] and time_header(c)]
            unsupported_axis=[c for c in pagecells if c['row']<cell['row'] and
                c['col']>=cell['col'] and c['end_col']<=cell['end_col'] and
                not subject_matches(c['text']) and not time_header(c) and
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
                if day_axis[id(days[0])] is None:
                    day_nodes=[n for n in _extract(packet.source,h,0,len(h.raw_text),Context()) if n.field in {'day_of_week','date'}]
                else:
                    day_nodes=[_node(packet.source,h,'day_of_week',h.raw_text,0,len(h.raw_text),Context(),value=day_axis[id(days[0])])]+[n for n in _extract(packet.source,h,0,len(h.raw_text),Context()) if n.field=='date']
                nodes.extend(refs(n.model_copy(update={'shared':True,'transformations':n.transformations+('s2a-corroborated-weekday-axis',)}),h,days[0]['words']) for n in day_nodes)
            else:
                diagnostics.append('DAY_HEADER_OWNERSHIP_UNRESOLVED')
                unreadable=[c for c in pagecells if c['col']==0 and c['row']==cell['row'] and c['text'].strip()]
                if len(unreadable)==1 and day_axis:
                    h=derived_cells[id(unreadable[0])];n=_node(packet.source,h,'day_of_week',h.raw_text,0,len(h.raw_text),Context())
                    nodes.extend(refs(v.model_copy(update={'shared':True}),h,unreadable[0]['words']) for v in _extract(packet.source,h,0,len(h.raw_text),Context()) if v.field=='date')
                    nodes.append(refs(n.model_copy(update={'value':None,'state':FieldState.UNRESOLVED,'shared':True,'transformations':n.transformations+('s2a-unreadable-row-header-no-day-inference',)}),h,unreadable[0]['words']))
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
            if cell['ambiguous'] or (id(cell) not in titles and len(subject_matches(cell['text']))!=1):valid=False
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
            'row_fragment':c.get('row_fragment',0),'ambiguous':c['ambiguous'],'text':c['text']} for c in pagecells)
    if len(allgroups)>MAX_GROUPS:raise ValueError('S2A_GROUP_CAP')
    base=build_graph(packet,settings)
    evidence={s.evidence_id:s for s in (*base.evidence,*derived,*extras)}
    # Canonicalized original lines and supplementary words/cells coexist.
    graph=base.model_copy(update={'evidence':tuple(evidence[k] for k in sorted(evidence)),
        'groups':tuple(sorted(allgroups,key=lambda g:g.group_id)),
        'diagnostics':base.diagnostics+('S2A_APPLICATION_ONLY',)})
    layout={'profile':PROFILE,'version':'s2a.3','config_digest':CONFIG_HASH,'cells':cellmeta,'issues':issues,'association_operations':budget[0],
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
    from backend.review.source_routing import source_conflicts
    conflicts=source_conflicts(graph)
    return tuple(assess_event(run,e,findings,source_graph=graph,source_findings=conflicts) for e in events) or (assess_event(run,None,findings,source_graph=graph,source_findings=conflicts),)
