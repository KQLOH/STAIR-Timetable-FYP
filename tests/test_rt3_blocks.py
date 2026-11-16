"""Synthetic RT3 wrapped-layout and compression regressions."""
from hashlib import sha256
from io import BytesIO
import unittest
from PIL import Image,ImageDraw,ImageFont
from backend.models.extraction import DocumentEvidence,EvidencePage
from backend.layout.block_capture import capture_words,detect_regions
from backend.layout.block_models import BlockPage,BlockEvidence
from backend.layout.block_grid import reconstruct_blocks,block_graph
from backend.services.extraction_adapter import adapt_extraction


def wrapped_fixture(rows=None, *, second=None, axes=True):
    rows=rows or ['QA1701 (L)','HX C','ALEX RIVER','9:00 AM - 11:00 AM']
    xs=[120,260,400,540,680,820]
    image=Image.new('RGB',(900,480),'white');draw=ImageDraw.Draw(image);font=ImageFont.load_default(size=12)
    global_rows=[('Mon',15,155),('2033-02-07',15,230)]
    if axes:
        for i,x in enumerate(xs[:-1]):global_rows.extend([(f'{8+i}:00',x+5,50),(f'{9+i}:00',x+5,85)])
    layouts=[(260,539,rows)]
    if second:layouts.append((541,679,second))
    for left,right,texts in layouts:
        draw.rectangle((left,140,right,299),fill=(45,120,95))
        for i,t in enumerate(texts):draw.text((left+8,152+i*16),t,font=font,fill='white')
    for t,x,y in global_rows:draw.text((x,y),t,font=font,fill='black')
    buf=BytesIO();image.save(buf,format='PNG');checksum=sha256(buf.getvalue()).hexdigest();source='synthetic-'+checksum[:20]
    regions,vs,hs,ops=detect_regions(image,source,1)
    def tokens(rows):
        result={k:[] for k in ['text','left','top','width','height','block_num','par_num','line_num','conf']}
        for line,(text,x,y) in enumerate(rows,1):
            for word in text.split():
                width=font.getlength(word)
                for k,v in dict(text=word,left=x,top=y,width=width,height=12,block_num=1,par_num=1,line_num=line,conf=90).items():result[k].append(v)
                x+=width+font.getlength(' ')
        return result
    lines=[];words=capture_words(source,1,tokens(global_rows),lines,'synthetic-context',{'operation':'TOKEN_ORACLE'})
    for left,right,texts in layouts:
        region=next(r for r in regions if left<=r.bbox.x0<right)
        words.extend(capture_words(source,1,tokens([(t,left+8,152+i*16) for i,t in enumerate(texts)]),lines,'synthetic-crop',{'operation':'TOKEN_ORACLE'},region.region_id))
    raw=DocumentEvidence(source_id=source,route_type='upload_image',original_label='invented-four-line.png',media_type='image/png',checksum=checksum,
        parser_version='SYNTHETIC_TOKEN_ORACLE',pages=[EvidencePage(page=1,width=900,height=480,regions=lines)])
    page=BlockPage(page=1,width=900,height=480,route='synthetic-context',transform={'operation':'TOKEN_ORACLE'},runtime={'status':'NO_OCR'},
        words=tuple(words),regions=regions,vertical_rules=tuple(xs),horizontal_rules=(140,300),detection_operations=ops)
    return adapt_extraction(raw),BlockEvidence(source_document_id=source,content_sha256=checksum,pages=(page,))


from tests.test_four_line_mapping import values
from fastapi.testclient import TestClient
from backend.main import app
from backend.layout.block_grid import wrapped_shape

class RT3Contracts(unittest.TestCase):
    def test_wrapped_fields_and_single_name(self):
        p,s=wrapped_fixture(['QA1701','(T)','RX 204','Alex','River','9:00 AM -','11:00 AM'])
        v=values(p,s)[0]
        self.assertEqual((v['course_code'],v['session_type'],v['location'],v['lecturer'],v['start_time'],v['end_time']),('QA1701','Tutorial','RX 204','Alex River','09:00','11:00'))
        self.assertIsNone(v['course_name'])
        p,s=wrapped_fixture(['QA1701 (L)','HX ABC','Alex','9:00 AM - 11:00 AM'])
        v=values(p,s)[0];self.assertEqual((v['location'],v['lecturer']),('HX ABC','Alex'))

    def test_hyphenated_course_multiword_room(self):
        p,s=wrapped_fixture(['QAZ-1701 (P)','HX SG 4','Alex River','9:00 AM - 11:00 AM'])
        v=values(p,s)[0];self.assertEqual((v['course_code'],v['location']),('QAZ-1701','HX SG 4'))

    def test_trailing_note_not_consumed(self):
        p,s=wrapped_fixture(['QA1701','(T)','HX C','Alex','9:00 AM - 11:00 AM','Weekly'])
        self.assertIsNone(wrapped_shape([w for w in s.pages[0].words if w.region_id]))

    def test_jpeg_separator_and_pixel_equivalent_png(self):
        image=Image.new('RGB',(520,320),'white');draw=ImageDraw.Draw(image)
        for left,color in [(40,(62,132,77)),(182,(62,132,77)),(324,(154,43,49))]:
            draw.rectangle((left,60,left+140,250),fill=color)
            draw.text((left+10,100),'QX1801 (T)',fill='white')
        buf=BytesIO();image.save(buf,format='JPEG',quality=75)
        jpeg=Image.open(BytesIO(buf.getvalue()));a=detect_regions(jpeg,'synthetic',1)[0]
        png=BytesIO();jpeg.save(png,format='PNG');b=detect_regions(Image.open(BytesIO(png.getvalue())),'synthetic',1)[0]
        self.assertEqual(len(a),3);self.assertEqual(a,b)
        self.assertTrue(all(x.boundary_status=='RECTANGULAR' for x in a))

    def test_digest_assessment_recomputes_and_rejects_tampering(self):
        p,s=wrapped_fixture();r=reconstruct_blocks(p,s)[0]
        body={'source_packet':p.model_dump(mode='json'),'layout_evidence':s.model_dump(mode='json'),'reconstruction':{'canonical_digest':r['canonical_digest']}}
        with TestClient(app) as client:
            response=client.post('/api/layout/s2b/assess',json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(all(d['decision']=='ABSTAIN' for d in response.json()['decisions']))
            body['reconstruction']['canonical_digest']='0'*64
            self.assertEqual(client.post('/api/layout/s2b/assess',json=body).status_code,422)

    def test_empty_states(self):
        import subprocess
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        run=subprocess.run(['node',str(root/'tests/rt3_empty_checks.js')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_measured_half_hour_edges_and_off_grid_rejection(self):
        from types import SimpleNamespace
        from backend.layout.block_grid import header_axis,explicit_boundary_clocks
        p,s=wrapped_fixture();page=s.pages[0];axis,_=header_axis(page)
        region=SimpleNamespace(bbox=SimpleNamespace(x0=330,x1=470))
        self.assertEqual(explicit_boundary_clocks(region,page,axis),[570,630])
        region.bbox.x0=345;self.assertIsNone(explicit_boundary_clocks(region,page,axis))

    def test_red_warning_and_capture_scope_without_status_promotion(self):
        p,s=wrapped_fixture();page=s.pages[0]
        red=page.regions[0].model_copy(update={'pixel_color_key':1+20*1024+5*32+5})
        s=s.model_copy(update={'pages':(page.model_copy(update={'regions':(red,)}),)})
        result=reconstruct_blocks(p,s)[0]['canonical_output']
        self.assertIn('RED_REGION_REQUIRES_CLASH_OR_HOLIDAY_REVIEW',result['layout']['regions'][0]['issues'])
        self.assertEqual(result['layout']['scope'],'VISIBLE_CAPTURE_ONLY_COMPLETENESS_NOT_ESTABLISHED')
        for event in result['packet']['events']+result['unresolved_candidates']:
            self.assertEqual(event['human_verification']['status'],'NOT_VERIFIED')
            self.assertEqual(event['calendar']['approval_status'],'NOT_APPROVED')

    def test_mobile_chrome_clock_does_not_expand_header_crop(self):
        import os
        from PIL import ImageOps
        from tests.test_rt2_blocks import image_bytes
        image=Image.open(BytesIO(image_bytes()))
        image=ImageOps.expand(image,border=(0,300,0,0),fill=(20,20,20))
        draw=ImageDraw.Draw(image);draw.text((20,20),'12:05',fill='white',font=ImageFont.load_default(size=20))
        draw.rectangle((100,150,250,200),fill=(30,120,180));draw.text((120,160),'Print',fill='white')
        buf=BytesIO();image.save(buf,format='JPEG',quality=85)
        with TestClient(app) as client:
            response=client.post('/api/extract?pipeline=evidence&layout_profile=ocr-block-grid-s2b-v1',content=buf.getvalue(),headers={'Content-Type':'image/jpeg','X-Filename':'synthetic-mobile.jpg'})
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();result=client.post('/api/reconstruct',json={'packet':data['data'],'layout_evidence':data['layout_evidence'],'profile':'ocr-block-grid-s2b-v1','decoder_mode':'constrained'})
            self.assertEqual(result.status_code,200,result.text)
            co=result.json()['canonical_output'];self.assertEqual(len(co['packet']['events'])+len(co['unresolved_candidates']),3)

    def test_numbered_facility_without_a_named_venue_allowlist(self):
        p,s=wrapped_fixture(['QA1701 (P)','Activity Hall 2','Alex River','9:00 AM - 11:00 AM'])
        self.assertEqual(values(p,s)[0]['location'],'ACTIVITY HALL 2')
