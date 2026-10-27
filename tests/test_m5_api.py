"""Synthetic HTTP tests; review bridge is explicitly installed only within tests."""
import json
from uuid import uuid4
import unittest
from fastapi.testclient import TestClient

from backend.main import app
from backend.review.api import ReviewBridge
from backend.review.auth import TrustedHostAuthority
from backend.review.store import ReviewStore
from test_m5_policy import fixture


class APIReviewTests(unittest.TestCase):
    def setUp(self):
        self.prior_bridge=getattr(app.state,'m5_review_bridge',None)
        app.state.m5_review_bridge=None
        self.client=TestClient(app,client=('127.0.0.1',40000))
        self.request,_=fixture()

    def tearDown(self): app.state.m5_review_bridge=self.prior_bridge

    def assess(self,**changes):
        body=self.request.model_dump(mode='json'); body.update(changes)
        return self.client.post('/api/policy/assess',json=body)

    def enable_synthetic_bridge(self):
        self.auth=TrustedHostAuthority()
        self.admin,self.token=self.auth.issue('synthetic-local-admin',{'read','queue','assign','correct','revalidate','resolve','oversee'})
        self.store=ReviewStore(self.auth)
        app.state.m5_review_bridge=ReviewBridge(self.store)
        self.headers={'Authorization':'Bearer '+self.token}
        self.id=self.store.ingest(self.request,self.admin)[0]
        v=self.store.get(self.id,self.admin)
        v=self.store.assign(self.id,self.admin.actor_id,v.etag,self.admin)
        self.view=self.store.start(self.id,v.etag,self.admin)

    def test_stateless_policy_endpoint(self):
        r=self.assess()
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['decisions'][0]['decision'],'ABSTAIN')
        self.assertEqual(r.json()['provenance_trust'],'CALLER_SUPPLIED_INTEGRITY_CHECKED')
        self.assertFalse(r.json()['registered_routing_ready'])

    def test_default_policy_does_not_create_queue(self):
        self.assertEqual(self.assess().status_code,200)
        self.assertIsNone(app.state.m5_review_bridge)

    def test_public_result_redacts_source_content(self):
        r=self.assess()
        self.assertNotIn('CS101',r.text)
        self.assertNotIn('raw_text',r.text)
        self.assertNotIn('capture_aliases',r.text)
        self.assertNotIn('synthetic-host',r.text)
        self.assertNotIn('C:\\',r.text)

    def test_malformed_request_redacted(self):
        r=self.client.post('/api/policy/assess',content=b'{secret-private',headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,422)
        self.assertNotIn('secret-private',r.text)

    def test_request_limit(self):
        r=self.client.post('/api/policy/assess',content=b' '*2097153,headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)

    def test_media_type(self): self.assertEqual(self.client.post('/api/policy/assess',content=b'x').status_code,415)

    def test_registered_profile_409(self): self.assertEqual(self.assess(policy_profile='registered').status_code,409)

    def test_invalid_mode_422(self): self.assertEqual(self.assess(decoder_mode='LayoutLMv3').status_code,422)

    def test_all_review_routes_default_deny(self):
        ident=str(uuid4())
        for method,path in [('GET','/api/review/queue'),('GET',f'/api/review/tasks/{ident}'),
                            ('GET',f'/api/review/tasks/{ident}/history'),('POST','/api/review/ingest'),
                            ('POST',f'/api/review/tasks/{ident}/transition'),('POST',f'/api/review/tasks/{ident}/corrections'),
                            ('POST',f'/api/review/tasks/{ident}/revalidate')]:
            with self.subTest(path=path): self.assertEqual(self.client.request(method,path).status_code,403)

    def test_tarumt_token_is_not_review_authorization(self):
        self.enable_synthetic_bridge()
        r=self.client.get('/api/review/queue',headers={'Authorization':'Bearer synthetic-university-token'})
        self.assertEqual(r.status_code,403)

    def test_nonlocal_peer_denied_even_with_token(self):
        self.enable_synthetic_bridge()
        client=TestClient(app,client=('203.0.113.10',40000))
        self.assertEqual(client.get('/api/review/queue',headers=self.headers).status_code,403)

    def test_forwarded_localhost_not_trusted(self):
        self.enable_synthetic_bridge()
        client=TestClient(app,client=('203.0.113.10',40000))
        self.assertEqual(client.get('/api/review/queue',headers={**self.headers,'X-Forwarded-For':'127.0.0.1'}).status_code,403)

    def test_authorized_private_task_and_history(self):
        self.enable_synthetic_bridge()
        r=self.client.get(f'/api/review/tasks/{self.id}',headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.json()['evidence'][0]['bbox'])
        self.assertEqual(r.json()['evidence'][0]['kind'],'SOURCE_EXTRACTION')
        self.assertNotIn('raw_html',r.text)
        self.assertEqual(self.client.get(f'/api/review/tasks/{self.id}/history',headers=self.headers).status_code,200)

    def test_actor_id_injection_rejected(self):
        self.enable_synthetic_bridge()
        r=self.client.post(f'/api/review/tasks/{self.id}/corrections',headers=self.headers,json={
            'actor_id':'forged-admin','request_id':str(uuid4()),'expected_etag':self.view.etag,
            'expected_revision_id':str(self.view.current_event.revision.revision_id),'reason':'Synthetic','fields':[]})
        self.assertEqual(r.status_code,422)

    def test_authorized_correction_revalidation_and_history(self):
        self.enable_synthetic_bridge()
        from backend.review.models import CorrectionSubmission,FieldCorrection,HumanEvidenceInput
        evidence=self.store.evidence(self.id,self.admin)[0]
        submission=CorrectionSubmission(request_id=uuid4(),expected_etag=self.view.etag,
            expected_revision_id=self.view.current_event.revision.revision_id,reason='Synthetic local correction',
            human_evidence=(HumanEvidenceInput(evidence_id='human:api',raw_text='A2',source_evidence_ids=(evidence.evidence_id,),page=1,note='Synthetic assertion'),),
            fields=(FieldCorrection(field_name='location',state='PRESENT',value='A2',raw_value='A2',evidence_ids=('human:api',)),))
        r=self.client.post(f'/api/review/tasks/{self.id}/corrections',headers=self.headers,json=submission.model_dump(mode='json'))
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['state'],'REVALIDATION_REQUIRED')
        r=self.client.post(f'/api/review/tasks/{self.id}/revalidate',headers=self.headers,json={'expected_etag':r.json()['etag']})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['state'],'REVALIDATED')
        self.assertEqual(r.json()['current_event']['human_verification']['status'],'NOT_VERIFIED')
        self.assertEqual(r.json()['current_event']['calendar']['approval_status'],'NOT_APPROVED')
        self.assertEqual(self.client.get(f'/api/review/tasks/{self.id}/history',headers=self.headers).json()['corrections'][0]['status'],'SUBMITTED')

    def test_invalid_correction_retained_http422(self):
        self.enable_synthetic_bridge()
        body={'request_id':str(uuid4()),'expected_etag':self.view.etag,'expected_revision_id':str(self.view.current_event.revision.revision_id),
              'reason':'Synthetic unsupported change','fields':[{'field_name':'location','state':'PRESENT','value':'A2','raw_value':'A2',
                  'evidence_ids':[self.store.evidence(self.id,self.admin)[0].evidence_id]}]}
        r=self.client.post(f'/api/review/tasks/{self.id}/corrections',headers=self.headers,json=body)
        self.assertEqual(r.status_code,422)
        self.assertTrue(r.json()['detail']['proposal_retained'])

    def test_stale_http409(self):
        self.enable_synthetic_bridge()
        r=self.client.post(f'/api/review/tasks/{self.id}/revalidate',headers=self.headers,json={'expected_etag':1})
        self.assertEqual(r.status_code,409)

    def test_unknown_task_404(self):
        self.enable_synthetic_bridge()
        self.assertEqual(self.client.get('/api/review/tasks/'+str(uuid4()),headers=self.headers).status_code,404)

    def test_revoked_token_403(self):
        self.enable_synthetic_bridge(); self.auth.revoke(self.admin)
        self.assertEqual(self.client.get('/api/review/queue',headers=self.headers).status_code,403)

    def test_existing_m4_endpoint_unchanged(self):
        r=self.client.post('/api/reconstruct',json={'packet':self.request.source_packet.model_dump(mode='json'),
                                                  'decoder_mode':'constrained','profile':'m4-development-v1'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['canonical_digest'],self.request.reconstruction['canonical_digest'])

    def test_private_response_redacts_local_paths(self):
        from backend.review.api import _redact
        result=_redact({'raw_text':r'Synthetic source C:\private\source.pdf','nested':[r'D:/private/evidence.txt']})
        self.assertNotIn('C:\\',json.dumps(result))
        self.assertNotIn('D:/',json.dumps(result))

    def test_authenticated_ingest_and_queue_summary(self):
        self.enable_synthetic_bridge()
        from test_m5_policy import fixture
        request,_=fixture('CS202 Tuesday 12:00-13:00')
        r=self.client.post('/api/review/ingest',headers=self.headers,json=request.model_dump(mode='json'))
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(r.json()['task_ids']),1)
        self.assertEqual(self.client.get('/api/review/queue',headers=self.headers).json()['total'],2)
