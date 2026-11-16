"""Strict loopback browser pairing with invented TEST_ONLY source identity."""
from datetime import timedelta
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.review.auth import TrustedHostAuthority, utc_now
from backend.review.local_host import LocalReviewHost, create_host_app, ORIGIN, HOST, COOKIE
from backend.review.persistence import PersistentReviewStore, ReviewJournal
from backend.review.models import ReviewError
from backend.services.file_extractor import extract_document
from backend.services.extraction_adapter import adapt_extraction
from backend.reconstruction.service import reconstruct
from backend.review.binding import AssessmentInput
from tests.portable_s4b_v1 import review_pdf


@unittest.skipUnless(os.name == 'nt', 'Windows local host and encrypted storage')
class LocalReviewHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='stair-host-synthetic-');self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.source_file=self.path/'invented.pdf';self.source_file.write_bytes(review_pdf())
        packet=adapt_extraction(extract_document('invented.pdf','application/pdf',self.source_file.read_bytes()))
        self.source=packet.source;self.binding=(self.source.source_document_id,self.source.content_sha256)
        self.request=AssessmentInput(source_packet=packet,reconstruction=reconstruct(packet,'constrained'))
        self.authority=TrustedHostAuthority(test_only_source_bindings={self.binding})
        self.store=PersistentReviewStore(self.authority,ReviewJournal(self.path/'private','SYNTHETIC-HOST-ONLY'))
        self.host=LocalReviewHost(self.store,'SYNTHETIC-HOST-ONLY',identity=lambda:'SYNTHETIC-HOST-ONLY',grant_mode='TEST_ONLY')
        self.app=create_host_app(self.host)
        self.client=TestClient(self.app,base_url=ORIGIN,client=('127.0.0.1',43000));self.addCleanup(self.client.close)
        self.headers={'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'}

    def pending(self):
        r=self.client.post('/__local_review__/pending',headers=self.headers,json={'source_document_id':self.binding[0],'content_sha256':self.binding[1]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['authorized']);self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])
        return r.json()

    def connect(self):
        pending=self.pending();self.host.authorize_from_console(pending['nonce'],self.source_file)
        return {**self.headers,'X-STAIR-Review-Binding':pending['binding']}

    def test_no_identity_from_browser_and_no_http_authorization(self):
        self.assertEqual(self.client.get('/api/review/session',headers=self.headers).status_code,403)
        r=self.client.post('/__local_review__/authorize',headers=self.headers,json={'actor':'forged'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(len(self.authority._grants),0)

    def test_pending_does_not_authorize(self):
        p=self.pending();h={**self.headers,'X-STAIR-Review-Binding':p['binding']}
        self.assertEqual(self.client.get('/api/review/session',headers=h).status_code,403)
        self.assertEqual(len(self.authority._grants),0)

    def test_explicit_console_file_consent_gives_exact_scope(self):
        h=self.connect();r=self.client.get('/api/review/session',headers=h)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['actor_reference'],'SYNTHETIC-HOST-ONLY')
        self.assertEqual(r.json()['source_bindings'],[list(self.binding)])
        self.assertEqual(r.json()['mode'],'TEST_ONLY')
        self.assertEqual(r.json()['storage'],'LOCAL_WINDOWS_DPAPI_RESTART_PERSISTENT')
        self.assertNotIn(next(iter(self.host.channels.values())).bearer,r.text)

    def test_wrong_file_and_changed_windows_identity_refused(self):
        p=self.pending();wrong=self.path/'wrong.pdf';wrong.write_bytes(b'%PDF-synthetic wrong')
        with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'],wrong)
        with patch.object(self.host,'identity',return_value='DIFFERENT-SYNTHETIC'):
            with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'],self.source_file)
        self.assertEqual(len(self.authority._grants),0)

    def test_expired_pairing_and_nonce_replay_refused(self):
        p=self.pending()
        with patch.object(self.host,'clock',return_value=self.host.clock()+121):
            with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'],self.source_file)
        self.host.authorize_from_console(p['nonce'],self.source_file)
        with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'],self.source_file)

    def test_cross_origin_and_dns_rebinding_refused(self):
        h=self.connect()
        for extra in ({'Origin':'http://evil.invalid'},{'Host':'evil.invalid:8020'},
                      {'Sec-Fetch-Site':'cross-site'},{'X-Forwarded-For':'127.0.0.1'},
                      {'Sec-Fetch-Dest':'document'}):
            self.assertEqual(self.client.get('/api/review/session',headers={**h,**extra}).status_code,403)

    def test_cookie_without_binding_and_binding_without_cookie_refused(self):
        h=self.connect();self.assertEqual(self.client.get('/api/review/session',headers=self.headers).status_code,403)
        self.client.cookies.clear();self.assertEqual(self.client.get('/api/review/session',headers=h).status_code,403)

    def test_non_loopback_peer_refused(self):
        with TestClient(self.app,base_url=ORIGIN,client=('192.0.2.1',40000)) as remote:
            self.assertEqual(remote.get('/').status_code,403)

    def test_no_bearer_substitution_or_duplicate_header(self):
        h=self.connect()
        self.assertEqual(self.client.get('/api/review/session',headers={**h,'Authorization':'Bearer forged'}).status_code,403)
        self.assertEqual(self.client.get('/api/review/session',headers=list(h.items())+[('Origin',ORIGIN)]).status_code,403)

    def test_queue_scope_and_claim_are_enforced(self):
        h=self.connect();r=self.client.post('/api/review/ingest',headers=h,json=self.request.model_dump(mode='json'))
        self.assertEqual(r.status_code,200,r.text)
        ident=r.json()['task_ids'][0]
        row=self.client.get('/api/review/queue',headers=h).json()['tasks'][0]
        r=self.client.post(f'/api/review/tasks/{ident}/transition',headers=h,json={'action':'claim','expected_etag':row['etag'],'reason':'Invented explicit source owner claim'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['assignee'],'SYNTHETIC-HOST-ONLY')
        body=self.request.model_dump(mode='json');body['source_packet']['source']['content_sha256']='a'*64
        r=self.client.post('/api/review/ingest',headers=h,json=body)
        self.assertEqual(r.status_code,403)

    def test_ending_session_revokes_channel(self):
        h=self.connect();r=self.client.post('/api/review/session/end',headers=h,json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/review/session',headers=h).status_code,403)

    def test_pairing_does_not_survive_restart(self):
        h=self.connect()
        restarted_authority=TrustedHostAuthority(test_only_source_bindings={self.binding})
        store=PersistentReviewStore(restarted_authority,ReviewJournal(self.path/'private','SYNTHETIC-HOST-ONLY'))
        restarted=LocalReviewHost(store,'SYNTHETIC-HOST-ONLY',identity=lambda:'SYNTHETIC-HOST-ONLY',grant_mode='TEST_ONLY')
        with TestClient(create_host_app(restarted),base_url=ORIGIN,client=('127.0.0.1',43000)) as client:
            client.cookies.update(self.client.cookies)
            self.assertEqual(client.get('/api/review/session',headers=h).status_code,403)

    def test_host_only_assets_and_api_base(self):
        index=self.client.get('/');self.assertIn('/js/local-review-host.js',index.text)
        js=self.client.get('/js/app.js');self.assertIn('const API_BASE = "'+ORIGIN+'";',js.text)
        self.assertEqual(self.client.get('/api/intranet/connect',headers=self.headers).status_code,403)
        from backend.main import app
        with TestClient(app) as normal:
            self.assertEqual(normal.get('/api/review/session').status_code,403)

    def test_pending_size_and_unsupported_routes_bounded(self):
        r=self.client.post('/__local_review__/pending',headers={**self.headers,'Content-Type':'application/json'},content=' '*1025)
        self.assertEqual(r.status_code,403)
        h=self.connect()
        for route in ('/api/review/grants','/api/review/tasks/%2e%2e/history','/api/review/queue?offset=0&bad=1'):
            self.assertEqual(self.client.get(route,headers=h).status_code,403)

    def test_http_correction_history_and_preview_after_restart(self):
        h=self.connect()
        r=self.client.post('/api/review/ingest',headers=h,json=self.request.model_dump(mode='json'))
        ident=r.json()['task_ids'][0]
        original=self.store.original_reconstruction(ident,next(iter(self.host.channels.values())).principal)
        task=self.client.get(f'/api/review/tasks/{ident}',headers=h).json()['task']
        for action in ('claim','start'):
            r=self.client.post(f'/api/review/tasks/{ident}/transition',headers=h,json={'action':action,'expected_etag':task['etag'],'reason':'Invented TEST_ONLY workflow'})
            self.assertEqual(r.status_code,200,r.text);task=r.json()
        parent=self.client.get(f'/api/review/tasks/{ident}',headers=h).json()['evidence'][0]
        body={'request_id':str(uuid4()),'expected_revision_id':task['current_event']['revision']['revision_id'],'expected_etag':task['etag'],
              'reason':'Invented TEST_ONLY correction; not a real student submission',
              'human_evidence':[{'evidence_id':'human:synthetic-http','raw_text':'Z082','source_evidence_ids':[parent['evidence_id']],
                                'page':parent['page'],'note':'Synthetic regression assertion'}],
              'fields':[{'field_name':'location','state':'PRESENT','value':'Z082','raw_value':'Z082','evidence_ids':['human:synthetic-http']}]}
        r=self.client.post(f'/api/review/tasks/{ident}/corrections',headers=h,json=body)
        self.assertEqual(r.status_code,200,r.text);task=r.json()
        r=self.client.post(f'/api/review/tasks/{ident}/revalidate',headers=h,json={'expected_etag':task['etag']})
        self.assertEqual(r.status_code,200,r.text);task=r.json()
        history=self.client.get(f'/api/review/tasks/{ident}/history',headers=h).json()
        self.authority=TrustedHostAuthority(test_only_source_bindings={self.binding})
        self.store=PersistentReviewStore(self.authority,ReviewJournal(self.path/'private','SYNTHETIC-HOST-ONLY'))
        self.host=LocalReviewHost(self.store,'SYNTHETIC-HOST-ONLY',identity=lambda:'SYNTHETIC-HOST-ONLY',grant_mode='TEST_ONLY')
        self.client.close();self.client=TestClient(create_host_app(self.host),base_url=ORIGIN,client=('127.0.0.1',43000));self.addCleanup(self.client.close)
        self.assertEqual(self.client.get('/api/review/session',headers=h).status_code,403)
        h=self.connect()
        restored=self.client.get(f'/api/review/tasks/{ident}',headers=h).json()['task']
        self.assertEqual(restored,task)
        self.assertEqual(self.client.get(f'/api/review/tasks/{ident}/history',headers=h).json(),history)
        principal=next(iter(self.host.channels.values())).principal
        self.assertEqual(self.store.original_reconstruction(ident,principal),original)
        snap=self.client.get(f'/api/review/tasks/{ident}/snapshot',headers=h).json()
        current=next(e for e in snap['packet']['events'] if e['candidate_id']==task['current_event']['candidate_id'])
        self.assertEqual(current['revision']['content']['location'],'Z082')
        self.assertIsNone(current['revision']['content']['recurrence'])
        self.assertEqual(current['human_verification']['status'],'NOT_VERIFIED')
        stale=self.client.post(f'/api/review/tasks/{ident}/corrections',headers=h,json=body)
        self.assertEqual(stale.status_code,409)
        preview=self.client.post('/api/calendar/v1/preview',headers=self.headers,json={'bundle':{'packet':snap['packet']}})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertFalse(preview.json()['write_authorized'])
        self.assertTrue(all(o['action']=='BLOCKED' for o in preview.json()['preview']['operations']))
