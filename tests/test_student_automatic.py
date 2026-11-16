"""Invented layout routing contracts, no held-out or real student accuracy claims."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from backend.main import app
from backend.layout.automatic import AutomaticCollector
from tests.s1_functional_audit import grid, png, pdf

class StudentAutomaticTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app);self.addCleanup(self.client.close)
    def extract(self,raw,name='invented.png'):
        return self.client.post('/api/extract?layout_profile=auto',content=raw,headers={'Content-Type':'application/pdf' if name.endswith('.pdf') else 'image/png','X-Filename':name})
    def test_native_pdf_stays_native_and_keeps_every_page(self):
        with patch('backend.services.file_extractor.recognize',side_effect=AssertionError('Native PDF must not invoke OCR')):
            response=self.extract(pdf(),'invented.pdf')
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertIsNone(result['layout_profile']);self.assertEqual(result['data']['source']['route'],'native_pdf')
        self.assertEqual({s['page'] for s in result['data']['evidence']},{1,2})
    def test_colored_reader_selected_from_pixels(self):
        raw=(Path(__file__).parent/'synthetic_samples/s2b_preparation/01-two-row-colored.png').read_bytes()
        response=self.extract(raw);self.assertEqual(response.status_code,200,response.text[:300])
        x=response.json();self.assertEqual(x['layout_profile'],'ocr-block-grid-s2b-v1')
        result=self.client.post('/api/reconstruct',json={'packet':x['data'],'layout_evidence':x['layout_evidence'],'profile':x['layout_profile'],'decoder_mode':'constrained'})
        self.assertEqual(result.status_code,200,result.text[:300]);c=result.json()['canonical_output']
        self.assertTrue(any(e['revision']['content']['course_code']=='ZX4101' for e in c['packet']['events']+c['unresolved_candidates']))
    def test_uncolored_grid_and_merged_cell_use_ruled_reader(self):
        for merged in (False,True):
            with self.subTest(merged=merged):
                response=self.extract(grid(merged));self.assertEqual(response.status_code,200,response.text[:300]);x=response.json()
                self.assertEqual(x['layout_profile'],'ocr-ruled-grid-s2a-v1')
                result=self.client.post('/api/reconstruct',json={'packet':x['data'],'layout_evidence':x['layout_evidence'],'profile':x['layout_profile'],'decoder_mode':'constrained'})
                self.assertEqual(result.status_code,200,result.text[:300]);c=result.json()['canonical_output']
                events=c['packet']['events']+c['unresolved_candidates']
                self.assertTrue(any(e['revision']['content']['course_code']==('CS201' if merged else 'CS101') for e in events))
    def test_plain_image_keeps_general_evidence(self):
        response=self.extract(png(['XY1234 Monday 2033-02-07 09:00-11:00 Lecture Room X1']))
        self.assertEqual(response.status_code,200,response.text[:300]);x=response.json()
        self.assertIsNone(x['layout_profile']);self.assertTrue(x['data']['evidence'])
    def test_mixed_families_stop_before_wrong_sidecar(self):
        c=AutomaticCollector();c.selected='ocr-block-grid-s2b-v1';c.page_count=1
        with self.assertRaisesRegex(ValueError,'AUTO_MIXED_LAYOUTS'):
            c.capture_page('synthetic',Image.new('RGB',(200,200),'white'),2,'synthetic',{}, {}, [], {})
    def test_automatic_mode_cannot_change_baseline_inputs(self):
        for method in ('B1','B2','B2H-v1'):
            self.assertEqual(self.client.post('/api/extract?layout_profile=auto&pipeline='+method,content=b'x').status_code,422)
    def test_corrupt_input_reports_failure(self):
        self.assertEqual(self.extract(b'not a timetable').status_code,422)

    def test_malformed_pdf_and_mime_mismatch_remain_safe_errors(self):
        self.assertEqual(self.extract(b'%PDF-broken','corrupt.pdf').status_code,422)
        response=self.client.post('/api/extract?layout_profile=auto',content=pdf(),headers={'Content-Type':'image/png','X-Filename':'wrong.pdf'})
        self.assertEqual(response.status_code,422)

    def test_raster_pdf_uses_ocr_with_its_actual_grid_coordinates(self):
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.lib.utils import ImageReader
        data=BytesIO();canvas=Canvas(data,pagesize=(540,187.2))
        canvas.drawImage(ImageReader(BytesIO(grid())),0,0,width=540,height=187.2);canvas.save()
        response=self.extract(data.getvalue(),'invented-raster.pdf')
        self.assertEqual(response.status_code,200,response.text[:300]);result=response.json()
        self.assertEqual(result['data']['source']['route'],'ocr')
        self.assertEqual(result['layout_profile'],'ocr-ruled-grid-s2a-v1')
        self.assertTrue(all(s['route']=='ocr' for s in result['data']['evidence']))
        self.assertEqual(result['layout_evidence']['pages'][0]['transform']['dpi'],200)
