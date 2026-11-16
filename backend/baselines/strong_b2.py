"""B2-strong-v2: independent template rules, never P1 decoding or OCR repair.

Development implementation of retained Chapter 3 geometry rules. Supports
axis-aligned colored cells and unmerged header intersections. Unknown merged
spans in unruled/uncolored documents fail closed, not guessed from text gaps.
"""
from dataclasses import dataclass
from statistics import median
from collections import defaultdict
from uuid import uuid5,NAMESPACE_URL
import re

from backend.models.event import (EventContent, FieldObservation, FieldState,
    EventRevision, CanonicalEvent, CanonicalTimetable, EventIdentity, ExtractionStatus)
from backend.models.evidence import EvidenceSpan, BoundingBox, ParseIssue
from backend.services.normalizer import normalize_time
from backend.reconstruction.models import digest

CONFIG={'id':'B2-strong-v2','version':'2.1.2','row_height_factor':0.45,
        'header_geometry':'ordered-midpoints','cell_assignment':'maximum-overlap',
        'ocr':'shared-full-page-word-evidence','correction':'none',
        'anchor_rule':'heading-role-and-completed-session-boundary-v1'}
CODE=re.compile(r'\b[A-Z]{2,6}-?\d{3,4}[A-Z]?\b',re.I)
TYPE=re.compile(r'\(([LTP])\)|\b(Lecture|Tutorial|Practical)\b',re.I)
CLOCK=re.compile(r'^(?:[01]?\d|2[0-3]):[0-5]\d$')
RANGE=re.compile(r'(\d{1,2}:\d{2}\s*(?:AM|PM))\s*[-–]\s*(\d{1,2}:\d{2}\s*(?:AM|PM))',re.I)
DAYS={'mon':1,'tue':2,'wed':3,'thu':4,'fri':5,'sat':6,'sun':7}


@dataclass(frozen=True)
class Word:
    id:str
    text:str
    box:tuple
    confidence:float|None=None


def center(word,axis):return (word.box[axis]+word.box[axis+2])/2


def lines(words):
    """Scale-relative vertical grouping, deterministic geometry/ID ties."""
    if not words:return []
    height=median(w.box[3]-w.box[1] for w in words)
    groups=[]
    for word in sorted(words,key=lambda w:(center(w,1),w.box[0],w.id)):
        target=next((g for g in groups if abs(center(word,1)-median(center(w,1) for w in g))<=height*0.45),None)
        if target is None:groups.append([word])
        else:target.append(word)
    return [sorted(g,key=lambda w:(w.box[0],w.id)) for g in groups]


def bands(anchors,limit):
    positions=sorted({position for position,_ in anchors})
    return {p:((positions[i-1]+p)/2 if i else 0,
               (p+positions[i+1])/2 if i+1<len(positions) else limit)
            for i,p in enumerate(positions)}


def course_anchors(texts):
    """Suppress only an identifier in an existing heading's venue slot.

    Regex shape alone cannot promote a later venue line into a course.
    Other code-shaped headings are retained, even if a previous OCR time is
    damaged. A missing time must not silently suppress subsequent sessions.
    """
    result=[]
    for i,text in enumerate(texts):
        code=CODE.search(text)
        named=not code and (TYPE.search(text) or re.match(r'^Course\s*:',text,re.I))
        if not code and not named:continue
        if named and not re.search(r'[A-Za-z]{3}',TYPE.sub('',text)):continue
        venue_slot=result[-1]+1 if result else None
        if venue_slot is not None and venue_slot<len(texts) and TYPE.fullmatch(texts[venue_slot]):venue_slot+=1
        if i==venue_slot and code and CODE.fullmatch(text.strip()) and not TYPE.search(text):continue
        result.append(i)
    return result


class WordCollector(list):
    """Existing extraction hook: saves original word boxes, no extra OCR calls."""
    max_pages=4
    def check_image(self,image):
        if image.width*image.height>8_000_000 or len(self)>=self.max_pages:
            raise ValueError('STRONG_B2_RESOURCE_LIMIT')
    def capture_page(self,source_id,image,page,adapter,data,runtime,line_regions,transform):
        words=[]
        for i,text in enumerate(data['text']):
            if not str(text).strip():continue
            x,y,w,h=(float(data[k][i]) for k in ('left','top','width','height'))
            if w<=0 or h<=0:continue
            conf=float(data['conf'][i]);conf=conf/100 if conf>=0 else None
            words.append(Word(f'{source_id}:p{page}:w{i}',str(text).strip(),(x,y,x+w,y+h),conf))
            if len(words)>4096:raise ValueError('STRONG_B2_WORD_CAP')
        colored=colored_cells(image)
        self.append({'page':page,'width':image.width,'height':image.height,'words':words,
            'cells':colored or ruled_cells(image),'geometry':'colored' if colored else 'observed-rules',
            'runtime':runtime,'transform':transform})


def ruled_cells(image):
    """Uncolored cells bounded by observed rules; missing dividers yield spans."""
    import numpy as np
    a=np.asarray(image.convert('RGB')).astype('int16')
    dark=(a.max(2)-a.min(2)<20)&(a.max(2)<200)
    def peaks(values):
        groups=[]
        for n in values:
            if groups and n==groups[-1][-1]+1:groups[-1].append(int(n))
            else:groups.append([int(n)])
        return [sum(g)/len(g) for g in groups]
    ys=peaks(np.flatnonzero(dark.sum(1)>=image.width*.4))
    cells=[]
    for y0,y1 in zip(ys,ys[1:]):
        if y1-y0<10:continue
        xs=peaks(np.flatnonzero(dark[int(y0)+1:int(y1)].mean(0)>=.8))
        for x0,x1 in zip(xs,xs[1:]):
            if x1-x0>10:cells.append((x0,y0,x1,y1))
    return cells


def colored_cells(image):
    """Connected colored fill components with observed white separators.

    Text holes remain holes; connected background joins their surrounding runs.
    No reference labels or proposed-method detection code are used.
    """
    import numpy as np
    a=np.asarray(image.convert('RGB')).astype('int16')
    mask=(a.max(2)-a.min(2)>35)&(a.max(2)<245)
    parents=[];boxes=[];previous=[]
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    for y,row in enumerate(mask):
        indices=np.flatnonzero(row);current=[]
        if not len(indices):previous=[];continue
        cuts=np.flatnonzero(np.diff(indices)>1)
        starts=[int(indices[0])]+[int(indices[i+1]) for i in cuts]
        ends=[int(indices[i])+1 for i in cuts]+[int(indices[-1])+1]
        for x0,x1 in zip(starts,ends):
            hits={root(i) for lo,hi,i in previous if min(x1,hi)>max(x0,lo)}
            if hits:
                index=min(hits)
                for other in hits:
                    if other!=index:
                        parents[other]=index
                        boxes[index]=[min(boxes[index][0],boxes[other][0]),min(boxes[index][1],boxes[other][1]),max(boxes[index][2],boxes[other][2]),y+1]
                boxes[index][0]=min(boxes[index][0],x0);boxes[index][2]=max(boxes[index][2],x1);boxes[index][3]=y+1
            else:
                index=len(parents);parents.append(index);boxes.append([x0,y,x1,y+1])
                if len(parents)>200000:raise ValueError('STRONG_B2_COMPONENT_CAP')
            current.append((x0,x1,index))
        previous=current
    return sorted([tuple(b) for i,b in enumerate(boxes) if root(i)==i and b[2]-b[0]>=image.width*.035 and b[3]-b[1]>=image.height*.025])



def predict_page(source,page):
    words=page['words'];evidence=[];issues=[];events=[]
    for w in words:
        evidence.append(EvidenceSpan(evidence_id=w.id,source_document_id=source.source_document_id,
            route='ocr',raw_text=w.text,page=page['page'],confidence=w.confidence,
            bbox=BoundingBox(x0=w.box[0],y0=w.box[1],x1=w.box[2],y1=w.box[3],coordinate_space='PIXELS'),
            transformations=('B2-strong-v2:unaltered-OCR-word',)))
    daywords=[w for w in words if w.text.lower()[:3] in DAYS and re.fullmatch(r'Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?',w.text,re.I)]
    if len(daywords)<2:
        issues.append(ParseIssue(code='B2_STRONG_DAY_AXIS_INSUFFICIENT'))
        return events,evidence,issues
    orientation=0 if max(center(w,0) for w in daywords)-min(center(w,0) for w in daywords)>max(center(w,1) for w in daywords)-min(center(w,1) for w in daywords) else 1
    other=1-orientation
    dayanchors=[(center(w,orientation),w) for w in daywords]
    daybands=bands(dayanchors,page['width'] if orientation==0 else page['height'])
    clocks=[w for w in words if CLOCK.fullmatch(w.text) and (center(w,1)<min(center(d,1) for d in daywords) if orientation==1 else center(w,0)<min(center(d,0) for d in daywords))]
    # Double-row start/end axes: choose the outer header band, never mix labels.
    if clocks:
        edge=min(center(w,orientation) for w in clocks)
        h=median(w.box[3]-w.box[1] for w in clocks)
        clocks=[w for w in clocks if abs(center(w,orientation)-edge)<=h*.45]
    ordered=sorted(clocks,key=lambda w:(center(w,other),w.id))
    axis=[];previous=-1
    for w in ordered:
        hour,minute=map(int,w.text.split(':'));value=hour*60+minute
        if value<previous and hour<12:value+=720
        if value<=previous or value>=1440:continue
        axis.append((center(w,other),value,w));previous=value
    rectangles=list(page.get('cells',[]))
    if not rectangles and len(axis)>=2:
        timebands=bands([(p,w) for p,_,w in axis],page['width'] if other==0 else page['height'])
        for dp,_ in dayanchors:
            for tp,_,_ in axis:
                d0,d1=daybands[dp];t0,t1=timebands[tp]
                rectangles.append((t0,d0,t1,d1) if orientation==1 else (d0,t0,d1,t1))
    # Assign each word to exactly one cell by overlap, nearest center, leftmost.
    owned=defaultdict(list)
    for w in words:
        candidates=[]
        for i,b in enumerate(rectangles):
            area=max(0,min(w.box[2],b[2])-max(w.box[0],b[0]))*max(0,min(w.box[3],b[3])-max(w.box[1],b[1]))
            if area:candidates.append((-area,abs(center(w,0)-(b[0]+b[2])/2),b[0],i))
        if candidates:owned[min(candidates)[-1]].append(w)
    seen=set()
    for index,cellwords in sorted(owned.items()):
        b=rectangles[index];grouped=lines(cellwords)
        # Join a wrapped prefix and numeric suffix only inside one observed cell.
        texts=[' '.join(w.text for w in g) for g in grouped]
        i=0
        while i+1<len(texts):
            if re.fullmatch('[A-Z]{2,6}',texts[i]) and re.match(r'^\d{3,4}\b',texts[i+1]):
                grouped[i:i+2]=[grouped[i]+grouped[i+1]];texts[i:i+2]=[''.join(texts[i:i+2])]
            i+=1
        anchors=course_anchors(texts)
        if not anchors:continue
        for start,end in zip(anchors,anchors[1:]+[len(texts)]):
            chunk=texts[start:end];used=[w for g in grouped[start:end] for w in g];refs=tuple(w.id for w in used)
            vals={};obs={};raw=' '.join(chunk);code=CODE.search(chunk[0])
            def put(field,value,raw_value,ids=refs):
                vals[field]=value;obs[field]=FieldObservation(field_name=field,state=FieldState.PRESENT,raw_value=raw_value,evidence_ids=ids)
            if code:
                local_type=TYPE.search(chunk[0]) or (len(chunk)>1 and TYPE.fullmatch(chunk[1]))
                uncertain_role=(CODE.fullmatch(chunk[0].strip()) and not local_type) or (code.start()>0 and not local_type and not re.match(r'^Course\s*:',chunk[0],re.I))
                if uncertain_role:
                    obs['course_code']=FieldObservation(field_name='course_code',state=FieldState.UNRESOLVED,
                        raw_value=code.group(),evidence_ids=refs,justification='Bare identifier without course-role corroboration')
                    issues.append(ParseIssue(code='B2_STRONG_IDENTIFIER_ROLE_UNRESOLVED',evidence_ids=refs))
                else:put('course_code',code.group().upper(),code.group())
            else:
                name=TYPE.sub('',re.sub(r'^Course\s*:\s*','',chunk[0],flags=re.I)).strip()
                if name:put('course_name',name,chunk[0])
            types=list(TYPE.finditer(raw))
            if types:
                value=types[0].group(1) or types[0].group(2)
                put('session_type',{'l':'Lecture','t':'Tutorial','p':'Practical'}.get(value.lower(),value.title()),types[0].group())
            section=re.search(r'\b(?:Section|Group)\s*:\s*([A-Z0-9-]+)',raw,re.I)
            if section:put('section',section.group(1),section.group())
            for field,pattern in [('teaching_weeks',r'(?<!Excluded )\bWeeks\s*:\s*([0-9, -]+)'),
                                  ('excluded_weeks',r'\bExcluded Weeks\s*:\s*([0-9, -]+)')]:
                match=re.search(pattern,raw,re.I)
                if match:
                    values=[]
                    for part in match.group(1).split(','):
                        limits=[int(v) for v in part.strip().split('-') if v.strip()]
                        if len(limits)==1 and limits[0]>0:values.append(limits[0])
                        elif len(limits)==2 and 0<limits[0]<=limits[1]<=100:values.extend(range(limits[0],limits[1]+1))
                    if values:put(field,tuple(sorted(set(values))),match.group())
            dp,day=min(dayanchors,key=lambda pair:abs(pair[0]-(b[orientation]+b[orientation+2])/2))
            put('day_of_week',DAYS[day.text.lower()[:3]],day.text,(day.id,))
            # Date must share the day band, never borrow a different day's date.
            dates=[w for w in words if re.fullmatch(r'\d{4}-\d{2}-\d{2}',w.text) and daybands[dp][0]<=center(w,orientation)<daybands[dp][1]]
            if len(dates)==1:
                try:put('date',__import__('datetime').date.fromisoformat(dates[0].text),dates[0].text,(dates[0].id,))
                except ValueError:pass
            times=RANGE.findall(raw)
            if len(times)==1:
                for f,t in zip(('start_time','end_time'),times[0]):
                    value=normalize_time(t)
                    if value:put(f,value,t)
            elif len(times)>1:
                issues.append(ParseIssue(code='B2_STRONG_AMBIGUOUS_SESSION_BOUNDARIES',evidence_ids=refs))
            elif len(axis)>=2:
                # Header centers define midpoint slot edges. Only observed
                # neighboring clocks authorize an endpoint; no invented last slot.
                boundaries=[(axis[i-1][0]+axis[i][0])/2 for i in range(1,len(axis))]
                boundaries.insert(0,axis[0][0]-(axis[1][0]-axis[0][0])/2)
                tolerance=median(w.box[3]-w.box[1] for w in words)*2
                for field,edge in [('start_time',b[other]),('end_time',b[other+2])]:
                    n=min(range(len(boundaries)),key=lambda i:abs(boundaries[i]-edge))
                    if abs(boundaries[n]-edge)<=tolerance:
                        value=axis[n][1]
                        put(field,f'{value//60:02d}:{value%60:02d}',axis[n][2].text,(axis[n][2].id,))
            # Four logical roles: course/type, location, lecturer, explicit time.
            remaining=[(i,t) for i,t in enumerate(chunk[1:],1) if not RANGE.search(t) and not re.fullmatch(r'\([LTP]\)',t,re.I)]
            if remaining and obs.get('course_code',FieldObservation(field_name='course_code')).state!=FieldState.UNRESOLVED:
                room=remaining[0][1]
                if re.fullmatch(r'(?:[A-Z]{1,5}\s+)?[A-Z0-9?-]{1,8}|(?:Room|Lab|Hall|Pool)\s+[A-Z0-9? -]+',room,re.I):
                    if re.search(r'\bTBA\b|\?|\bUNKNOWN\b',room,re.I):
                        obs['location']=FieldObservation(field_name='location',state=FieldState.UNRESOLVED,raw_value=room,evidence_ids=refs)
                    else:put('location',room,room)
                    names=[t for _,t in remaining[1:] if not re.search(r'\d{1,2}:\d{2}|\d\s*(?:AM|PM)\b|\b(?:Weeks|Section|Group)\s*:',t,re.I)]
                    if names:put('lecturer',' '.join(names),'\n'.join(names))
            for f in EventContent.model_fields:
                if f not in obs:obs[f]=FieldObservation(field_name=f,state=FieldState.MISSING)
            content=EventContent(**vals);key=digest(content.model_dump(mode='json'))
            if key in seen:
                issues.append(ParseIssue(code='B2_STRONG_DUPLICATE_SUPPRESSED',evidence_ids=refs));continue
            seen.add(key)
            seed=f'{source.source_document_id}:{page["page"]}:{index}:{start}:{key}'
            revision=EventRevision(revision_id=uuid5(NAMESPACE_URL,seed+':revision'),source_document_id=source.source_document_id,method_id=CONFIG['id'],
                configuration_digest=digest(CONFIG),content=content,observations=tuple(obs.values()))
            events.append(CanonicalEvent(candidate_id=f'b2v2-p{page["page"]}-{index}-{start}',identity=EventIdentity(event_id=uuid5(NAMESPACE_URL,seed+':event')),revision=revision,
                extraction_status=ExtractionStatus.EXTRACTED if revision.core_fields_present else ExtractionStatus.PARTIAL))
    return events,evidence,issues


def predict(source,pages):
    events=[];evidence=[];issues=[]
    for page in pages:
        ev,sp,err=predict_page(source,page);events.extend(ev);evidence.extend(sp);issues.extend(err)
    return CanonicalTimetable(source=source,events=tuple(events),evidence=tuple(evidence),issues=tuple(issues))
