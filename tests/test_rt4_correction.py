"""Synthetic RT4 field corrections; no private screenshots or dictionaries."""
from hashlib import sha256
from io import BytesIO
import unittest
from PIL import Image,ImageDraw,ImageFont
from backend.models.extraction import DocumentEvidence,EvidencePage
from backend.layout.block_capture import capture_words,detect_regions
from backend.layout.block_models import BlockPage,BlockEvidence
from backend.layout.block_grid import reconstruct_blocks,block_graph
from backend.services.extraction_adapter import adapt_extraction


def correction_fixture(rows=None, *, second=None, axes=True, alternate=None, alternate_confidence=90):
    rows=rows or ['QA1701 (L)','HX C','ALEX RIVER','9:00 AM - 11:00 AM']
    xs=[120,260,400,540,680,820]
    image=Image.new('RGB',(900,480),'white');draw=ImageDraw.Draw(image);font=ImageFont.load_default(size=12)
    global_rows=[('Mon',15,155),('2033-02-07',15,230)]
    if axes:
        for i,x in enumerate(xs[:-1]):global_rows.extend([(f'{8+i}:00',x+5,50),(f'{9+i}:00',x+5,85)])
    layouts=[(260,539,rows)]
    layouts.extend([(541,679,['QA1701 (T)','D234G','Test Person','11:00 AM - 12:00 PM']),(681,819,['QA1701 (P)','F567H','Test Person','12:00 PM - 1:00 PM'])])
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
    if alternate:
        region=regions[0]
        alt_data=tokens([(t,268,152+i*16) for i,t in enumerate(alternate)])
        alt_data['conf']=[alternate_confidence]*len(alt_data['text'])
        words.extend(capture_words(source,1,alt_data,lines,'s2b-region-light-text-ocr-v2',{'operation':'TOKEN_ORACLE'},region.region_id))
    raw=DocumentEvidence(source_id=source,route_type='upload_image',original_label='invented-four-line.png',media_type='image/png',checksum=checksum,
        parser_version='SYNTHETIC_TOKEN_ORACLE',pages=[EvidencePage(page=1,width=900,height=480,regions=lines)])
    page=BlockPage(page=1,width=900,height=480,route='synthetic-context',transform={'operation':'TOKEN_ORACLE'},runtime={'status':'NO_OCR'},
        words=tuple(words),regions=regions,vertical_rules=tuple(xs),horizontal_rules=(140,300),detection_operations=ops)
    return adapt_extraction(raw),BlockEvidence(source_document_id=source,content_sha256=checksum,pages=(page,))


from backend.services.ocr_correction import correct_field, established_formats
from tests.test_four_line_mapping import values
from fastapi.testclient import TestClient
from backend.main import app

class RT4FieldTests(unittest.TestCase):
    def test_o_in_known_numeric_position(self):
        r=correct_field('location','B0O1J',formats={'ADDDA':[]})
        self.assertEqual((r['status'],r['suggested']),('SUPPORTED','B001J'))
        self.assertEqual(r['original'],'B0O1J')

    def test_i_in_known_numeric_position(self):
        self.assertEqual(correct_field('location','B00IJ',formats={'ADDDA':[]})['suggested'],'B001J')

    def test_parenthesis_requires_additional_crop_evidence(self):
        self.assertEqual(correct_field('location','B001)',formats={'ADDDA':[]})['status'],'AMBIGUOUS')
        r=correct_field('location','B001)',alternatives=['B001J'])
        self.assertEqual((r['status'],r['suggested']),('SUPPORTED','B001J'))

    def test_legitimate_letters_and_digits_unchanged(self):
        for raw in ['OX203','IO208J','B001J','PA103','DK O','DK ABA']:
            self.assertEqual(correct_field('location',raw,formats={'ADDDA':[]})['status'],'UNCHANGED',raw)

    def test_multiletter_prefix_numeric_confusion(self):
        self.assertEqual(correct_field('location','TA2S5',formats={'AADDD':[]})['suggested'],'TA255')

    def test_free_text_locations_unchanged(self):
        for raw in ['Open Studio','Innovation Hall 2','Room O','West Wing']:
            self.assertEqual(correct_field('location',raw)['status'],'UNCHANGED')

    def test_unknown_valid_format_not_rewritten(self):
        self.assertEqual(correct_field('location','QB123',formats={'ADDDD':[]})['status'],'UNCHANGED')

    def test_unknown_ambiguous_format_requests_review(self):
        self.assertEqual(correct_field('location','B0O1J')['status'],'AMBIGUOUS')

    def test_competing_format_interpretations(self):
        r=correct_field('location','BOO1J',formats={'ADDDA':[],'AADDA':[]})
        self.assertEqual(r['status'],'AMBIGUOUS')

    def test_alternative_crop_competes_with_format(self):
        r=correct_field('location','B0O1J',formats={'ADDDA':[]},alternatives=['BO01J'])
        self.assertEqual(r['status'],'AMBIGUOUS')
        self.assertEqual(correct_field('location','BO123',alternatives=['B0123'])['status'],'AMBIGUOUS')

    def test_course_legitimate_suffix_preserved(self):
        for raw in ['AB1234C','QAZ-1234','SO1234']:
            self.assertEqual(correct_field('course_code',raw)['status'],'UNCHANGED')

    def test_course_correction_requires_support(self):
        self.assertEqual(correct_field('course_code','QAZ-12S4')['status'],'AMBIGUOUS')
        self.assertEqual(correct_field('course_code','QAZ-12S4',formats={'AAA-DDDD':[]})['suggested'],'QAZ-1254')

    def test_type_no_global_digit_replacement(self):
        self.assertEqual(correct_field('session_type','7')['status'],'AMBIGUOUS')
        self.assertEqual(correct_field('session_type','7',alternatives=['T'])['suggested'],'T')
        self.assertEqual(correct_field('session_type','1',alternatives=['T','L'])['status'],'AMBIGUOUS')

    def test_time_requires_same_crop_support_and_valid_range(self):
        raw='1O:00 AM - 11:00 AM'
        self.assertEqual(correct_field('time_range',raw)['status'],'AMBIGUOUS')
        self.assertEqual(correct_field('time_range',raw,alternatives=['10:00 AM - 11:00 AM'])['status'],'SUPPORTED')
        self.assertNotEqual(correct_field('time_range','1O:00 - 11:00',alternatives=['10:00 - 11:00'])['status'],'SUPPORTED')
        self.assertNotEqual(correct_field('time_range','1O:99 AM - 11:00 AM',alternatives=['10:99 AM - 11:00 AM'])['status'],'SUPPORTED')

    def test_names_are_never_character_corrected(self):
        for name in ['Olivia Iris Jones Smith','Omar Ismail','Jill Osei','Jo OIJS']:
            r=correct_field('lecturer',name,formats={'ADDDA':[]},alternatives=['Different Name'])
            self.assertEqual((r['status'],r['original']),('UNCHANGED',name))

    def test_format_requires_distinct_values_and_regions(self):
        witness=lambda value,region,confidence=.9:dict(text=value,region_id=region,confidence=confidence,word_ids=[])
        self.assertFalse(established_formats([witness('X123J','a'),witness('X123J','b')],'target'))
        self.assertFalse(established_formats([witness('X123J','a'),witness('Y234K','a')],'target'))
        self.assertFalse(established_formats([witness('X123J','a'),witness('Y234K','target')],'target'))
        self.assertFalse(established_formats([witness('X123J','a'),witness('Y234K','b',.2)],'target'))
        self.assertFalse(established_formats([witness('X123J','a'),witness('Y234K','b',None)],'target'))
        self.assertIn('ADDDA',established_formats([witness('X123J','a'),witness('Y234K','b')],'target'))

    def test_bounded_candidate_generation(self):
        self.assertEqual(correct_field('location','O'*1000)['status'],'UNCHANGED')
        self.assertLessEqual(len(correct_field('location','BOO1J')['candidates']),32)

class RT4GraphTests(unittest.TestCase):
    def fixture(self):
        return correction_fixture(['QA1701 (L)','X0O1J','Olivia Iris Jones','9:00 AM - 11:00 AM'])

    def test_ablation_and_original_evidence(self):
        p,s=self.fixture();before=p.model_dump();sidecar=s.model_dump()
        off=reconstruct_blocks(p,s)[0]['canonical_output']
        on=reconstruct_blocks(p,s.model_copy(update={'correction_mode':'context-v1'}))[0]['canonical_output']
        self.assertEqual(before,p.model_dump());self.assertEqual(sidecar,s.model_dump())
        self.assertFalse(any(c['revision']['content']['location']=='X001J' for c in off['packet']['events']+off['unresolved_candidates']))
        event=next(e for e in on['packet']['events']+on['unresolved_candidates'] if e['revision']['content']['location']=='X001J')
        self.assertEqual(event['revision']['content']['lecturer'],'Olivia Iris Jones')
        self.assertEqual(event['human_verification']['status'],'NOT_VERIFIED');self.assertEqual(event['calendar']['approval_status'],'NOT_APPROVED')
        correction=next(c for d in on['layout']['regions'] for c in d.get('ocr_corrections',[]) if c['status']=='SUPPORTED')
        self.assertEqual(correction['original'],'X0O1J');self.assertTrue(correction['original_confidence']);self.assertEqual(len(correction['format_evidence']),2)
        self.assertTrue(correction['source_word_ids']);self.assertEqual(correction['bbox']['coordinate_space'],'PIXELS')

    def test_correction_is_bound_in_policy_digest(self):
        p,s=self.fixture();s=s.model_copy(update={'correction_mode':'context-v1'});r=reconstruct_blocks(p,s)[0]
        body={'source_packet':p.model_dump(mode='json'),'layout_evidence':s.model_dump(mode='json'),'reconstruction':{'canonical_digest':r['canonical_digest']}}
        with TestClient(app) as client:
            response=client.post('/api/layout/s2b/assess',json=body);self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(all(d['decision']=='ABSTAIN' for d in response.json()['decisions']))
            body['layout_evidence']['correction_mode']='off'
            self.assertEqual(client.post('/api/layout/s2b/assess',json=body).status_code,422)

    def test_missing_layout_context_blocks_auto_correction(self):
        p,s=correction_fixture(['QA1701 (L)','X0O1J','Olivia Iris Jones','9:00 AM - 11:00 AM'],axes=False)
        s=s.model_copy(update={'correction_mode':'context-v1'})
        result=reconstruct_blocks(p,s)[0]['canonical_output']
        self.assertFalse(any(e['revision']['content']['location']=='X001J' for e in result['packet']['events']+result['unresolved_candidates']))

    def test_time_correction_has_original_and_alternative_source_refs(self):
        rows=['QA1701 (L)','X123J','Olivia Iris','9:O0 AM - 11:00 AM']
        alt=rows[:-1]+['9:00 AM - 11:00 AM']
        p,s=correction_fixture(rows,alternate=alt);s=s.model_copy(update={'correction_mode':'context-v1'})
        co=reconstruct_blocks(p,s)[0]['canonical_output']
        correction=next(c for d in co['layout']['regions'] for c in d.get('ocr_corrections',[]) if c['field']=='time_range')
        self.assertEqual(correction['status'],'SUPPORTED');self.assertTrue(correction['alternative_evidence'][0]['word_ids'])
        self.assertEqual(correction['original'],'9:O0 AM - 11:00 AM')
        self.assertTrue(any(e['revision']['content']['start_time']=='09:00' for e in co['packet']['events']+co['unresolved_candidates']))

    def test_correction_ui_and_own_region_binding(self):
        import subprocess
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        run=subprocess.run(['node',str(root/'tests/rt4_correction_checks.js')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_low_confidence_alternative_cannot_authorize_j_repair(self):
        rows=['QA1701 (L)','X001)','Olivia Iris','9:00 AM - 11:00 AM']
        alt=[rows[0],'X001J',*rows[2:]]
        p,s=correction_fixture(rows,alternate=alt,alternate_confidence=20)
        co=reconstruct_blocks(p,s.model_copy(update={'correction_mode':'context-v1'}))[0]['canonical_output']
        correction=next(c for d in co['layout']['regions'] for c in d.get('ocr_corrections',[]) if c['field']=='location')
        self.assertEqual(correction['status'],'AMBIGUOUS');self.assertIn('X001J',correction['alternative_readings'])
        self.assertEqual(correction['alternative_evidence'][0]['confidence'],[.2])
