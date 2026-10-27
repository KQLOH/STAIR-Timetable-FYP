"""Synthetic software diagnostics only: no dataset labels or research evaluation."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import copy
import json
import unittest
import subprocess

from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.file_extractor import _ocr_image, extract_document
from backend.services.extraction_adapter import adapt_extraction
from backend.models.extraction import DocumentEvidence
from backend.models.event import CanonicalTimetable
from backend.layout.ruled_models import RuledEvidence, PROFILE, MAX_WORDS
from backend.layout.ruled import (reconstruct_ruled, ruled_graph, cells, assess_ruled,
                                 CONFIG, CONFIG_HASH)
from backend.layout.ruled_capture import visible_grid
from backend.layout.service import reconstruct_layout
from backend.reconstruction.service import reconstruct
from tests.s1_functional_audit import grid, png, pdf

ROOT=Path(__file__).resolve().parents[1]


def fixture(merged=False,rows=None,scale=1,reverse=False,image=None):
    """Known synthetic OCR tokens over a generated grid; confidence unavailable."""
    image=image or Image.open(BytesIO(grid(merged)))
    rows=rows or [('24-HOUR SYNTHETIC TIMETABLE - TEST DATA',20,15),
        ('09:00-11:00',200,100),('11:00-13:00',850,100),('Monday',20,195),
        ('CS201 MERGED SESSION' if merged else 'CS101 NETWORKS',210,180),
        ('ROOM A101 GROUP G1',210,235),('Tuesday',20,380),
        ('CS303 DATABASES',210,370),('ROOM C303',210,425)] + ([] if merged else
        [('MA202 CALCULUS',850,180),('ROOM B202 GROUP G2',850,235)])
    font=ImageFont.load_default(size=27)
    data={k:[] for k in ['text','left','top','width','height','block_num','par_num','line_num','conf']}
    for line,(text,x,y) in enumerate(rows,1):
        for word in text.split():
            width=font.getlength(word)
            for k,v in dict(text=word,left=round(x*scale),top=round((y+5)*scale),
                width=max(1,round(width*scale)),height=round(25*scale),block_num=1,par_num=1,line_num=line,conf=-1).items():data[k].append(v)
            x+=width+font.getlength(' ')
    if reverse:data={k:list(reversed(v)) for k,v in data.items()}
    if scale!=1:image=image.resize((round(image.width*scale),round(image.height*scale)))
    collector=[]
    with patch('backend.services.file_extractor.recognize',return_value=(data,{'engine_version':'SYNTHETIC_STUB','config':'not a real OCR execution'})):
        page=_ocr_image('synthetic-s2a',image,1,'SYNTHETIC_TEST_OCR',collector=collector)
    document=DocumentEvidence(source_id='synthetic-s2a',route_type='upload_image',original_label='synthetic.png',
        media_type='image/png',checksum='1'*64,parser_version='synthetic-test',pages=[page])
    packet=adapt_extraction(document)
    return packet,RuledEvidence(source_document_id=document.source_id,content_sha256=document.checksum,pages=collector)


def output(packet,sidecar,mode='constrained'):
    return reconstruct_ruled(packet,sidecar,mode)[0]


def events(result):
    c=result['canonical_output']
    return c['packet']['events']+c['unresolved_candidates']


def contents(result):return [e['revision']['content'] for e in events(result)]


class RuledContractTests(unittest.TestCase):
    def test_frontend_rejects_rotated_or_mismatched_pixel_overlay(self):
        from tests.test_m51_integration import NODE
        script=r'''
const fs=require('fs'),vm=require('vm');const context={document:{createElement:()=>({dataset:{},style:{},setAttribute(){},addEventListener(){}})}};
context.globalThis=context;vm.runInNewContext(fs.readFileSync('frontend/js/upload.js','utf8'),context);
let count=0;const frame={querySelectorAll:()=>[],append:()=>count++};
const image={naturalWidth:100,naturalHeight:100};
const result={layoutEvidence:{pages:[{page:1,width:100,height:100,transform:{source_exif_orientation:6}}]},
extraction:{evidence:[{evidence_id:'synthetic',page:1,bbox:{coordinate_space:'PIXELS',x0:1,y0:1,x1:20,y1:20}}]}};
context.STAIRUpload.drawBoxes(frame,image,result);if(count!==0)throw Error('Rotated overlay must be disabled');
result.layoutEvidence.pages[0].transform.source_exif_orientation=1;
context.STAIRUpload.drawBoxes(frame,image,result);if(count!==1)throw Error('Supported overlay missing');
result.layoutEvidence.pages[0].width=200;
context.STAIRUpload.drawBoxes(frame,image,result);if(count!==1)throw Error('Different pixel dimensions must not mix');
'''
        run=subprocess.run([NODE,'-e',script],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_grid_relationships_and_word_field_ownership(self):
        p,s=fixture();r=output(p,s);c=r['canonical_output'];got={x['course_code']:x for x in contents(r)}
        self.assertEqual(set(got),{'CS101','MA202','CS303'})
        expected={'CS101':(1,'09:00','11:00','A101','G1'),'MA202':(1,'11:00','13:00','B202','G2'),'CS303':(2,'09:00','11:00','C303',None)}
        spans={x['evidence_id']:x for x in c['packet']['evidence']}
        for e in events(r):
            v=e['revision']['content'];self.assertEqual(tuple(v[k] for k in ['day_of_week','start_time','end_time','location','section']),expected[v['course_code']])
            for field in ['course_code','day_of_week','start_time','end_time','location']:
                obs=next(o for o in e['revision']['observations'] if o['field_name']==field)
                words=[spans[i]['raw_text'] for i in obs['evidence_ids'] if i.startswith('s2a-word-')]
                self.assertTrue(words,(field,v));self.assertFalse({'B202','G2'} & set(words) if v['course_code']=='CS101' else False)
            for field in ['date','source_timezone','recurrence','session_type']:self.assertIsNone(v[field])
        self.assertEqual(len(c['packet']['events']),3);CanonicalTimetable.model_validate(c['packet'])

    def test_merged_printed_contiguous_span(self):
        p,s=fixture(True);v={x['course_code']:x for x in contents(output(p,s))}
        self.assertEqual((v['CS201']['start_time'],v['CS201']['end_time'],v['CS201']['day_of_week']),('09:00','13:00',1))
        self.assertEqual((v['CS303']['start_time'],v['CS303']['end_time']),('09:00','11:00'))

    def test_interrupted_separator_is_ambiguous(self):
        p,s=fixture();g=copy.deepcopy(s.pages[0].grid);g['vertical_coverage'][1][2]=.5
        s=s.model_copy(update={'pages':(s.pages[0].model_copy(update={'grid':g}),)})
        r=output(p,s);self.assertTrue(any(c['ambiguous'] for c in r['canonical_output']['layout']['cells']))
        self.assertFalse(any(v['day_of_week']==1 and v['start_time'] for v in contents(r)))

    def test_noncontiguous_headers_do_not_invent_span(self):
        rows=[('24-HOUR TIMETABLE',20,15),('09:00-10:00',200,100),('11:00-13:00',850,100),('Monday',20,195),('CS201 MERGED SESSION',210,180)]
        p,s=fixture(True,rows=rows);r=output(p,s);v=contents(r)[0]
        self.assertIsNone(v['start_time']);self.assertIsNone(v['end_time']);self.assertEqual(v['course_code'],'CS201')

    def test_single_time_label_never_invents_end(self):
        p,s=fixture(rows=[('24-HOUR TIMETABLE',20,15),('09:00',200,100),('Monday',20,195),('CS101 NETWORKS',210,180)])
        result=output(p,s);v=contents(result)[0];self.assertIsNone(v['start_time']);self.assertIsNone(v['end_time'])
        for field in ['start_time','end_time']:
            observation=next(o for o in events(result)[0]['revision']['observations'] if o['field_name']==field)
            self.assertEqual(observation['state'],'UNRESOLVED');self.assertTrue(observation['evidence_ids'])

    def test_unreadable_range_and_unknown_room(self):
        p,s=fixture(rows=[('24-HOUR TIMETABLE',20,15),('??:00-11:00',200,100),('Monday',20,195),('CS101 NETWORKS',210,180),('ROOM TBA GROUP UNKNOWN',210,235)])
        e=events(output(p,s))[0];v=e['revision']['content'];self.assertIsNone(v['start_time']);self.assertIsNone(v['location']);self.assertIsNone(v['section'])
        self.assertEqual(next(o['state'] for o in e['revision']['observations'] if o['field_name']=='location'),'UNRESOLVED')
        self.assertEqual(next(o['state'] for o in e['revision']['observations'] if o['field_name']=='start_time'),'UNRESOLVED')

    def test_no_clock_authority_no_am_pm_inference(self):
        p,s=fixture(rows=[('09:00-11:00',200,100),('Monday',20,195),('CS101 NETWORKS',210,180)])
        self.assertIsNone(contents(output(p,s))[0]['start_time'])

    def test_explicit_meridiem_range(self):
        p,s=fixture(rows=[('9AM-11AM',200,100),('Monday',20,195),('CS101 NETWORKS',210,180)])
        self.assertEqual(contents(output(p,s))[0]['start_time'],'09:00')

    def test_missing_day_is_not_monday(self):
        p,s=fixture(rows=[('24-HOUR TIMETABLE',20,15),('09:00-11:00',200,100),('CS101 NETWORKS',210,180)])
        self.assertIsNone(contents(output(p,s))[0]['day_of_week'])

    def test_long_name_and_labeled_metadata(self):
        p,s=fixture(rows=[('24-HOUR TIMETABLE',20,15),('09:00-11:00',200,100),('Monday',20,195),
            ('CS101 Distributed Database',210,170),('Systems and Applications',210,205),('Lecture ROOM A101 GROUP G1',210,245),('Lecturer: Ada Lovelace',210,280)])
        v=contents(output(p,s))[0];self.assertEqual(v['course_name'],'Distributed Database Systems and Applications')
        self.assertEqual((v['session_type'],v['location'],v['section'],v['lecturer']),('Lecture','A101','G1','Ada Lovelace'))

    def test_compound_cell_preserves_session_local_details(self):
        p,s=fixture(rows=[('24-HOUR TIMETABLE',20,15),('Monday',20,195),
            ('CS101 09:00-10:00 Lecture ROOM A1 GROUP G1;',210,170),('11:00-12:00 Tutorial ROOM B1 GROUP G2',210,235)])
        got={v['session_type']:v for v in contents(output(p,s))}
        self.assertEqual(set(got),{'Lecture','Tutorial'})
        self.assertEqual((got['Lecture']['start_time'],got['Lecture']['location'],got['Lecture']['section']),('09:00','A1','G1'))
        self.assertEqual((got['Tutorial']['start_time'],got['Tutorial']['location'],got['Tutorial']['section'],got['Tutorial']['day_of_week']),('11:00','B1','G2',1))

    def test_one_line_shared_day_and_conflicting_days(self):
        for text,expected in [('CS101 Monday 09:00-10:00 Lecture; 11:00-12:00 Tutorial',[1,1]),
                ('CS101 Monday 09:00-10:00 Lecture; Tuesday 11:00-12:00 Tutorial',[1,2])]:
            image=Image.new('RGB',(1800,180),'white');p,s=fixture(rows=[(text,20,30)],image=image)
            self.assertEqual(sorted(v['day_of_week'] for v in contents(output(p,s))),expected)

    def test_scale_and_alternate_ocr_order(self):
        reference=sorted(contents(output(*fixture())),key=lambda v:v['course_code'])
        for options in [{'scale':1.5},{'reverse':True}]:
            self.assertEqual(sorted(contents(output(*fixture(**options))),key=lambda v:v['course_code']),reference)

    def test_repeat_determinism_and_matched_application_diagnostic(self):
        p,s=fixture();a=output(p,s);b=output(p,s);u=output(p,s,'unconstrained')
        self.assertEqual(a['canonical_digest'],b['canonical_digest'])
        self.assertEqual(a['canonical_output']['base_score_digest'],u['canonical_output']['base_score_digest'])
        self.assertEqual(a['canonical_output']['candidate_graph_digest'],u['canonical_output']['candidate_graph_digest'])
        self.assertEqual(u['canonical_output']['method_id'],'B3-matched-app-s2a-v1')

    def test_original_paths_unmodified_by_variant_execution(self):
        p,s=fixture();m4=reconstruct(p,'constrained')['canonical_digest'];m52=reconstruct_layout(p)[0]['canonical_digest']
        before=p.model_dump_json();output(p,s)
        self.assertEqual(before,p.model_dump_json());self.assertEqual(m4,reconstruct(p,'constrained')['canonical_digest']);self.assertEqual(m52,reconstruct_layout(p)[0]['canonical_digest'])

    def test_raw_lines_words_and_no_fabricated_confidence(self):
        p,s=fixture();r=output(p,s);spans={v['evidence_id']:v for v in r['canonical_output']['packet']['evidence']}
        for line in p.evidence:
            sid=r['execution_metadata']['capture_aliases'][line.evidence_id]
            self.assertEqual(spans[sid]['raw_text'],line.raw_text);self.assertEqual(spans[sid]['bbox'],line.bbox.model_dump())
        for w in s.pages[0].words:
            self.assertEqual(spans[w.evidence_id]['raw_text'],w.text);self.assertIsNone(spans[w.evidence_id]['confidence']);self.assertIn(w.parent_line_id,{a.evidence_id for a in p.evidence})

    def test_source_page_space_and_word_id_tampering_rejected(self):
        p,s=fixture()
        for key,value in [('source_document_id','other'),('content_sha256','0'*64)]:
            with self.assertRaises(ValueError):output(p,s.model_copy(update={key:value}))
        w=s.pages[0].words[0]
        for change in [{'parent_line_id':'missing'},{'evidence_id':'fabricated'}]:
            page=s.pages[0].model_copy(update={'words':(w.model_copy(update=change),)+s.pages[0].words[1:]})
            with self.assertRaises(ValueError):output(p,s.model_copy(update={'pages':(page,)}))

    def test_overlapping_boxes_are_unresolved(self):
        p,s=fixture();page=s.pages[0];ws=list(page.words);course=next(w for w in ws if w.text=='CS101');name=next(w for w in ws if w.text=='NETWORKS')
        ws[ws.index(name)]=name.model_copy(update={'bbox':course.bbox})
        cs,issues=cells(page.model_copy(update={'words':tuple(ws)}))
        self.assertTrue(any(i['code']=='OVERLAPPING_WORD_OWNERSHIP' for i in issues));self.assertTrue(any(c['ambiguous'] and 'CS101' in c['text'] for c in cs))

    def test_empty_and_outside_cells_preserve_evidence(self):
        p,s=fixture();r=output(p,s);self.assertTrue(any(not c['word_ids'] for c in r['canonical_output']['layout']['cells']))
        self.assertTrue(any(i['code']=='WORD_OUTSIDE_CELLS' for i in r['canonical_output']['layout']['issues']))
        self.assertTrue(any('24-HOUR' in e['raw_text'] for e in r['canonical_output']['packet']['evidence']))

    def test_faint_missing_and_transposed_family_are_not_claimed_supported(self):
        self.assertEqual(visible_grid(Image.new('RGB',(500,400),'white'))['status'],'NO_SUPPORTED_GRID')
        # Pixel measurements alone cannot determine semantic orientation. A
        # transposed day-column/time-row family must not become supported events.
        p,s=fixture(rows=[('24-HOUR TIMETABLE',20,15),('Monday',200,100),('Tuesday',850,100),
                          ('09:00-11:00',20,195),('CS101 NETWORKS',210,180)])
        r=output(p,s);self.assertFalse(r['canonical_output']['packet']['events'])
        self.assertIsNone(contents(r)[0]['day_of_week']);self.assertIsNone(contents(r)[0]['start_time'])
        image=Image.new('RGB',(500,400),'white');d=ImageDraw.Draw(image)
        for x in [10,150,490]:d.line((x,20,x,390),fill=(210,210,210),width=3)
        for y in [20,80,390]:d.line((10,y,490,y),fill=(210,210,210),width=3)
        self.assertEqual(visible_grid(image)['status'],'NO_SUPPORTED_GRID')

    def test_repeated_header_nearest_printed_band(self):
        image=Image.new('RGB',(1500,650),'white');draw=ImageDraw.Draw(image)
        for y in [80,150,330,400,640]:draw.line((10,y,1490,y),fill='black',width=3)
        for x in [10,170,820,1490]:draw.line((x,80,x,640),fill='black',width=3)
        p,s=fixture(image=image,rows=[('24-HOUR TIMETABLE',20,15),('09:00-11:00',200,100),
            ('Monday',20,200),('CS101 NETWORKS',210,180),('14:00-16:00',200,350),
            ('Tuesday',20,450),('MA202 CALCULUS',210,430)])
        v={x['course_code']:x for x in contents(output(p,s))}
        self.assertEqual((v['CS101']['day_of_week'],v['CS101']['start_time']),(1,'09:00'))
        self.assertEqual((v['MA202']['day_of_week'],v['MA202']['start_time']),(2,'14:00'))

    def test_missing_internal_separator_preserves_competing_courses(self):
        p,s=fixture();g=copy.deepcopy(s.pages[0].grid);g['vertical_coverage'][1][2]=0
        s=s.model_copy(update={'pages':(s.pages[0].model_copy(update={'grid':g}),)})
        r=output(p,s)
        self.assertFalse(any(v['day_of_week']==1 and v['start_time'] for v in contents(r)))
        alternatives={a['raw_value'] for e in events(r) for o in e['revision']['observations'] if o['field_name']=='course_code' for a in o['alternatives']}
        self.assertTrue({'CS101','MA202'}<=alternatives)

    def test_caps_fail_explicitly(self):
        p,s=fixture()
        with patch.dict(CONFIG,{'max_operations':1}):
            with self.assertRaisesRegex(ValueError,'OPERATION_CAP'):output(p,s)
        g=copy.deepcopy(s.pages[0].grid);g['xs']=list(range(1,25));g['ys']=list(range(1,25));g['vertical_coverage']=[[1]*24 for _ in range(23)]
        with self.assertRaisesRegex(ValueError,'CELL_CAP'):cells(s.pages[0].model_copy(update={'grid':g}))
        raw=s.model_dump(mode='json');raw['pages'][0]['width']=9000000
        with self.assertRaises(ValueError):RuledEvidence.model_validate(raw)
        raw=s.model_dump(mode='json');raw['pages']*=9
        with self.assertRaises(ValueError):RuledEvidence.model_validate(raw)
        raw=s.model_dump(mode='json');raw['pages'][0]['words']*=MAX_WORDS
        with self.assertRaises(ValueError):RuledEvidence.model_validate(raw)
        with patch('backend.layout.ruled.MAX_GROUPS',1):
            with self.assertRaisesRegex(ValueError,'GROUP_CAP'):output(p,s)

    def test_policy_stays_abstain_and_envelope_tampering_rejected(self):
        p,s=fixture();r=output(p,s);decisions=assess_ruled(p,s,r)
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in decisions))
        bad=copy.deepcopy(r);bad['canonical_output']['packet']['events'][0]['revision']['content']['location']='MADEUP'
        with self.assertRaisesRegex(ValueError,'MISMATCH'):assess_ruled(p,s,bad)


class RuledHTTPAndLiveOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.client=TestClient(app);cls.client.__enter__()
    @classmethod
    def tearDownClass(cls):cls.client.__exit__(None,None,None)

    def extract(self,name,raw,mime='image/png',suffix=''):
        return self.client.post('/api/extract?layout_profile='+PROFILE+suffix,content=raw,headers={'X-Filename':name,'Content-Type':mime})

    def test_actual_grid_ocr_api_and_policy_calendar_safety(self):
        x=self.extract('synthetic-grid.png',grid());self.assertEqual(x.status_code,200,x.text[:300]);x=x.json()
        words=[w for p in x['layout_evidence']['pages'] for w in p['words']]
        self.assertTrue(any(w['confidence'] is not None and 0<w['confidence']<=1 for w in words))
        self.assertEqual(x['layout_evidence']['pages'][0]['transform']['rotation_applied_degrees'],0)
        q={'packet':x['data'],'layout_evidence':x['layout_evidence'],'profile':PROFILE,'decoder_mode':'constrained'}
        r=self.client.post('/api/reconstruct',json=q);self.assertEqual(r.status_code,200,r.text[:300]);r=r.json()
        got={v['course_code']:v for v in contents(r)};self.assertEqual(set(got),{'CS101','MA202','CS303'})
        self.assertEqual((got['MA202']['location'],got['MA202']['start_time'],got['CS303']['day_of_week']),('B202','11:00',2))
        for e in events(r):
            self.assertEqual(e['human_review']['status'],'NOT_REVIEWED');self.assertEqual(e['human_verification']['status'],'NOT_VERIFIED')
            self.assertEqual(e['calendar']['approval_status'],'NOT_APPROVED');self.assertEqual(e['calendar']['sync_status'],'NOT_SUBMITTED')
        pr=self.client.post('/api/layout/s2a/assess',json={'source_packet':x['data'],'layout_evidence':x['layout_evidence'],'reconstruction':r})
        self.assertEqual(pr.status_code,200,pr.text[:300]);self.assertTrue(all(d['decision']=='ABSTAIN' for d in pr.json()['decisions']))
        bundle={'packet':r['canonical_output']['packet'],'context':{},'assessments':[],'reconstruction_status':r['canonical_output']['status']}
        calendar=self.client.post('/api/calendar/v1/preview',json={'bundle':bundle}).json();self.assertFalse(calendar['write_authorized'])

    def test_actual_merged_and_compound_ocr(self):
        for name,raw in [('merged',grid(True)),('compound',png(['CS101 Monday 09:00-10:00 Lecture; 11:00-12:00 Tutorial']))]:
            x=self.extract(name+'.png',raw).json();r=output(CanonicalTimetable.model_validate(x['data']),RuledEvidence.model_validate(x['layout_evidence']))
            got=contents(r)
            if name=='merged':
                v=next(v for v in got if v['course_code']=='CS201');self.assertEqual((v['start_time'],v['end_time']),('09:00','13:00'))
            else:self.assertEqual({(v['session_type'],v['day_of_week']) for v in got},{('Lecture',1),('Tutorial',1)})

    def test_actual_multipage_pdf_scaling_no_cross_page_join(self):
        x=self.extract('synthetic.pdf',pdf(),'application/pdf');self.assertEqual(x.status_code,200,x.text[:300]);x=x.json()
        self.assertEqual(x['data']['source']['route'],'ocr');self.assertEqual({s['page'] for s in x['data']['evidence']},{1,2})
        self.assertTrue(all(p['transform']['dpi']==200 and p['transform']['scale_x']>2 for p in x['layout_evidence']['pages']))
        r=output(CanonicalTimetable.model_validate(x['data']),RuledEvidence.model_validate(x['layout_evidence']));spans={s['evidence_id']:s for s in r['canonical_output']['packet']['evidence']}
        for e in events(r):
            refs={spans[i]['page'] for o in e['revision']['observations'] for i in o['evidence_ids']};self.assertEqual(len(refs),1)
        self.assertEqual({v['course_code'] for v in contents(r)},{'CS101','MA202'})

    def test_unknown_mixed_profiles_and_malformed_uploads_rejected(self):
        self.assertEqual(self.client.post('/api/extract?layout_profile=unknown',content=b'x').status_code,422)
        self.assertEqual(self.extract('x.png',grid(),suffix='&pipeline=B2').status_code,422)
        self.assertEqual(self.extract('bad.png',b'bad').status_code,422)
        p,s=fixture();q={'packet':p.model_dump(mode='json'),'profile':PROFILE,'decoder_mode':'constrained'}
        self.assertEqual(self.client.post('/api/reconstruct',json=q).status_code,422)
        q.update(layout_evidence=s.model_dump(mode='json'),profile='m4-development-v1')
        self.assertEqual(self.client.post('/api/reconstruct',json=q).status_code,422)

    def test_default_extraction_keeps_native_pdf_and_no_supplement(self):
        r=self.client.post('/api/extract',content=pdf(),headers={'X-Filename':'synthetic.pdf','Content-Type':'application/pdf'})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['data']['source']['route'],'native_pdf');self.assertNotIn('layout_evidence',r.json())


if __name__=='__main__':unittest.main()
