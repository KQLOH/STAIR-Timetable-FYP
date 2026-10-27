"""Bounded generic colored-rectangle measurements and explicitly separate crop OCR.

No real-sample identity, coordinates, palette, source content or fixture manifest
is accessed. Original global OCR survives; local OCR transforms are recorded.
"""
import time
import re
import numpy as np
from PIL import Image, ImageOps

from backend.models.evidence import BoundingBox
from backend.models.extraction import EvidenceRegion, BoundingBox as RawBox
from backend.reconstruction.models import digest
from backend.services.ocr_runtime import recognize, confidence, OCRRuntimeError
from backend.layout.block_models import (BlockPage, BlockWord, ColorRegion, PROFILE,
    MAX_PIXELS, MAX_TOTAL_PIXELS, MAX_PAGES, MAX_REGIONS, MAX_DOCUMENT_REGIONS,
    MAX_WORDS, MAX_DOCUMENT_WORDS, MAX_OPERATIONS)

DETECTION_CONFIG={'quantization_bits':5,'minimum_chroma':24,'minimum_fill':.58,
    'minimum_area_fraction':.00025,'minimum_side_fraction':.012,
    'crop_scale':3,'crop_border_source_pixels':6,'max_operations':MAX_OPERATIONS,
    'bridge_erosion_radius_fraction':.001,'minimum_proposal_fill':.20,
    'rectangular_border_coverage':.90}


def _thin_peaks(values, maximum):
    result=[];start=None
    for i,yes in enumerate([*values,False]):
        if yes and start is None:start=i
        if not yes and start is not None:
            if i-start<=maximum:result.append((start+i-1)/2)
            start=None
    return tuple(result)


def _longest(mask):
    current=np.zeros(mask.shape[1],dtype=np.int32);best=current.copy()
    for row in mask:
        current=np.where(row,current+1,0);best=np.maximum(best,current)
    return best


def structural_rules(rgb):
    """Thin local contrast strokes; does not interpret text or clock values."""
    gray=np.asarray(Image.fromarray(rgb).convert('L')).astype(np.int16)
    chroma=rgb.max(axis=2).astype(np.int16)-rgb.min(axis=2)
    h,w=gray.shape;gap=max(2,round(min(h,w)*.003))
    vertical=np.zeros((h,w),dtype=bool);horizontal=vertical.copy()
    vertical[:,gap:-gap]=(gray[:,gap:-gap]+3<np.minimum(gray[:,:-2*gap],gray[:,2*gap:])) & (chroma[:,gap:-gap]<24)
    horizontal[gap:-gap,:]=(gray[gap:-gap,:]+3<np.minimum(gray[:-2*gap,:],gray[2*gap:,:])) & (chroma[gap:-gap,:]<24)
    vs=_thin_peaks(_longest(vertical)>=max(25,h*.12),max(4,round(w*.004)))
    hs=_thin_peaks((horizontal.sum(axis=1)>=w*.35) & (_longest(horizontal.T)>=w*.04),max(4,round(h*.006)))
    if len(vs)>128 or len(hs)>64:raise ValueError('S2B_RULE_CAP')
    return vs,hs


def detect_regions(image,source_id,page):
    if image.width*image.height>MAX_PIXELS:raise ValueError('S2B_PIXEL_CAP')
    rgb=np.asarray(image.convert('RGB'));h,w,_=rgb.shape
    chroma=rgb.max(axis=2).astype(np.int16)-rgb.min(axis=2)
    q=rgb.astype(np.int32)//8
    keys=np.where(chroma>=DETECTION_CONFIG['minimum_chroma'],1+q[:,:,0]*1024+q[:,:,1]*32+q[:,:,2],0)
    original_keys=keys
    radius=max(1,round(min(h,w)*.001))
    # Remove only narrow color bridges. This does not split a genuinely solid
    # touching rectangle or supply a missing internal source boundary.
    eroded=np.zeros_like(keys);center=keys[radius:-radius,radius:-radius]
    keep=(center==keys[:-2*radius,radius:-radius]) & (center==keys[2*radius:,radius:-radius]) & (center==keys[radius:-radius,:-2*radius]) & (center==keys[radius:-radius,2*radius:])
    eroded[radius:-radius,radius:-radius]=np.where(keep,center,0);keys=eroded
    parent={};stats={};previous=[];operations=0
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(a,b):
        a,b=root(a),root(b)
        if a==b:return a
        lo,hi=sorted((a,b));parent[hi]=lo
        x=stats[lo];y=stats.pop(hi)
        stats[lo]=[min(x[0],y[0]),min(x[1],y[1]),max(x[2],y[2]),max(x[3],y[3]),x[4]+y[4],x[5]]
        return lo
    for y,row in enumerate(keys):
        boundaries=np.flatnonzero(np.r_[True,row[1:]!=row[:-1],True]);current=[];j=0
        for x0,x1 in zip(boundaries,boundaries[1:]):
            key=int(row[x0])
            if not key:continue
            operations+=1
            if operations>MAX_OPERATIONS:raise ValueError('S2B_DETECTION_OPERATION_CAP')
            index=len(parent);parent[index]=index;stats[index]=[int(x0),y,int(x1),y+1,int(x1-x0),key]
            while j<len(previous) and previous[j][1]<=x0:j+=1
            k=j
            while k<len(previous) and previous[k][0]<x1:
                operations+=1
                if operations>MAX_OPERATIONS:raise ValueError('S2B_DETECTION_OPERATION_CAP')
                a,b,oldkey,oldid=previous[k]
                if oldkey==key and min(x1,b)>max(x0,a):index=union(index,oldid)
                k+=1
            current.append((int(x0),int(x1),key,index))
        previous=current
    regions=[]
    for x0,y0,x1,y1,area,key in stats.values():
        if (x1-x0<max(12,w*.012) or y1-y0<max(12,h*.02) or area<h*w*.00025):continue
        coverage=area/((x1-x0)*(y1-y0))
        if coverage<DETECTION_CONFIG['minimum_proposal_fill']:continue
        pixels=original_keys[y0:y1,x0:x1]==key
        border=min(float(pixels[0,:].mean()),float(pixels[-1,:].mean()),float(pixels[:,0].mean()),float(pixels[:,-1].mean()))
        boundary='RECTANGULAR' if coverage>=DETECTION_CONFIG['minimum_fill'] and border>=.90 else 'AMBIGUOUS_COMPONENT'
        seed={'source':source_id,'page':page,'box':[x0,y0,x1,y1],'color_key':key,'method':'s2b.1'}
        regions.append(ColorRegion(region_id='s2b-region-'+digest(seed),
            bbox=BoundingBox(x0=x0,y0=y0,x1=x1,y1=y1,coordinate_space='PIXELS'),
            pixel_color_key=key,fill_coverage=coverage,boundary_status=boundary,inner_margin_pixels=radius))
    regions.sort(key=lambda r:(r.bbox.y0,r.bbox.x0,r.region_id))
    if len(regions)>MAX_REGIONS:raise ValueError('S2B_REGION_CAP')
    vs,hs=structural_rules(rgb)
    return tuple(regions),vs,hs,operations


def capture_words(source_id,page,data,line_regions,route,transform,region_id=None,offset=(0,0),scale=1,existing_lines=False):
    lines={}
    for i,text in enumerate(data['text']):
        if str(text).strip():lines.setdefault(tuple(int(data[k][i]) for k in ('block_num','par_num','line_num')),[]).append(i)
    words=[]
    for ordinal,(key,indices) in enumerate(sorted(lines.items()),1):
        text=' '.join(str(data['text'][i]).strip() for i in indices)
        boxes={i:[offset[0]+float(data['left'][i])/scale,offset[1]+float(data['top'][i])/scale,
                  offset[0]+float(data['left'][i]+data['width'][i])/scale,offset[1]+float(data['top'][i]+data['height'][i])/scale] for i in indices}
        bbox=[min(v[0] for v in boxes.values()),min(v[1] for v in boxes.values()),max(v[2] for v in boxes.values()),max(v[3] for v in boxes.values())]
        lid='s2b-line-'+digest({'source':source_id,'page':page,'region':region_id,'route':route,
                              'transform_digest':digest(transform),'key':key,'text':text,'bbox':bbox})
        scores=[confidence(data.get('conf',[None]*len(data['text']))[i]) for i in indices]
        score=sum(scores)/len(scores) if all(s is not None for s in scores) else None
        if existing_lines:
            line_regions[ordinal-1].evidence_id=lid
        else:
            line_regions.append(EvidenceRegion(evidence_id=lid,page=page,line=ordinal,text=text,
                bbox=RawBox(x0=bbox[0],y0=bbox[1],x1=bbox[2],y1=bbox[3]),confidence=score,
                adapter=route,group=region_id))
        for i in indices:
            raw=str(data['text'][i]);b=boxes[i]
            wid='s2b-word-'+digest({'source':source_id,'page':page,'parent':lid,'text':raw,'box':b,'region':region_id})
            words.append(BlockWord(evidence_id=wid,text=raw,bbox=BoundingBox(x0=b[0],y0=b[1],x1=b[2],y1=b[3],coordinate_space='PIXELS'),
                confidence=confidence(data.get('conf',[None]*len(data['text']))[i]),parent_line_id=lid,
                block=key[0],paragraph=key[1],line=key[2],reading_order=i,region_id=region_id,
                capture_route=route,transform=transform))
    return words


def prepare_crop(image,region):
    b=region.bbox if hasattr(region,'bbox') else region
    crop=image.crop((int(b.x0),int(b.y0),int(b.x1),int(b.y1))).convert('RGB')
    bitmap=crop.resize((crop.width*3,crop.height*3),Image.Resampling.LANCZOS)
    bitmap=ImageOps.expand(bitmap,border=18,fill='white')
    return bitmap,{
        'operation':'region RGB crop; Lanczos 3x resize; 18px white border',
        'crop':[b.x0,b.y0,b.x1,b.y1],'scale_x':3,'scale_y':3,'padding_x':18,'padding_y':18,
        'source_space':'PIXELS','target_space':'PIXELS',
        'output_boxes_mapped_to':'ORIGINAL_PAGE_PIXELS','rotation_applied_degrees':0}


class BlockCollector(list):
    """Internal extractor hook. Never selected by a registered/default route."""
    max_pages = MAX_PAGES
    def __init__(self):
        super().__init__();self.started=time.monotonic();self.local_calls=0

    def check_image(self,image):
        if image.width*image.height>MAX_PIXELS:raise ValueError('S2B_PIXEL_CAP')
        if len(self)>=MAX_PAGES or sum(p.width*p.height for p in self)+image.width*image.height>MAX_TOTAL_PIXELS:
            raise ValueError('S2B_PAGE_OR_TOTAL_PIXEL_CAP')
        if time.monotonic()-self.started>60:raise ValueError('S2B_EXTRACTION_WATCHDOG')

    def capture_page(self,source_id,image,page,adapter,data,runtime,line_regions,transform):
        regions,vs,hs,operations=detect_regions(image,source_id,page)
        if sum(len(p.regions) for p in self)+len(regions)>MAX_DOCUMENT_REGIONS:raise ValueError('S2B_DOCUMENT_REGION_CAP')
        words=capture_words(source_id,page,data,line_regions,adapter,transform,existing_lines=True)
        diagnostics=[]
        def local_ocr(box,route,region_id=None):
            if time.monotonic()-self.started>60:raise ValueError('S2B_EXTRACTION_WATCHDOG')
            self.local_calls+=1
            if self.local_calls>64:raise ValueError('S2B_LOCAL_OCR_CALL_CAP')
            crop,local_transform=prepare_crop(image,box)
            if crop.width*crop.height>MAX_PIXELS:raise ValueError('S2B_CROP_PIXEL_CAP')
            try:local,local_runtime=recognize(crop)
            except OCRRuntimeError as error:
                diagnostics.append('REGION_OCR_FAILED:'+(region_id or route)+':'+error.code);return
            b=box.bbox if hasattr(box,'bbox') else box
            local_transform={**local_transform,'parent_page_transform':transform,'runtime':local_runtime}
            words.extend(capture_words(source_id,page,local,line_regions,route,local_transform,
                                       region_id,(b.x0-6,b.y0-6),3))
            if len(words)>MAX_WORDS:raise ValueError('S2B_WORD_CAP')
        for region in regions:local_ocr(region,'s2b-region-crop-ocr-v1',region.region_id)
        # Source-derived context crops preserve separately observed OCR readings.
        # No character correction or lexical substitution is performed.
        clocks=[w for w in words if w.capture_route==adapter and
            re.fullmatch(r'\d{1,2}[:.]\d{2}',w.text.strip()) and
            w.bbox.y1<=min((r.bbox.y0 for r in regions),default=0)]
        if len(clocks)>=6 and regions:
            height=max(w.bbox.y1-w.bbox.y0 for w in clocks)
            box=BoundingBox(x0=max(0,int(min(w.bbox.x0 for w in clocks)-height)),
                y0=max(0,int(min(w.bbox.y0 for w in clocks)-height)),
                x1=min(image.width,int(max(w.bbox.x1 for w in clocks)+height+1)),
                y1=min(image.height,int(max(w.bbox.y1 for w in clocks)+height+1)),coordinate_space='PIXELS')
            local_ocr(box,'s2b-axis-crop-ocr-v1')
            left=min(min(r.bbox.x0 for r in regions),min(w.bbox.x0 for w in clocks))
            bands=sorted(set((r.bbox.y0,r.bbox.y1) for r in regions))
            for y0,y1 in bands:
                if left>10:
                    box=BoundingBox(x0=0,y0=y0,x1=int(left),y1=y1,coordinate_space='PIXELS')
                    local_ocr(box,'s2b-row-header-crop-ocr-v1')
        if sum(len(p.words) for p in self)+len(words)>MAX_DOCUMENT_WORDS:raise ValueError('S2B_DOCUMENT_WORD_CAP')
        self.append(BlockPage(page=page,width=image.width,height=image.height,route=adapter,transform=transform,
            runtime=runtime,words=tuple(words),regions=regions,vertical_rules=vs,horizontal_rules=hs,
            diagnostics=tuple(diagnostics),detection_operations=operations))
