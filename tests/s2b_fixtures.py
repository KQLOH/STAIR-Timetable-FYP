"""Invented rendered/token-oracle fixtures, explicitly not real OCR or research data."""
from collections import defaultdict
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from backend.models.extraction import DocumentEvidence, EvidencePage
from backend.layout.block_capture import capture_words, detect_regions
from backend.layout.block_models import BlockEvidence, BlockPage
from backend.services.extraction_adapter import adapt_extraction


def packet(blocks=None, *, explicit_headers=False, authority=False, conflict_date=False,
           header_unknown=False, axes=True, same_color=False, scale=1, reverse=False):
    blocks=blocks if blocks is not None else [dict(code='QA1701',kind='L',name='Graph Study',venue='Q1',staff='TEST ACTOR',group='G9',start=9,end=11,row=0,col=1,span=2)]
    xs=[120,260,400,540,680,820];ys=[140,300,460]
    image=Image.new('RGB',(900,510),'white');draw=ImageDraw.Draw(image)
    for x in xs:draw.line((x,45,x,460),fill=(220,224,229))
    for y in [45,80,115,*ys]:draw.line((10,y,820,y),fill=(220,224,229))
    global_rows=[]
    def clock(hour):return f'{hour%12 or 12}:00'+('AM' if hour<12 else 'PM') if explicit_headers else f'{hour%12 or 12}:00'
    if authority:global_rows.append(('24-HOUR',10,15))
    if axes:
        for i,x in enumerate(xs[:-1]):
            global_rows.extend([(clock(8+i),x+5,53),(clock(9+i),x+5,88)])
        if header_unknown:global_rows[2 if authority else 1]=('??:??',global_rows[2 if authority else 1][1],global_rows[2 if authority else 1][2])
    for row in range(2):
        d=date(2033,2,7)+timedelta(days=row)
        global_rows.extend([(('Tue' if conflict_date else 'Mon') if row==0 else 'Tue',15,ys[row]+10),(d.isoformat(),15,ys[row]+80)])
    layouts=[]
    for index,b in enumerate(blocks):
        x0,x1=xs[b['col']],xs[b['col']+b.get('span',1)];y0=ys[b['row']];y1=ys[b['row']+1]
        draw.rectangle((x0,y0,x1,y1),fill=(42,112,102) if same_color or index%2==0 else (54,79,136))
        code=b.get('code','???');kind=b.get('kind','L')
        meridiem=lambda h:f'{h%12 or 12}:00 '+('AM' if h<12 else 'PM')
        raw=b.get('raw_time',meridiem(b['start'])+' - '+meridiem(b['end']))
        text=[f'{code} ({kind})',b.get('name','Graph Study'),'ROOM '+b.get('venue','Q1'),
              'Lecturer: '+b.get('staff','TEST ACTOR'),'GROUP '+b.get('group','G9'),raw]
        layouts.append((b,(x0,y0,x1+1,y1+1),[(t,x0+6,y0+8+24*i) for i,t in enumerate(text)]))
    font=ImageFont.load_default(size=11)
    for t,x,y in global_rows:draw.text((x,y),t,font=font,fill='black')
    for _,_,rows in layouts:
        for t,x,y in rows:draw.text((x,y),t,font=font,fill='white')
    if scale!=1:image=image.resize((round(image.width*scale),round(image.height*scale)))
    output=BytesIO();image.save(output,format='PNG');checksum=sha256(output.getvalue()).hexdigest();source='src-'+checksum[:24]
    regions,vs,hs,operations=detect_regions(image,source,1)
    def data(rows):
        result={k:[] for k in ['text','left','top','width','height','block_num','par_num','line_num','conf']}
        for line,(t,x,y) in enumerate(rows,1):
            for word in t.split():
                width=font.getlength(word)
                for key,value in dict(text=word,left=x*scale,top=y*scale,width=width*scale,height=11*scale,
                    block_num=1,par_num=1,line_num=line,conf=-1).items():result[key].append(value)
                x+=width+font.getlength(' ')
        if reverse:result={k:list(reversed(v)) for k,v in result.items()}
        return result
    lines=[];words=capture_words(source,1,data(global_rows),lines,'synthetic-global',{'operation':'RENDERED_TOKEN_ORACLE_NOT_OCR'})
    grouped=defaultdict(list)
    for b,box,rows in layouts:
        cx=(box[0]+box[2])/2*scale;cy=(box[1]+box[3])/2*scale
        owner=next(r for r in regions if r.bbox.x0<=cx<r.bbox.x1 and r.bbox.y0<=cy<r.bbox.y1)
        grouped[owner.region_id].extend(rows)
    for rid,rows in grouped.items():words.extend(capture_words(source,1,data(rows),lines,'synthetic-region',{'operation':'RENDERED_TOKEN_ORACLE_NOT_OCR'},rid))
    document=DocumentEvidence(source_id=source,route_type='upload_image',original_label='INVENTED_UNIT_FIXTURE.png',media_type='image/png',
        checksum=checksum,parser_version='SYNTHETIC_TOKEN_ORACLE',pages=[EvidencePage(page=1,width=image.width,height=image.height,regions=lines)])
    page=BlockPage(page=1,width=image.width,height=image.height,route='synthetic-global',transform={'operation':'SYNTHETIC_TEST_ONLY'},
        runtime={'status':'NO_OCR_EXECUTED_TOKEN_ORACLE'},words=words,regions=regions,
        vertical_rules=[x*scale for x in xs],horizontal_rules=[y*scale for y in ys],detection_operations=operations)
    return adapt_extraction(document),BlockEvidence(source_document_id=source,content_sha256=checksum,pages=(page,))
