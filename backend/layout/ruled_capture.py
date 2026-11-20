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


def visible_grid(image, words=()):
    import numpy as np
    # Scan cost is bounded by MAX_PIXELS, independent of text/candidate search.
    dark=np.asarray(image.convert('L'))<150
    def longest(axis):
        result=np.zeros(dark.shape[1-axis], dtype='int32')
        current=result.copy()
        for row in (dark if axis==0 else dark.T):
            current=np.where(row,current+1,0); result=np.maximum(result,current)
        return result
    vertical=longest(0)>=max(60,image.height*.12)
    xs=_runs(vertical)
    edges=np.diff(np.concatenate(([False],vertical,[False])).astype('int8'))
    widths=np.flatnonzero(edges==-1)-np.flatnonzero(edges==1)
    thickness=float(np.median(widths)) if len(widths) else 1.0
    tolerance=max(3,int(np.ceil(thickness)))
    inset=max(3,int(np.ceil(thickness/2))+2)
    horizontal=longest(1)
    major=horizontal>=max(120,image.width*.55)
    if len(xs)>=3:
        # A rule across the course area alone is local when a weekday cell
        # spans both sessions; it must not create a new blank weekday row.
        left,right=xs[0],xs[-1]
        major=major & (dark[:,left:right+1].mean(axis=1)>=.96) & dark[:,max(0,left-2):left+3].any(axis=1) & dark[:,max(0,right-2):right+3].any(axis=1)
    ys=_runs(major)
    if len(xs)>32 or len(ys)>32:raise ValueError('S2A_SEPARATOR_CAP')
    if len(xs)<3 or len(ys)<3:
        return {'status':'NO_SUPPORTED_GRID','xs':[],'ys':[],'vertical_coverage':[]}
    # Full-width horizontal separators define a single axis-aligned table.
    from statistics import median
    glyph_height=median([w.bbox.y1-w.bbox.y0 for w in words]) if words else 0
    def longest_strip_run(column):
        best=run=0
        for value in column:
            run=run+1 if value else 0;best=max(best,run)
        return best
    coverage=[];raw_coverage=[]
    for a,b in zip(ys,ys[1:]):
        raw_row=[];row=[]
        for x in xs:
            column=dark[a+3:b-2,max(0,x-2):x+3].any(axis=1).copy()
            raw=float(column.mean()) if len(column) else 0.0;raw_row.append(round(raw,4))
            # A fully supported rule remains closed even if OCR overlaps it.
            # For uncertain strips, retain only ink outside recorded text boxes.
            if .05<raw<.96 and longest_strip_run(column)<=max(8,glyph_height*1.5):
                for word in words:
                    q=word.bbox
                    if q.x0<x+3 and q.x1>x-2:
                        lo=max(0,int(q.y0)-a-3);hi=max(0,min(len(column),int(q.y1)-a-2))
                        column[lo:hi]=False
            row.append(round(float(column.mean()),4) if len(column) else 0.0)
        raw_coverage.append(raw_row);coverage.append(row)
    # Local rules must meet measured column boundaries. Text underlines that
    # end inside a cell cannot create a new session boundary.
    local=[];minimum=max(40,min(b-a for a,b in zip(xs,xs[1:]))*.8)
    for y in range(ys[0]+3,ys[-1]-2):
        if horizontal[y]<minimum or any(abs(y-v)<=tolerance for v in ys):continue
        padded=np.concatenate(([False],dark[y],[False])).astype('int8')
        edges=np.diff(padded);starts=np.flatnonzero(edges==1);ends=np.flatnonzero(edges==-1)-1
        for left,right in zip(starts,ends):
            if right-left<minimum:continue
            near_left=[x for x in xs if abs(x-int(left))<=tolerance];near_right=[x for x in xs if abs(x-int(right))<=tolerance]
            if len(near_left)!=1 or len(near_right)!=1:continue
            x0,x1=near_left[0],near_right[0]
            previous=next((v for v in reversed(local) if v['x0']==x0 and v['x1']==x1 and y-v['last_y']<=3),None)
            if previous:previous.update(last_y=y,y=round((previous['first_y']+y)/2))
            else:local.append({'y':y,'first_y':y,'last_y':y,'x0':x0,'x1':x1})
            if len(local)>256:raise ValueError('S2A_LOCAL_SEPARATOR_CAP')
    return {'status':'MEASURED','xs':xs,'ys':ys,'vertical_coverage':coverage,'raw_vertical_coverage':raw_coverage,
            'local_horizontal_rules':[{k:v[k] for k in ('y','x0','x1')} for v in local],
            'rule_tolerance':tolerance,'rule_inset':inset,'pixel_threshold':150,'separator_policy':'closed major rules; text-box-aware open strips; local horizontal rules bound to measured columns'}



def recover_empty_header(image,data,runtime,recognizer):
    """One source-coordinate retry when page OCR contains no header clocks.

    Never replace or rank competing OCR readings. This is application S2a only;
    other readers and registered baseline acquisition are unchanged.
    """
    grid=visible_grid(image)
    if grid['status']!='MEASURED':return data,runtime
    x0,x1=grid['xs'][0],grid['xs'][-1];y0,y1=grid['ys'][0],grid['ys'][1]
    header_words=[]
    for i,text in enumerate(data['text']):
        if not any(c.isalnum() for c in str(text)):continue
        cx=float(data['left'][i])+float(data['width'][i])/2;cy=float(data['top'][i])+float(data['height'][i])/2
        if x0<=cx<=x1 and y0<=cy<=y1:header_words.append(str(text))
    import re
    from backend.reconstruction.candidates import TIME_RE
    if any(re.search(r'[:.]\s*\d|\?{2,}|TBA|UNKNOWN|UNREADABLE',text,re.I) for text in header_words) or TIME_RE.search(' '.join(header_words)):return data,runtime
    inset=grid.get('rule_inset',3)
    crop=(x0+inset,y0+inset,x1-inset,y1-inset)
    if crop[2]-crop[0]<10 or crop[3]-crop[1]<10:return data,runtime
    from backend.services.ocr_runtime import OCRRuntimeError
    trace={'kind':'original-pixel-empty-header-crop','crop':list(crop),'replaces_existing_words':False,'added_words':0}
    try:extra,details=recognizer(image.crop(crop))
    except OCRRuntimeError as error:return data,{**runtime,'s2a_header_retry':{**trace,'error_code':error.code}}
    merged={key:list(value) for key,value in data.items() if isinstance(value,(list,tuple))}
    block=max(data.get('block_num',[0]),default=0)+1
    for i,text in enumerate(extra['text']):
        if not str(text).strip():continue
        left,top,width,height=[float(extra[k][i]) for k in ('left','top','width','height')]
        if not (0<=left<left+width<=crop[2]-crop[0] and 0<=top<top+height<=crop[3]-crop[1]):continue
        global_left=left+crop[0];global_top=top+crop[1]
        duplicate=False
        for j,old in enumerate(data['text']):
            if str(old).strip().casefold()!=str(text).strip().casefold():continue
            a,b=float(data['left'][j]),float(data['top'][j]);w,h=float(data['width'][j]),float(data['height'][j])
            overlap=max(0,min(a+w,global_left+width)-max(a,global_left))*max(0,min(b+h,global_top+height)-max(b,global_top))
            if overlap>=.5*min(w*h,width*height):duplicate=True;break
        if duplicate:continue
        if sum(bool(str(t).strip()) for t in merged['text'])>=MAX_WORDS:raise ValueError('S2A_WORD_CAP')
        for key in merged:
            value=extra[key][i] if key in extra and i<len(extra[key]) else -1 if key=='conf' else 0
            if key=='left':value=left+crop[0]
            elif key=='top':value=top+crop[1]
            elif key=='block_num':value=int(value)+block
            merged[key].append(value)
        trace['added_words']+=1
    trace['runtime']={k:details.get(k) for k in ('engine_version','wrapper_version','config','language')}
    return merged,{**runtime,'s2a_header_retry':trace}


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
        transform=transform,runtime=runtime,words=tuple(words),grid=visible_grid(image,words))
