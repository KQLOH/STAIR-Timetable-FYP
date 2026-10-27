"""Prepare independent planning fixtures, not an OCR adapter or research corpus.

No real screenshot, OCR, university source, credentials or labels are read.
All text, dates, identities, colors and geometry below are invented test inputs.
Expected fields describe what this generator prints, not observed OCR success.
"""
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIRECTORY = Path(__file__).resolve().parent
WIDTHS = (94, 126, 108, 138, 102, 116, 130, 98, 120, 112, 136, 104)
PALE_RULE = (224, 227, 231)
PALE_ROW = (246, 247, 249)
COLORS = ((40, 113, 106), (54, 87, 137), (131, 83, 35))


def clock(hour, meridiem=False):
    value = f'{hour % 12 or 12}:00'
    return value + (' AM' if hour < 12 else ' PM') if meridiem else value


def font(size):
    return ImageFont.load_default(size=size)


def fit_text(draw, box, lines, size, fill):
    x0, y0, x1, y1 = box
    step = (y1-y0-12)/max(1,len(lines))
    for index, line in enumerate(lines):
        point = min(size, max(9,int(step-2)))
        while point>8 and draw.textbbox((0,0),line,font=font(point))[2]>x1-x0-10:
            point -= 1
        draw.text((x0+5,y0+5+index*step),line,font=font(point),fill=fill)


def make_table(name, *, scale=1, shift=0, borders=True, palette=COLORS,
               text_size=16, uncertain=False, ambiguous=False, transposed=False,
               adjacent=False, shared_separator=True, variant_values=False, widths=WIDTHS):
    left = 35+shift
    axis = left+150
    xs = [axis]
    for width in widths:xs.append(xs[-1]+width)
    header_top, header_mid, body_top = 65+shift, 101+shift, 137+shift
    row_height = 114
    ys = [body_top+i*row_height for i in range(6)]
    image = Image.new('RGB',(xs[-1]+35,ys[-1]+45),'white')
    draw = ImageDraw.Draw(image)
    draw.text((left,16+shift),'SYNTHETIC PLANNING FIXTURE - NOT UNIVERSITY DATA',font=font(20),fill='black')
    start = date(2036,5,19) if variant_values else date(2033,2,7)
    start -= timedelta(days=start.weekday())
    axis_start = 7 if variant_values else 6
    for row in range(5):
        draw.rectangle((left,ys[row],xs[-1],ys[row+1]),fill=PALE_ROW if row%2 else 'white')
        draw.rectangle((left,ys[row],axis,ys[row+1]),fill=(218,226,235))
        day = start+timedelta(days=row)
        label = clock(axis_start+row,True) if transposed else day.strftime('%a')+'\n'+day.isoformat()
        fit_text(draw,(left,ys[row],axis,ys[row+1]),label.split('\n'),18,'black')
    draw.rectangle((left,header_top,xs[-1],body_top),fill=(245,245,246))
    fit_text(draw,(left,header_top,axis,body_top),['Day / Time'],16,'black')
    for col in range(12):
        top = (start+timedelta(days=col%5)).strftime('%a') if transposed else clock(axis_start+col)
        bottom = (start+timedelta(days=col%5)).isoformat() if transposed else clock(axis_start+col+1)
        fit_text(draw,(xs[col],header_top,xs[col+1],header_mid),[top],16,'black')
        fit_text(draw,(xs[col],header_mid,xs[col+1],body_top),[bottom],16,'black')
    if borders:
        for y in [header_top,header_mid,*ys]:draw.line((left,y,xs[-1],y),fill=PALE_RULE,width=1)
        for x in [left,*xs]:draw.line((x,header_top,x,ys[-1]),fill=PALE_RULE,width=1)
        for a,b in zip(xs,xs[1:]):draw.line(((a+b)//2,body_top,(a+b)//2,ys[-1]),fill=PALE_RULE,width=1)
    # Six fictional regions, with one-, two- and three-column spans. None copy
    # an input course, person, venue, date, position or the reported block count.
    specs = [(0,1,3,'ZX4101','L','Graph Logic'),(0,8,10,'QV5202','P','Number Lab'),
             (1,4,5,'WN6303','T','Symbol Study'),(2,6,9,'YK7404','L','Model Theory'),
             (3,0,2,'JF8505','P','Vector Work'),(4,9,10,'UC9606','T','Proof Lab')]
    if adjacent:specs[1]=(0,3,5,'QV5202','P','Number Lab')
    if variant_values:
        codes=('EX5101','VR6202','HG7303','NU8404','OD9505','CX1606')
        titles=('Finite Sets','Signal Methods','Logic Workshop','Spatial Algebra','Data Models','Queue Theory')
        specs=[((row+1)%5,a,b,codes[i],kind,titles[i]) for i,(row,a,b,_,kind,_) in enumerate(specs)]
    expected=[]
    for index,(row,a,b,code,kind,title) in enumerate(specs):
        box=[xs[a],ys[row],xs[b],ys[row+1]]
        raw_range=f'{clock(axis_start+a,True)} - {clock(axis_start+b,True)}'
        state='PRESENT'
        if uncertain and index==1:raw_range=f'{clock(axis_start+a)} - {clock(axis_start+b)}';state='UNRESOLVED'
        if uncertain and index==2:raw_range='??:?? - unreadable';state='UNRESOLVED'
        if ambiguous and index==3:
            box[0]+=widths[a]//3;box[2]-=widths[b-1]//3
            raw_range='Time TBA';state='UNRESOLVED'
        venue='TEST-V'+str((401 if variant_values else 201)+index)
        if uncertain and index==2:venue='TBA'
        lines=[f'{code} ({kind})',title,venue,f'SYNTHETIC STAFF {index+1}',raw_range]
        draw.rectangle(tuple(box),fill=palette[index%len(palette)])
        fit_text(draw,box,lines,text_size,'white')
        day=start+timedelta(days=row)
        expected.append({'fixture_region':'synthetic-region-'+str(index+1),
            'course_code':code,'course_name':title,'session_type_token':kind,
            'date':None if transposed else day.isoformat(),'day_of_week':None if transposed else row+1,
            'start_time':f'{axis_start+a:02}:00' if state=='PRESENT' else None,
            'end_time':f'{axis_start+b:02}:00' if state=='PRESENT' else None,'time_field_state':state,
            'raw_time_text':raw_range,'location':None if venue=='TBA' else venue,
            'raw_location':venue,'lecturer':f'SYNTHETIC STAFF {index+1}',
            'block_bbox':[round(v*scale) for v in box],
            'time_state_basis':'Conservative unresolved without demonstrated axis authority' if state=='UNRESOLVED' else 'Explicit synthetic AM/PM range',
            'unsupported_orientation':transposed})
    if adjacent and shared_separator:
        draw.line((xs[3],ys[0],xs[3],ys[1]-1),fill=PALE_RULE,width=2)
    if scale!=1:image=image.resize((round(image.width*scale),round(image.height*scale)))
    path=DIRECTORY/name
    image.save(path,format='PNG')
    return {'file':name,'sha256':sha256(path.read_bytes()).hexdigest(),'width':image.width,
        'height':image.height,'kind':'INDEPENDENT_SYNTHETIC_PLANNING_INPUT',
        'registered_partition':None,'not_research_gold':True,
        'family':'UNSUPPORTED_TRANSPOSED_AXES' if transposed else 'COLORED_BLOCKS_TWO_ROW_TIME_AXIS',
        'purpose':'Unsupported orientation; no guessed day/header ownership' if transposed else
            'Future field/evidence-ownership assertions; no recognition success asserted',
        'ownership_condition':'Two adjacent same-color blocks; do not force a split without boundary/context evidence' if adjacent else 'Separate printed regions',
        'shared_separator_present':shared_separator if adjacent else None,
        'expected_printed_regions':expected,'ocr_executed':False}


def make_notice():
    image=Image.new('RGB',(950,410),'white');draw=ImageDraw.Draw(image)
    draw.text((25,20),'SYNTHETIC NON-TIMETABLE NEGATIVE INPUT',font=font(22),fill='black')
    notices=[('SERVICE NOTICE',['MODEL ZX4999','Invented notice, not a course session']),
             ('COLOR LEGEND',['TEST GREEN','No timetable day or time relationship'])]
    for index,(heading,lines) in enumerate(notices):
        box=(30+450*index,100,450+450*index,330)
        draw.rectangle(box,fill=COLORS[index]);fit_text(draw,box,[heading,*lines],22,'white')
    path=DIRECTORY/'08-unrelated-colored-notice.png';image.save(path)
    return {'file':path.name,'sha256':sha256(path.read_bytes()).hexdigest(),'width':950,'height':410,
        'kind':'INDEPENDENT_SYNTHETIC_PLANNING_INPUT','family':'NOT_A_TIMETABLE',
        'purpose':'Colored rectangles and code-like text alone must not establish a timetable session',
        'expected_printed_regions':[],'ocr_executed':False,'not_research_gold':True}


def generate():
    fixtures=[make_table('01-two-row-colored.png'),
        make_table('02-scaled-translated.png',scale=1.35,shift=23),
        make_table('03-colors-and-small-fonts.png',palette=((67,78,132),(117,68,90),(38,114,76)),text_size=12,
                   variant_values=True,widths=tuple(reversed(WIDTHS))),
        make_table('04-missing-grid-rules.png',borders=False),
        make_table('05-unreadable-and-no-meridiem.png',uncertain=True),
        make_table('06-ambiguous-block-boundary.png',ambiguous=True),
        make_table('07-transposed-unsupported.png',transposed=True),make_notice(),
        make_table('09-adjacent-same-color.png',palette=(COLORS[0],),adjacent=True),
        make_table('10-touching-ambiguous-regions.png',palette=(COLORS[0],),adjacent=True,shared_separator=False)]
    manifest={'schema_version':'s2b-synthetic-preparation.1','status':'FIXTURES_ONLY_NO_ADAPTER_IMPLEMENTATION',
        'origin':'Invented text and geometry in this generator; no real file or protected research data read',
        'time_oracle_basis':'Generator inputs plus explicitly printed synthetic ranges; not inferred real-source labels',
        'evaluation_status':'NOT_EXECUTED; no OCR accuracy or recognition result claimed',
        'fixtures':fixtures}
    (DIRECTORY/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    result=generate()
    print(f"Prepared {len(result['fixtures'])} independent synthetic planning PNGs; no OCR executed.")
