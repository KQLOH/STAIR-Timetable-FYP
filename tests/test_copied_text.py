"""FR1 copied-text contracts using invented timetables, never human references."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.file_extractor import MAX_TEXT_BYTES, MAX_TEXT_LINES
from backend.services.extraction_adapter import adapt_extraction
from backend.services.file_extractor import extract_document

ROOT = Path(__file__).resolve().parents[1]
TEXT = "CS101 Monday 2033-02-07 09:00-10:00 Lecture Room: A101\r\n\r\nMA202 Tuesday 2033-02-08 11:00-12:00 Tutorial Room: B202"

class CopiedTextTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
    def extract(self, raw, query=''):
        return self.client.post('/api/extract'+query, content=raw, headers={'Content-Type':'text/plain', 'X-Filename':'copied-timetable.txt'})
    def test_exact_original_bytes_and_nonspatial_provenance(self):
        raw = TEXT.encode('utf-8')
        response = self.extract(raw)
        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()['data']
        self.assertEqual(packet['source']['content_sha256'], sha256(raw).hexdigest())
        self.assertEqual(packet['source']['content_hash_basis'], 'original_bytes')
        self.assertEqual(packet['source']['route'], 'text')
        self.assertFalse(packet['events'])
        self.assertTrue(all(s['route']=='text' and s['bbox'] is None and s['confidence'] is None for s in packet['evidence']))
        self.assertEqual([s['raw_text'] for s in packet['evidence']], TEXT.split('\r\n\r\n'))
    def test_complete_events_reconstruct_and_repeat_without_new_identity(self):
        outputs=[]
        for _ in range(2):
            packet=self.extract(TEXT.encode()).json()['data']
            response=self.client.post('/api/reconstruct',json={'packet':packet,'profile':'m4-development-v1','decoder_mode':'constrained','context':{}})
            self.assertEqual(response.status_code,200,response.text)
            out=response.json()['canonical_output'];events=out['packet']['events']
            self.assertEqual(len(events),2)
            content={e['revision']['content']['course_code']:e['revision']['content'] for e in events}
            self.assertEqual((content['CS101']['date'],content['CS101']['start_time'],content['CS101']['end_time'],content['CS101']['location']),('2033-02-07','09:00','10:00','A101'))
            self.assertEqual((content['MA202']['day_of_week'],content['MA202']['session_type'],content['MA202']['location']),(2,'Tutorial','B202'))
            self.assertTrue(all(e['human_verification']['status']=='NOT_VERIFIED' and e['calendar']['approval_status']=='NOT_APPROVED' and e['revision']['content']['recurrence'] is None for e in events))
            outputs.append({(e['candidate_id'],e['identity']['event_id']) for e in events})
        self.assertEqual(outputs[0],outputs[1])
    def test_empty_binary_and_late_nul_fail_safely(self):
        for raw in (b'',b'   ',b'a'*5000+b'\0',b'CS101\x01Monday'):
            with self.subTest(raw_length=len(raw)):
                self.assertEqual(self.extract(raw).status_code,422)
    def test_large_text_and_excessive_lines_fail_before_reconstruction(self):
        for raw in (b'a'*(MAX_TEXT_BYTES+1),b'x\n'*(MAX_TEXT_LINES+1)):
            self.assertEqual(self.extract(raw).status_code,413)
    def test_text_cannot_be_silently_substituted_for_baseline_ocr(self):
        for pipeline in ('B1','B2','B2H-v1'):
            self.assertEqual(self.extract(TEXT.encode(),'?pipeline='+pipeline).status_code,422)
    def test_text_cannot_claim_grid_geometry(self):
        for profile in ('ocr-block-grid-s2b-v1','ocr-ruled-grid-s2a-v1'):
            self.assertEqual(self.extract(TEXT.encode(),'?layout_profile='+profile).status_code,422)
    def test_unicode_text_is_preserved(self):
        raw='CS101 Monday 09:00-10:00 Lecturer: Renée'.encode()
        self.assertEqual(self.extract(raw).json()['data']['evidence'][0]['raw_text'],raw.decode())
    def test_student_entry_and_original_text_reselection_are_wired(self):
        from bs4 import BeautifulSoup
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        self.assertIsNotNone(page.find(id='copiedTimetableText'))
        self.assertEqual(page.find(id='extractCopiedText')['type'],'button')
        self.assertIn('.txt',page.find(id='fileInput')['accept'])
        self.assertIn('setupCopiedText();',(ROOT/'frontend/js/app.js').read_text(encoding='utf-8'))
    def test_actual_frontend_text_validation_and_original_rendering(self):
        result=subprocess.run(['node',str(ROOT/'tests/copied_text_checks.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

@unittest.skipUnless(os.name=='nt','Windows DPAPI source-scoped consent')
class CopiedTextConsentTests(unittest.TestCase):
    def test_exact_saved_text_requires_console_consent_and_remains_test_only(self):
        from backend.review.local_host import LocalReviewHost
        from backend.review.auth import TrustedHostAuthority
        from backend.review.persistence import PersistentReviewStore, ReviewJournal
        with TemporaryDirectory(prefix='stair-copied-SYNTHETIC-') as directory:
            root=Path(directory);file=root/'copied-timetable.txt';file.write_bytes(TEXT.encode())
            source=adapt_extraction(extract_document(file.name,'text/plain',file.read_bytes())).source
            authority=TrustedHostAuthority(test_only_source_bindings={(source.source_document_id,source.content_sha256)})
            actor='SYNTHETIC-COPIED-TEXT-TEST'
            store=PersistentReviewStore(authority,ReviewJournal(root/'private',actor))
            host=LocalReviewHost(store,actor,identity=lambda:actor,grant_mode='TEST_ONLY')
            _,pending=host.request_access({'source_document_id':source.source_document_id,'content_sha256':source.content_sha256})
            self.assertFalse(pending['authorized']);self.assertFalse(authority._grants)
            file.write_bytes(TEXT.encode()+b' ')
            with self.assertRaises(ValueError):host.authorize_from_console(pending['nonce'],file)
            self.assertFalse(authority._grants)
            file.write_bytes(TEXT.encode());host.authorize_from_console(pending['nonce'],file)
            self.assertEqual(len(authority._grants),1)
            self.assertEqual(next(iter(host.channels.values())).principal.mode,'TEST_ONLY')
            self.assertFalse(tuple(store.journal.records.iterdir()))
