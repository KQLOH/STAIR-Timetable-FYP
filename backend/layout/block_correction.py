"""RT4 adapter for the opt-in S2b application graph; raw evidence is immutable."""
import re
from backend.services.ocr_correction import correct_field, established_formats, signature, VERSION
from backend.models.event import FieldState


def slots(words):
    from backend.layout.block_grid import _lines, _box, ROLE_LABEL
    from backend.reconstruction.candidates import TIME_RE
    lines=_lines(words)
    if not 4<=len(lines)<=12:return {}
    boxes=[_box(line) for line in lines]
    if any(a.y1>b.y0 for a,b in zip(boxes,boxes[1:])):return {}
    texts=[' '.join(w.text.strip() for w in line) for line in lines]
    starts=[];cursor=0
    for text in texts:starts.append(cursor);cursor+=len(text)+1
    marker=re.compile(r'^([A-Z0-9 -]{3,20})\s*[\(\[\{]\s*([A-Z017])\s*[\)\]\}]$',re.I)
    count=1;first=texts[0];match=marker.fullmatch(first)
    if not match:count=2;first=' '.join(texts[:2]);match=marker.fullmatch(first)
    if not match:return {}
    for index in range(count+2,len(lines)):
        raw_time=' '.join(texts[index:]);staff=' '.join(texts[count+1:index])
        if not (TIME_RE.fullmatch(raw_time) or re.fullmatch(r'[0-9OIlLSBZ]{1,2}:[0-9OIlLSBZ]{2} [AP]M\s*[-\u2013]\s*[0-9OIlLSBZ]{1,2}:[0-9OIlLSBZ]{2} [AP]M',raw_time)):continue
        if ROLE_LABEL.search(staff) or not re.fullmatch(r"[^\W\d_]+(?:[ '\u2019-][^\W\d_]+){0,8}",staff):continue
        result={}
        for field,raw,start in [('course_code',match[1].strip(),match.start(1)),('session_type',match[2],match.start(2)),
                ('location',texts[count],starts[count]),('lecturer',staff,starts[count+1]),('time_range',raw_time,starts[index])]:
            # Locate exact token overlap, including separately wrapped source lines.
            end=start+len(raw);selected=[];at=0
            for line in lines:
                for word in line:
                    stop=at+len(word.text.strip())
                    if min(stop,end)>max(at,start):selected.append(word)
                    at=stop+1
            result[field]={'raw':raw,'start':start,'words':selected}
        return result
    return {}


def page_witnesses(page):
    from backend.layout.block_grid import select_region_words, MARKER
    witnesses={'location':[],'course_code':[]}
    for region in page.regions:
        if region.boundary_status!='RECTANGULAR':continue
        words,_=select_region_words([w for w in page.words if w.region_id==region.region_id])
        fields=slots(words)
        if not fields or not MARKER.fullmatch(fields['course_code']['raw']+' ('+fields['session_type']['raw']+')'):continue
        for field in witnesses:
            value=fields[field];conf=[w.confidence for w in value['words']]
            if not signature(value['raw']) or not conf or any(c is None for c in conf):continue
            if field=='location' and re.fullmatch(r'[A-Z]{2,}\d{4}[A-Z]?',re.sub(r'\s+','',value['raw']).upper()):continue
            witnesses[field].append({'text':value['raw'],'region_id':region.region_id,'confidence':min(conf),
                                     'word_ids':[w.evidence_id for w in value['words']]})
    return witnesses


def apply_corrections(packet,page,region,words,span,nodes,axis,outside,witnesses):
    from backend.layout.block_grid import (_field_node,_day_context,_geometry_span,explicit_boundary_clocks)
    from backend.services.normalizer import normalize_class_type, normalize_time
    from backend.reconstruction.candidates import TIME_RE
    fields=slots(words)
    if not fields:return nodes,[]
    _,context,issues=_day_context(packet,page,region,outside,axis)
    context_ok=(region.boundary_status=='RECTANGULAR' and axis['supported'] and not issues and
        all(any(n.field==f and n.state==FieldState.PRESENT for n in context) for f in ('date','day_of_week')) and
        bool(_geometry_span(region,axis) or explicit_boundary_clocks(region,page,axis)))
    variants=[]
    for route in sorted({w.capture_route for w in page.words if w.region_id==region.region_id}):
        variant=slots([w for w in page.words if w.region_id==region.region_id and w.capture_route==route])
        if variant:variants.append(variant)
    records=[]
    for field,value in fields.items():
        if field=='lecturer':continue
        formats=established_formats(witnesses.get(field,[]),region.region_id)
        alternative_fields=[v[field] for v in variants if field in v and
            {w.evidence_id for w in v[field]['words']}!={w.evidence_id for w in value['words']}]
        alternatives=[v['raw'] for v in alternative_fields]
        supporting=[v['raw'] for v in alternative_fields if v['words'] and all(w.confidence is not None and w.confidence>=.75 for w in v['words'])]
        result=correct_field(field,value['raw'],formats=formats,alternatives=supporting)
        result['alternative_readings']=alternatives
        if result['status']=='UNCHANGED':continue
        if not context_ok and result['status']=='SUPPORTED':
            result.update(status='AMBIGUOUS',source_supported=False,rule='field-layout-context-not-established')
        source_words=value['words'];scores=[w.confidence for w in source_words]
        result.update(region_id=region.region_id,page=page.page,bbox=region.bbox.model_dump(mode='json'),
                      original_confidence=scores,source_word_ids=[w.evidence_id for w in source_words],
                      format_evidence=[w for ws in formats.values() for w in ws],
                      alternative_evidence=[{'raw':v['raw'],'word_ids':[w.evidence_id for w in v['words']],
                                            'confidence':[w.confidence for w in v['words']]} for v in alternative_fields])
        refs={w.evidence_id for w in source_words}
        refs.update(w.evidence_id for v in alternative_fields for w in v['words'])
        refs.update(i for ws in formats.values() for w in ws for i in w['word_ids'])
        supported=result['status']=='SUPPORTED'
        if field=='time_range':
            match=TIME_RE.fullmatch(result['suggested'] or '')
            replacements=[('start_time',normalize_time(match[1]) if match else None),('end_time',normalize_time(match[2]) if match else None)]
        else:
            normalized=result['suggested']
            if field=='session_type' and normalized:normalized=normalize_class_type(normalized)
            replacements=[(field,normalized)]
        for name,normalized in replacements:
            node=_field_node(packet,span,words,name,value['raw'],start=value['start'],value=normalized if supported else None)
            node=node.model_copy(update={'value':normalized if supported else None,'state':FieldState.PRESENT if supported else FieldState.UNRESOLVED,
                'evidence_ids':tuple(sorted(set(node.evidence_ids)|refs)),
                'transformations':node.transformations+(VERSION,result['status'],result['rule'],'rt4-owner-region:'+region.region_id)})
            nodes=[n for n in nodes if n.field!=name]+[node]
        if field=='location' and supported:
            # The supported numeric room interpretation establishes the role
            # of the following source name; copy it without character repairs.
            if not any(n.field=='lecturer' and n.state==FieldState.PRESENT for n in nodes):
                staff=fields['lecturer']
                node=_field_node(packet,span,words,'lecturer',staff['raw'],start=staff['start'],value=staff['raw'])
                nodes=[n for n in nodes if n.field not in {'lecturer','section','cohort_id','course_name'}]+[node]
        records.append(result)
    return nodes,records
