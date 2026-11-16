"""Invented four-line token-oracle tests; no private timetable content or coordinates."""
from hashlib import sha256
from io import BytesIO
import unittest
from PIL import Image,ImageDraw,ImageFont
from backend.models.extraction import DocumentEvidence,EvidencePage
from backend.layout.block_capture import capture_words,detect_regions
from backend.layout.block_models import BlockPage,BlockEvidence
from backend.layout.block_grid import reconstruct_blocks,block_graph
from backend.services.extraction_adapter import adapt_extraction


def fixture(rows=None, *, second=None, axes=True):
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
        for i,t in enumerate(texts):draw.text((left+8,152+i*28),t,font=font,fill='white')
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
        words.extend(capture_words(source,1,tokens([(t,left+8,152+i*28) for i,t in enumerate(texts)]),lines,'synthetic-crop',{'operation':'TOKEN_ORACLE'},region.region_id))
    raw=DocumentEvidence(source_id=source,route_type='upload_image',original_label='invented-four-line.png',media_type='image/png',checksum=checksum,
        parser_version='SYNTHETIC_TOKEN_ORACLE',pages=[EvidencePage(page=1,width=900,height=480,regions=lines)])
    page=BlockPage(page=1,width=900,height=480,route='synthetic-context',transform={'operation':'TOKEN_ORACLE'},runtime={'status':'NO_OCR'},
        words=tuple(words),regions=regions,vertical_rules=tuple(xs),horizontal_rules=(140,300),detection_operations=ops)
    return adapt_extraction(raw),BlockEvidence(source_document_id=source,content_sha256=checksum,pages=(page,))


def values(p,s):
    r=reconstruct_blocks(p,s)[0]['canonical_output']
    return [e['revision']['content'] for e in r['packet']['events']+r['unresolved_candidates']]


class FourLineMappingTests(unittest.TestCase):
    def test_lecture(self):
        p,s=fixture();v=values(p,s)[0]
        self.assertEqual((v['session_type'],v['location'],v['lecturer']),('Lecture','HX C','Alex River'))
        self.assertIsNone(v['section']);self.assertIsNone(v['course_name'])

    def test_practical_capitalization(self):
        p,s=fixture(['QA1701 (P)','q001j','alex river','9:00 AM - 11:00 AM'])
        self.assertEqual((values(p,s)[0]['location'],values(p,s)[0]['lecturer']),('Q001J','Alex River'))

    def test_tutorial(self):
        p,s=fixture(['QA1701 (T)','RB215','Alex River','9:00 AM - 11:00 AM'])
        self.assertEqual((values(p,s)[0]['session_type'],values(p,s)[0]['location']),('Tutorial','RB215'))

    def test_word_splitting_and_case(self):
        p,s=fixture(['qa 1701 ( l )','q 001 j','ALEX RIVER','9:00 am - 11:00 am'])
        v=values(p,s)[0];self.assertEqual((v['course_code'],v['location'],v['lecturer']),('QA1701','Q001J','Alex River'))

    def test_compact_alpha_no_unsupported_spacing(self):
        p,s=fixture(['QA1701 (L)','HXC','Alex River','9:00 AM - 11:00 AM'])
        self.assertEqual(values(p,s)[0]['location'],'HXC')

    def test_source_witness_supports_spacing_and_distinct_sessions(self):
        p,s=fixture(['QA1701 (L)','HXD','Alex River','9:00 AM - 11:00 AM'],second=['QA1701 (T)','HX C','Alex River','11:00 AM - 12:00 PM'])
        got=values(p,s);self.assertEqual(len(got),2);self.assertEqual({v['location'] for v in got},{'HX C','HX D'})
        self.assertEqual({v['start_time'] for v in got},{'09:00','11:00'})
        graph,_,_=block_graph(p,s)
        location=next(n for g in graph.groups for n in g.domains['location'] if n.value=='HX D')
        self.assertTrue(any(t.startswith('venue-spacing-prefix-witness:') for t in location.transformations))

    def test_missing_second_line_does_not_promote_name(self):
        p,s=fixture(['QA1701 (L)','Alex River','9:00 AM - 11:00 AM']);v=values(p,s)[0]
        self.assertIsNone(v['location']);self.assertIsNone(v['course_name'])
        graph,_,_=block_graph(p,s);self.assertTrue(any(n.state.value=='UNRESOLVED' for n in graph.groups[0].domains['location']))

    def test_swapped_name_and_venue_unresolved(self):
        p,s=fixture(['QA1701 (L)','Alex River','HX C','9:00 AM - 11:00 AM']);v=values(p,s)[0]
        self.assertIsNone(v['location']);self.assertIsNone(v['lecturer'])

    def test_course_identifier_not_venue(self):
        p,s=fixture(['QA1701 (L)','ZZ2801','Alex River','9:00 AM - 11:00 AM'])
        self.assertIsNone(values(p,s)[0]['location'])

    def test_unknown_layout_no_positional_mapping(self):
        p,s=fixture(axes=False);self.assertIsNone(values(p,s)[0]['location'])

    def test_extra_line_keeps_alphabetic_identifier_uncertain(self):
        p,s=fixture(['QA1701 (L)','HX C','Alex River','9:00 AM - 11:00 AM','Weekly'])
        self.assertIsNone(values(p,s)[0]['location'])

    def test_missing_meridiem_keeps_roles_uncertain(self):
        p,s=fixture(['QA1701 (L)','HX C','Alex River','9:00 - 11:00'])
        self.assertIsNone(values(p,s)[0]['location'])

    def test_provenance_original_text_boxes_confidence_preserved(self):
        p,s=fixture(['QA1701 (L)','hxc','ALEX RIVER','9:00 AM - 11:00 AM']);before=s.model_dump()
        result,graph,_=reconstruct_blocks(p,s)
        self.assertEqual(before,s.model_dump())
        node=next(n for g in graph.groups for n in g.domains['location'] if n.value=='HXC')
        self.assertEqual(node.raw,'hxc');self.assertIn('s2b-four-line-course-venue-lecturer-explicit-time-v1',node.transformations)
        word=next(w for w in s.pages[0].words if w.text=='hxc');self.assertIn(word.evidence_id,node.evidence_ids)
        output=next(e for e in result['canonical_output']['packet']['evidence'] if e['evidence_id']==word.evidence_id)
        self.assertEqual(output['raw_text'],'hxc');self.assertEqual(output['bbox'],word.bbox.model_dump());self.assertEqual(output['confidence'],.9)

    def test_no_status_promotion(self):
        p,s=fixture();r=reconstruct_blocks(p,s)[0]['canonical_output']
        self.assertEqual(len(r['packet']['events']),1)
        e=r['packet']['events'][0];self.assertEqual(e['human_verification']['status'],'NOT_VERIFIED');self.assertEqual(e['calendar']['approval_status'],'NOT_APPROVED')

class FourLineReviewUITests(unittest.TestCase):
    def test_accessible_separate_candidate_and_revision_selection(self):
        import subprocess
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/four_line_review_checks.js')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
