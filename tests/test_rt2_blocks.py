"""RT2 regression fixtures are invented, generated in memory, never private data."""
from datetime import date, timedelta
from io import BytesIO
import unittest
import re
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from backend.main import app
from backend.layout.block_capture import detect_regions, prepare_crop, capture_words
from backend.layout.block_grid import reconstruct_blocks, header_axis, select_region_words
from backend.layout.block_models import BlockEvidence
from backend.models.event import CanonicalTimetable
from tests.s2b_fixtures import packet


def contents(result):
    c=result['canonical_output']
    return [e['revision']['content'] for e in c['packet']['events']+c['unresolved_candidates']]


def image_bytes(scale=1, color=(45,120,90)):
    """Centered headers, variable widths, adjacent sessions and tiny white text."""
    image=Image.new('RGB',(1101,431),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.load_default(size=11)
    xs=[140,260,380,520,660,800,950,1100]
    for x in [5,*xs]:draw.line((x,20,x,430),fill='#d2d2d2')
    for y in [20,50,80,190,300,430]:draw.line((5,y,1100,y),fill='#d2d2d2')
    def text(x,y,t,fill='black'):
        draw.text((x,y),t,font=font,fill=fill,anchor='mt')
    for i,(left,right) in enumerate(zip(xs,xs[1:])):
        text((left+right)/2,30,f'{(8+i)%12 or 12:02}:00')
        text((left+right)/2,60,f'{(9+i)%12 or 12:02}:00')
    for i,day in enumerate(['Mon','Tue','Wed']):
        text(70,100+i*110,day);text(70,122+i*110,(date(2033,2,7)+timedelta(days=i)).isoformat())
    for row,a,b,code,kind,venue,time in [(0,0,2,'ZX4101','L','R101','8:00 AM - 10:00 AM'),
            (1,3,4,'QW5202','P','S202','11:00 AM - 12:00 PM'),
            (1,4,5,'QW5202','P','S203','12:00 PM - 1:00 PM')]:
        left,right=xs[a]+1,xs[b]-1;top=81+row*110
        draw.rectangle((left,top,right,top+108),fill=color)
        for j,line in enumerate([f'{code} ({kind})',venue,'Test Person',time]):text((left+right)/2,top+15+j*20,line,'white')
    text(700,350,'Holiday - no classes')
    if scale!=1:image=image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
    stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()


class RT2Contracts(unittest.TestCase):
    def test_centered_headers_map_nonuniform_cells(self):
        p,s=packet()
        page=s.pages[0]
        # Shift token-oracle header words to their cell centers, retaining raw text.
        words=[]
        xs=[120,260,400,540,680,820]
        for w in page.words:
            if w.region_id is None and ':' in w.text:
                index=next(i for i in range(5) if xs[i]<=w.bbox.x0<xs[i+1])
                width=w.bbox.x1-w.bbox.x0;left=(xs[index]+xs[index+1]-width)/2
                w=w.model_copy(update={'bbox':w.bbox.model_copy(update={'x0':left,'x1':left+width})})
            words.append(w)
        page=page.model_copy(update={'words':tuple(words),'header_vertical_rules':tuple(xs)})
        axis,_=header_axis(page)
        self.assertEqual([(c['left'],c['right']) for c in axis['columns'][:-1]],list(zip(xs,xs[1:]))[:-1])

    def test_no_axis_authority_no_invented_meridiem(self):
        p,s=packet([dict(code='ZX4101',kind='L',start=9,end=11,row=0,col=1,span=2,raw_time='9:00 - 11:00')])
        value=contents(reconstruct_blocks(p,s)[0])[0]
        self.assertIsNone(value['start_time'])

    def test_continuous_axis_uses_explicit_neighbor_anchor(self):
        blocks=[dict(code='ZX4101',kind='L',start=9,end=10,row=0,col=1,span=1),
                dict(code='QW5202',kind='P',start=11,end=12,row=1,col=3,span=1,raw_time='11:00 - 12:00')]
        p,s=packet(blocks);result=reconstruct_blocks(p,s)[0]
        value=next(v for v in contents(result) if v['course_code']=='QW5202')
        self.assertEqual((value['start_time'],value['end_time']),('11:00','12:00'))
        self.assertTrue(any('TIME_INFERRED_FROM_EXPLICITLY_ANCHORED_AXIS' in r['issues'] for r in result['canonical_output']['layout']['regions']))

    def test_unreadable_interval_not_repaired_from_neighbor(self):
        blocks=[dict(code='ZX4101',kind='L',start=9,end=10,row=0,col=1,span=1),
                dict(code='QW5202',kind='P',start=11,end=12,row=1,col=3,span=1,raw_time='Time TBA')]
        p,s=packet(blocks)
        self.assertIsNone(next(v for v in contents(reconstruct_blocks(p,s)[0]) if v['course_code']=='QW5202')['start_time'])

    def test_missing_date_never_uses_system_date(self):
        p,s=packet();page=s.pages[0]
        page=page.model_copy(update={'words':tuple(w for w in page.words if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',w.text))})
        s=s.model_copy(update={'pages':(page,)})
        self.assertIsNone(contents(reconstruct_blocks(p,s)[0])[0]['date'])

    def test_small_white_text_crop_transform_and_bounds(self):
        im=Image.open(BytesIO(image_bytes()));regions,*_=detect_regions(im,'synthetic',1)
        self.assertEqual(len(regions),3)
        for r in regions:
            crop,t=prepare_crop(im,r,white_text=True)
            self.assertEqual(crop.width,4*int(r.bbox.x1-r.bbox.x0)+48)
            self.assertEqual(t['output_boxes_mapped_to'],'ORIGINAL_PAGE_PIXELS')
            self.assertIn('threshold',t['operation'])

    def test_scale_palette_and_adjacent_blocks(self):
        for scale,color in [(1,(45,120,90)),(1.5,(90,65,145)),(.8,(130,75,40))]:
            im=Image.open(BytesIO(image_bytes(scale,color)));regions,*_=detect_regions(im,'synthetic',1)
            self.assertEqual(len(regions),3)
            self.assertTrue(all(r.boundary_status=='RECTANGULAR' for r in regions))
            self.assertLess(regions[1].bbox.x1,regions[2].bbox.x0)

    def test_alternate_type_conflict_stays_explicit(self):
        _,s=packet();words=[w for w in s.pages[0].words if w.region_id]
        alternate=[w.model_copy(update={'text':'(P)' if w.text=='(L)' else w.text,
            'capture_route':'s2b-region-light-text-ocr-v2','parent_line_id':w.parent_line_id+'-alt'}) for w in words]
        _,issues=select_region_words(words+alternate)
        self.assertIn('COMPETING_CROP_TYPE_READINGS',issues)

    def test_alternate_type_requires_matching_course(self):
        _,s=packet();words=[w for w in s.pages[0].words if w.region_id]
        broken=[w.model_copy(update={'text':'(7)' if w.text=='(L)' else w.text}) for w in words]
        alternate=[w.model_copy(update={'capture_route':'s2b-region-light-text-ocr-v2',
            'parent_line_id':w.parent_line_id+'-alt'}) for w in words]
        selected,issues=select_region_words(broken+alternate)
        self.assertIn('TYPE_RECOVERED_FROM_ALTERNATE_CROP_OCR',issues)
        self.assertIn('(L)',[w.text for w in selected])
        wrong=[w.model_copy(update={'text':'ZZ9999' if w.text=='QA1701' else w.text}) for w in alternate]
        self.assertNotIn('TYPE_RECOVERED_FROM_ALTERNATE_CROP_OCR',select_region_words(broken+wrong)[1])

    def test_crop_cap_checked_before_upscale(self):
        from backend.models.evidence import BoundingBox
        image=Image.new('RGB',(1000,1000),'white')
        box=BoundingBox(x0=0,y0=0,x1=1000,y1=1000,coordinate_space='PIXELS')
        with self.assertRaisesRegex(ValueError,'CROP_PIXEL_CAP'):prepare_crop(image,box)

    def test_invalid_header_coordinates_rejected(self):
        p,s=packet();raw=s.model_dump();raw['pages'][0]['header_vertical_rules']=[-1]
        with self.assertRaises(ValueError):BlockEvidence.model_validate(raw)


class RT2SyntheticOCR(unittest.TestCase):
    def test_centered_small_text_end_to_end_at_two_scales(self):
        with TestClient(app) as client:
            for scale in [1,1.5]:
                with self.subTest(scale=scale):
                    x=client.post('/api/extract?layout_profile=ocr-block-grid-s2b-v1',content=image_bytes(scale),headers={'Content-Type':'image/png','X-Filename':'invented.png'})
                    self.assertEqual(x.status_code,200,x.text[:300]);x=x.json()
                    result=reconstruct_blocks(CanonicalTimetable.model_validate(x['data']),BlockEvidence.model_validate(x['layout_evidence']))[0]
                    values=contents(result)
                    self.assertEqual(len(values),3)
                    expected={('2033-02-07','08:00','10:00'),('2033-02-08','11:00','12:00'),('2033-02-08','12:00','13:00')}
                    self.assertEqual({tuple(v[k] for k in ['date','start_time','end_time']) for v in values},expected)
                    self.assertEqual(next(v for v in values if v['date']=='2033-02-07')['course_code'],'ZX4101')
                    # Tiny W glyphs can be read as W/ after resampling. Such OCR
                    # must stay unresolved, never be lexically "repaired".
                    for v in values:
                        if v['date']=='2033-02-08':self.assertIn(v['course_code'],('QW5202',None))
                    for e in result['canonical_output']['packet']['events']:
                        self.assertEqual(e['human_verification']['status'],'NOT_VERIFIED')
                        self.assertEqual(e['calendar']['approval_status'],'NOT_APPROVED')


if __name__=='__main__':unittest.main()
