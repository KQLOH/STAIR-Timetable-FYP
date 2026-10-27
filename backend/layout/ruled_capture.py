"""Bounded pixel measurements and original OCR tokens, explicitly opt-in only."""
from backend.models.evidence import BoundingBox
from backend.reconstruction.models import digest
from backend.services.ocr_runtime import confidence
from backend.layout.ruled_models import (Word, RuledPage, MAX_PIXELS, MAX_WORDS,
    MAX_TOTAL_PIXELS, MAX_PAGES)


def check_image(image, collector):
    if image.width*image.height > MAX_PIXELS:
        raise ValueError('S2A_PIXEL_CAP')
    if len(collector)>=MAX_PAGES or sum(p.width*p.height for p in collector)+image.width*image.height>MAX_TOTAL_PIXELS:
        raise ValueError('S2A_PAGE_OR_TOTAL_PIXEL_CAP')


def _runs(values):
    result=[]; active=[]
    for i, yes in enumerate(values):
        if yes: active.append(i)
        elif active: result.append(round(sum(active)/len(active)));active=[]
    if active:result.append(round(sum(active)/len(active)))
    return result


def visible_grid(image):
    import numpy as np
    # Scan cost is bounded by MAX_PIXELS, independent of text/candidate search.
    dark=np.asarray(image.convert('L'))<150
    def longest(axis):
        result=np.zeros(dark.shape[1-axis], dtype='int32')
        current=result.copy()
        for row in (dark if axis==0 else dark.T):
            current=np.where(row,current+1,0); result=np.maximum(result,current)
        return result
    xs=_runs(longest(0)>=max(60,image.height*.12))
    ys=_runs(longest(1)>=max(120,image.width*.55))
    if len(xs)>32 or len(ys)>32:raise ValueError('S2A_SEPARATOR_CAP')
    if len(xs)<3 or len(ys)<3:
        return {'status':'NO_SUPPORTED_GRID','xs':[],'ys':[],'vertical_coverage':[]}
    # Full-width horizontal separators define a single axis-aligned table.
    coverage=[]
    for a,b in zip(ys,ys[1:]):
        coverage.append([round(float(dark[a+3:b-2,max(0,x-2):x+3].any(axis=1).mean()),4)
                         if b-a>6 else 0 for x in xs])
    return {'status':'MEASURED','xs':xs,'ys':ys,'vertical_coverage':coverage,
            'pixel_threshold':150,'separator_policy':'full-width rows; measured vertical continuity'}


def capture(source_id, image, page, adapter, data, runtime, regions, transform):
    parents={}
    for index,key in enumerate(sorted({(data['block_num'][i],data['par_num'][i],data['line_num'][i])
                                      for i,t in enumerate(data['text']) if str(t).strip()})):
        r=regions[index]
        r.evidence_id='s2a-line-'+digest({'source':source_id,'page':page,'key':key,
                                       'text':r.text,'bbox':r.bbox.model_dump()})
        parents[key]=r.evidence_id
    words=[]
    for i,t in enumerate(data['text']):
        if not str(t).strip():continue
        key=tuple(data[k][i] for k in ('block_num','par_num','line_num'))
        x,y,w,h=(float(data[k][i]) for k in ('left','top','width','height'))
        if w<=0 or h<=0:raise ValueError('S2A_INVALID_WORD_BOX')
        seed={'source':source_id,'page':page,'parent':parents[key],'text':str(t),'box':[x,y,x+w,y+h]}
        words.append(Word(evidence_id='s2a-word-'+digest(seed),text=str(t),
            bbox=BoundingBox(x0=x,y0=y,x1=x+w,y1=y+h,coordinate_space='PIXELS'),
            confidence=confidence(data.get('conf',[None]*len(data['text']))[i]),
            parent_line_id=parents[key],block=key[0],paragraph=key[1],line=key[2],reading_order=i))
        if len(words)>MAX_WORDS:raise ValueError('S2A_WORD_CAP')
    return RuledPage(page=page,width=image.width,height=image.height,route=adapter,
        transform=transform,runtime=runtime,words=tuple(words),grid=visible_grid(image))
