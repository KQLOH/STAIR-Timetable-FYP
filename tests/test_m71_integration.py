"""M7.1 synthetic integration and regression checks. No real credentials/data."""
from contextlib import redirect_stdout
from io import StringIO, BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from PIL import Image

from backend import main
from backend.models.event import CanonicalTimetable
from tests.fixtures import timetable_html
from tests.test_m51_integration import ROOT, NODE, build_fixtures, synthetic_png
from tests.test_m7_analysis import make, attest, value
from backend.analysis.service import analyze


class FrontendRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='m71-test-',dir=ROOT/'data/temp')
        cls.fixture=Path(cls.temp.name)/'synthetic.json'
        with TestClient(main.app) as client:fixtures=build_fixtures(client)
        cls.fixture.write_text(json.dumps(fixtures),encoding='utf-8')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()


def attach(case):
    def test(self):
        r=subprocess.run([NODE,str(ROOT/'tests/m71_frontend_checks.js'),str(self.fixture),case],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr[-1800:])
    setattr(FrontendRegressionTests,'test_'+case,test)


for case in ('pending_upload_cannot_reuse_tar','upload_clears_structured_packet','source_switch_silent_reset',
             'late_refresh_cannot_replace_sample','late_refresh_cannot_replace_upload','connect_while_uploading_keeps_structured_label',
             'late_connect_cannot_replace_sample','sample_dates_match_weekdays','sample_preview_replaces_old_source',
             'forced_analysis_invalidation','login_failure_clears_password','sample_completeness_is_field_presence',
             'pagehide_invalidates_analysis'):attach(case)

for case in ('expired_refresh_clears_controls_preserves_events','expired_disconnect_clears_controls',
             'late_expired_refresh_preserves_new_session','late_expired_disconnect_preserves_new_session',
             'refresh_server_error_retains_unconfirmed_connection','session_error_redacts_payload_preserves_http_status'):attach(case)


class PrivacyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
        self.clients=patch.dict(main.CONNECTED_CLIENTS,{},clear=True);self.clients.start();self.addCleanup(self.clients.stop)
        self.portal=MagicMock();self.portal.student_name='SYNTHETIC';self.portal.last_timetable_url='https://synthetic.invalid/?private=SYNTHETIC_SECRET'
        self.portal.fetch_latest_timetable_html.return_value=timetable_html()
    def test_connect_does_not_persist_or_log_private_html(self):
        project=MagicMock();output=StringIO()
        with patch.object(main,'PROJECT_ROOT',project),patch.object(main,'TARUMTClient',return_value=self.portal),redirect_stdout(output):
            response=self.client.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC'})
        self.assertEqual(response.status_code,200);self.assertEqual(project.mock_calls,[])
        self.assertEqual(output.getvalue(),'')
    def test_connect_error_does_not_echo_sensitive_exception(self):
        self.portal.login.side_effect=RuntimeError('SYNTHETIC_SECRET cookie=password')
        with patch.object(main,'TARUMTClient',return_value=self.portal):r=self.client.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC'})
        self.assertEqual(r.status_code,500);self.assertNotIn('SYNTHETIC_SECRET',r.text)
    def test_portal_client_does_not_log_private_urls(self):
        from backend.services.tarumt_client import TARUMTClient
        portal=TARUMTClient();self.addCleanup(portal.session.close)
        response=MagicMock(text='<a href="logout.jsp">SYNTHETIC logout</a><h1>Semester Timetable by Week</h1>'+timetable_html(),status_code=200,url='https://synthetic.invalid/?private=SYNTHETIC_SECRET')
        output=StringIO()
        with patch.object(portal,'discover_timetable_url',return_value=response.url),patch.object(portal,'_get',return_value=response),redirect_stdout(output):
            self.assertIn('TEST1001',portal.fetch_latest_timetable_html())
        self.assertEqual(output.getvalue(),'')
    def test_extraction_contract_exception_redacted(self):
        with patch.object(main,'extract_document',side_effect=ValueError('SYNTHETIC_SECRET')):
            r=self.client.post('/api/extract',content=b'fake',headers={'content-type':'image/png','x-filename':'synthetic.png'})
        self.assertEqual(r.status_code,422);self.assertNotIn('SYNTHETIC_SECRET',r.text)
    def test_refresh_error_does_not_echo_sensitive_exception(self):
        main.CONNECTED_CLIENTS['synthetic-token']=self.portal;self.portal.fetch_latest_timetable_html.side_effect=RuntimeError('SYNTHETIC_SECRET')
        r=self.client.post('/api/intranet/timetable',json={'token':'synthetic-token'})
        self.assertEqual(r.status_code,500);self.assertNotIn('SYNTHETIC_SECRET',r.text)
    def test_synthetic_completeness_cannot_be_relabelled_real(self):
        q,a,_=attest(make());changed=q.model_copy(update={'declared_kind':'UNVERIFIED_CANDIDATES'})
        self.assertFalse(analyze(changed,a)['completeness']['trusted_complete'])
    def test_unrepresentable_scope_returns_redacted_422(self):
        body=make().model_dump(mode='json');body['scope'].update(start='9999-12-31',end='9999-12-31')
        r=self.client.post('/api/analysis/v1/analyze',json=body);self.assertEqual(r.status_code,422)


class PipelineQATests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
    def test_png_and_jpeg_through_evidence_reconstruction_policy_calendar_analysis(self):
        from tests.test_m52_layout import synthetic_png as layout_png
        content=layout_png();jpg=BytesIO();Image.open(BytesIO(content)).convert('RGB').save(jpg,format='JPEG',quality=95)
        for name,mime,raw in [('synthetic.png','image/png',content),('synthetic.jpg','image/jpeg',jpg.getvalue())]:
            with self.subTest(name=name):
                response=self.client.post('/api/extract?pipeline=evidence',content=raw,headers={'X-Filename':name,'Content-Type':mime})
                self.assertEqual(response.status_code,200,response.text);packet=response.json()['data'];CanonicalTimetable.model_validate(packet)
                self.assertEqual(packet['source']['route'],'ocr');self.assertTrue(packet['evidence'])
                self.assertTrue(all(s['page']==1 and s['bbox']['coordinate_space']=='PIXELS' for s in packet['evidence']))
                reconstructed=self.client.post('/api/reconstruct',json={'packet':packet,'decoder_mode':'constrained','profile':'ocr-layout-m52-v1','context':{}})
                self.assertEqual(reconstructed.status_code,200,reconstructed.text);envelope=reconstructed.json();output=envelope['canonical_output']
                events=output['packet']['events']+output['unresolved_candidates'];self.assertTrue(events)
                self.assertIn('BACS3413',[e['revision']['content']['course_code'] for e in events])
                policy=self.client.post('/api/layout/assess',json={'source_packet':packet,'reconstruction':envelope})
                self.assertEqual(policy.status_code,200,policy.text);self.assertTrue(policy.json()['decisions'])
                self.assertTrue(all(d['decision']=='ABSTAIN' and d['calibrated_probability'] is None for d in policy.json()['decisions']))
                bundle={'packet':output['packet'],'context':{},'assessments':[],'reconstruction_status':output['status']}
                eligible=self.client.post('/api/calendar/v1/eligibility',json=bundle);preview=self.client.post('/api/calendar/v1/preview',json={'bundle':bundle})
                self.assertEqual(eligible.status_code,200);self.assertEqual(eligible.json()['policy_status'],'BLOCKED')
                self.assertEqual(preview.status_code,200);self.assertFalse(preview.json()['write_authorized'])
                q=make().model_dump(mode='json');q.update(packet={**output['packet'],'events':events},context={},declared_kind='UNVERIFIED_CANDIDATES')
                r=self.client.post('/api/analysis/v1/analyze',json=q);self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()['completeness']['label'],'OBSERVED EVENTS ONLY');self.assertEqual(r.json()['recommendations'],[])
                self.assertEqual(value(r.json(),'observed_candidate_count'),len(events))
    def test_pdf_native_and_registered_ocr_routes_are_distinct(self):
        from tests.test_m31_ocr_and_b2 import synthetic_pdf
        raw=synthetic_pdf()
        for pipeline in ('evidence','B1','B2','B2H-v1'):
            r=self.client.post('/api/extract?pipeline='+pipeline,content=raw,headers={'X-Filename':'synthetic.pdf','Content-Type':'application/pdf'})
            self.assertEqual(r.status_code,200,r.text);p=r.json()['data'];self.assertTrue(p['evidence'])
            expected='native_pdf' if pipeline=='evidence' else 'ocr'
            self.assertEqual(p['source']['route'],expected);self.assertTrue(all(s['route']==expected for s in p['evidence']))
    def test_invalid_upload_matrix(self):
        rows=[('empty.png','image/png',b''),('bad.png','image/png',b'\x89PNG\r\n\x1a\ncorrupt'),
              ('bad.exe','application/octet-stream',b'MZ'),('mismatch.png','application/pdf',synthetic_png())]
        for name,mime,raw in rows:
            r=self.client.post('/api/extract',content=raw,headers={'X-Filename':name,'Content-Type':mime})
            self.assertGreaterEqual(r.status_code,400)
        self.assertEqual(self.client.post('/api/extract?pipeline=b1',content=b'x').status_code,422)
        self.assertEqual(self.client.post('/api/extract',content=b'x',headers={'Content-Length':str(20*1024*1024+1)}).status_code,413)
    def test_ocr_missing_and_timeout_are_transparent(self):
        from backend.services.ocr_runtime import OCRRuntimeError
        for code in ('OCR_EXECUTABLE_MISSING','OCR_INVOCATION_FAILED'):
            with patch('backend.services.file_extractor.recognize',side_effect=OCRRuntimeError(code,'Synthetic unavailable or timeout')):
                r=self.client.post('/api/extract',content=synthetic_png(),headers={'X-Filename':'synthetic.png','Content-Type':'image/png'})
            self.assertEqual(r.status_code,503,r.text);self.assertEqual(r.json()['detail']['diagnostic_code'],code)
    def test_protected_mutations_and_forged_actor_strings_stay_blocked(self):
        self.assertEqual(self.client.post('/api/review/ingest',json={'actor_id':'forged'}).status_code,403)
        for route in ('sandbox','history','reconciliation-diagnostics'):
            self.assertEqual(self.client.get('/api/calendar/v1/'+route,headers={'authorization':'Bearer forged'}).status_code,403)
        for action in ('execute','approve','sync'):
            self.assertEqual(self.client.post('/api/calendar/v1/'+action,json={'actor_id':'forged'}).status_code,404)
    def test_history_placeholder_does_not_claim_storage(self):
        # STATIC INSPECTION: actual History browser behavior is recorded separately.
        html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
        self.assertIn('does not persist extraction history',html)
        self.assertNotIn('Recent local sessions are stored',html)
