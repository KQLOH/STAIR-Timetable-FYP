"""M5.1 synthetic browser-client and real local API tests; no private data."""
import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from backend import main
from backend.models.extraction import DocumentEvidence, EvidencePage, EvidenceRegion, BoundingBox
from backend.services.extraction_adapter import adapt_extraction, adapt_baseline
from backend.services.event_adapter import canonical_api_data
from backend.services.timetable_parser import parse_intranet_response
from backend.services import ocr_runtime
from tests.fixtures import timetable_html

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')


def synthetic_png():
    image=Image.new('RGB',(1100,160),'white')
    ImageDraw.Draw(image).text((20,30),'CS101 Monday 09:00-11:00 Lecture',fill='black',font=ImageFont.load_default(size=32))
    output=BytesIO();image.save(output,format='PNG');return output.getvalue()


def fixture_document(text='CS101 Monday 09:00-11:00 Lecture'):
    return DocumentEvidence(source_id='m51-synthetic',route_type='upload_ocr',original_label='synthetic.png',
        media_type='image/png',checksum=sha256(text.encode()).hexdigest(),parser_version='m51-synthetic-fixture',
        pages=[EvidencePage(page=1,width=1100,height=160,text=text,regions=[EvidenceRegion(evidence_id='m51-span-1',
            page=1,line=1,text=text,bbox=BoundingBox(x0=20,y0=30,x1=600,y1=70),confidence=None,adapter='synthetic-ocr')])])


def build_fixtures(client):
    source=fixture_document()
    extractions={p:{'success':True,'pipeline':p,'data':(adapt_extraction(source) if p=='evidence' else adapt_baseline(source,p)).model_dump(mode='json')}
        for p in ('evidence','B1','B2','B2H-v1')}
    packet=extractions['B2H-v1']['data']
    r=client.post('/api/reconstruct',json={'packet':packet,'decoder_mode':'constrained','profile':'m4-development-v1','context':{}})
    assert r.status_code==200,r.text
    reconstruction=r.json()
    p=client.post('/api/policy/assess',json={'source_packet':packet,'reconstruction':reconstruction})
    assert p.status_code==200,p.text
    bundle={'packet':reconstruction['canonical_output']['packet'],'reconstruction_status':reconstruction['canonical_output']['status'],'context':{},'assessments':[]}
    e=client.post('/api/calendar/v1/eligibility',json=bundle);v=client.post('/api/calendar/v1/preview',json={'bundle':bundle})
    assert e.status_code==v.status_code==200
    return {'png':base64.b64encode(synthetic_png()).decode(),'extractions':extractions,'reconstruction':reconstruction,'reconstruction_json':json.dumps(reconstruction),
        'policy':p.json(),'eligibility':e.json(),'preview':v.json(),'structured':canonical_api_data(parse_intranet_response(timetable_html()))}


def node_check(filename,case,base=None):
    command=[NODE,str(ROOT/'tests/m51_frontend_checks.js'),str(filename),case]
    if base:command.append(base)
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=120)
    if result.returncode:raise AssertionError(result.stderr[-3000:] or result.stdout[-3000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


class M51BrowserClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(NODE).is_file():raise unittest.SkipTest('Node unavailable')
        cls.temp=TemporaryDirectory(prefix='m51-test-',dir=ROOT/'data/temp')
        cls.filename=Path(cls.temp.name)/'synthetic-fixtures.json'
        with TestClient(main.app) as client:fixtures=build_fixtures(client)
        cls.filename.write_text(json.dumps(fixtures),encoding='utf-8')

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_javascript_syntax(self):
        for name in ('app.js','upload.js','intranet.js'):
            r=subprocess.run([NODE,'--check',str(ROOT/'frontend/js'/name)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)


def attach_client_test(case):
    def test(self):self.assertEqual(node_check(self.filename,case)['status'],'passed')
    setattr(M51BrowserClientTests,'test_'+case,test)


for case in ('raw_upload','pipelines','file_validation','extraction_failure','bad_response','no_candidates','partial_and_alternatives',
    'reconstruction_failure','incomplete_search','policy_unavailable','calendar_safety','cancellation','object_urls','xss_and_preview',
    'provenance_validation','structured_compatibility','app_labels','exact_envelope','file_input','late_response','timeout'):
    attach_client_test(case)


class M51APITests(unittest.TestCase):
    def setUp(self):self.client=TestClient(main.app);self.addCleanup(self.client.close)

    def test_raw_body_and_exact_pipeline_contract(self):
        for pipeline in ('evidence','B1','B2','B2H-v1'):
            with patch.object(main,'extract_document',return_value=fixture_document()) as extractor:
                response=self.client.post('/api/extract?pipeline='+pipeline,content=synthetic_png(),headers={'Content-Type':'image/png','X-Filename':'synthetic.png'})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['pipeline'],pipeline)
                args=extractor.call_args
                self.assertEqual(args.args[0:2],('synthetic.png','image/png'))
                self.assertTrue(args.args[2].startswith(b'\x89PNG'))
                self.assertEqual(args.kwargs['force_ocr'],pipeline!='evidence')

    def test_cors_preflight_filename_header(self):
        r=self.client.options('/api/extract?pipeline=B1',headers={'Origin':'http://127.0.0.1:5500',
            'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type,x-filename'})
        self.assertEqual(r.status_code,200);self.assertEqual(r.headers['access-control-allow-origin'],'http://127.0.0.1:5500')
        denied=self.client.options('/api/extract',headers={'Origin':'https://untrusted.invalid','Access-Control-Request-Method':'POST'})
        self.assertEqual(denied.status_code,400)

    def test_invalid_and_no_multipart_contract(self):
        for name,mime,content in [('a.png','image/png',b'not-image'),('a.pdf','application/pdf',b'bad'),('a.exe','application/octet-stream',b'MZ')]:
            r=self.client.post('/api/extract',content=content,headers={'X-Filename':name,'Content-Type':mime});self.assertGreaterEqual(r.status_code,400)
        r=self.client.post('/api/extract?pipeline=b2',content=synthetic_png(),headers={'X-Filename':'a.png','Content-Type':'image/png'})
        self.assertEqual(r.status_code,422)
        r=self.client.post('/api/extract',files={'file':('a.png',synthetic_png(),'image/png')},headers={'X-Filename':'a.png'})
        self.assertGreaterEqual(r.status_code,400)

    def test_review_and_calendar_mutations_unavailable(self):
        self.assertEqual(self.client.post('/api/review/ingest',json={}).status_code,403)
        for suffix in ('approve','execute','sync','delete'):
            self.assertEqual(self.client.post('/api/calendar/v1/'+suffix,json={}).status_code,404)

    def test_intranet_mocked_session_preserves_canonical(self):
        mock=MagicMock();mock.fetch_latest_timetable_html.return_value=timetable_html();mock.student_name='SYNTHETIC TEST NAME'
        with patch.dict(main.CONNECTED_CLIENTS,{'synthetic-m51-token':mock},clear=True):
            r=self.client.post('/api/intranet/timetable',json={'token':'synthetic-m51-token'})
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()['data']['events'][0]['subject_code'],'TEST1001')
            self.assertEqual(r.json()['data']['canonical']['source']['route'],'tarumt_html');mock.login.assert_not_called()
            d=self.client.post('/api/intranet/disconnect',json={'token':'synthetic-m51-token'})
            self.assertTrue(d.json()['success']);mock.close.assert_called_once()

    def test_openapi_and_frontend_controls(self):
        schema=main.app.openapi();self.assertIn('/api/policy/assess',schema['paths'])
        self.assertEqual(schema['paths']['/api/extract']['post']['parameters'][0]['name'],'pipeline')
        html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
        self.assertIn('max 20 MiB',html);self.assertIn('value="B2H-v1"',html)
        self.assertIn('id="syncButton" disabled',html)
        self.assertIn('id="uploadProgress" aria-label="Processing upload (indeterminate)" hidden',html)
        self.assertIn('id="thresholdRange" type="range" min="40" max="95" value="70" disabled',html)
        css=(ROOT/'frontend/styles.css').read_text(encoding='utf-8')
        self.assertIn('.event-card .event-main:only-child { grid-column: 1 / -1; }',css)
        js=(ROOT/'frontend/js/upload.js').read_text(encoding='utf-8')
        self.assertNotIn('localStorage',js);self.assertNotIn('sessionStorage',js);self.assertNotIn('innerHTML',js)


def verify_live_client():
    """Real browser client logic (Node fetch) -> local FastAPI -> installed Tesseract."""
    import uvicorn
    ocr_runtime.detect_runtime()
    with TemporaryDirectory(prefix='m51-test-live-',dir=ROOT/'data/temp') as temp:
        filename=Path(temp)/'synthetic.json';filename.write_text(json.dumps({'png':base64.b64encode(synthetic_png()).decode()}),encoding='utf-8')
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64)
        port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            deadline=time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
            if not server.started:raise AssertionError('Synthetic local API server did not start')
            return node_check(filename,'live',f'http://127.0.0.1:{port}')['detail']
        finally:
            server.should_exit=True;thread.join(timeout=10);sock.close()
            if thread.is_alive():raise AssertionError('Synthetic local API server did not stop')


class M51LiveOCRTests(unittest.TestCase):
    def test_real_synthetic_client_ocr_to_policy_and_preview(self):
        try:ocr_runtime.detect_runtime()
        except ocr_runtime.OCRRuntimeError as error:self.skipTest('Live OCR unavailable: '+error.code)
        result=verify_live_client()
        self.assertEqual(result['external_calendar'],False);self.assertEqual(result['calendar_status'],'BLOCKED_READ_ONLY')
        self.assertIn('/api/extract',result['calls']);self.assertIn('/api/policy/assess',result['calls'])


if __name__=='__main__':unittest.main()
