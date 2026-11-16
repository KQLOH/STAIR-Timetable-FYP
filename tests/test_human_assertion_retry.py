"""Synthetic OCR spelling mismatch: failed assertion, explicit retry and restart."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.review.api import router, ReviewBridge
from backend.calendar.api import router as calendar_router
from backend.review.auth import TrustedHostAuthority
from backend.review.persistence import ReviewJournal, PersistentReviewStore
from test_m5_policy import fixture


@unittest.skipUnless(os.name=='nt','Windows encrypted review persistence')
class HumanAssertionRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='stair-assertion-synthetic-');self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.request,_=fixture('CS101 Monday 2033-02-07 09:00-11:00 Tutorial Room ZOO8')
        source=self.request.source_packet.source;self.actor='SYNTHETIC-ASSERTION-RETRY'
        bindings={(source.source_document_id,source.content_sha256)}
        self.authority=TrustedHostAuthority(test_only_source_bindings=bindings)
        self.principal,token=self.authority.issue(self.actor,{'read','queue','assign','correct','revalidate','oversee'},mode='TEST_ONLY',source_bindings=bindings)
        self.store=PersistentReviewStore(self.authority,ReviewJournal(self.path,self.actor))
        self.app=FastAPI();self.app.include_router(router);self.app.include_router(calendar_router)
        self.app.state.m5_review_bridge=ReviewBridge(self.store)
        self.client=TestClient(self.app,client=('127.0.0.1',43000));self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer '+token}
        self.ident=self.store.ingest(self.request,self.principal)[0]
        task=self.store.get(self.ident,self.principal)
        task=self.store.assign(self.ident,self.actor,task.etag,self.principal)
        self.task=self.store.start(self.ident,task.etag,self.principal)
        self.original=self.store.original_reconstruction(self.ident,self.principal)
        self.span=next(s for s in self.store.evidence(self.ident,self.principal) if 'ZOO8' in s.raw_text)

    def post(self,suffix,body):
        return self.client.post(f'/api/review/tasks/{self.ident}/'+suffix,headers=self.headers,json=body)

    def fail_without_assertion(self):
        body={'request_id':str(uuid4()),'expected_etag':self.task.etag,
              'expected_revision_id':str(self.task.current_event.revision.revision_id),
              'reason':'Synthetic human intends Z008 while OCR reads ZOO8',
              'fields':[{'field_name':'location','state':'PRESENT','raw_value':'Z008','value':'Z008',
                         'evidence_ids':[self.span.evidence_id],'alternatives':[],
                         'justification':'Explanatory text alone is not a structured source assertion'}],
              'human_evidence':[]}
        response=self.post('corrections',body)
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(response.json()['detail']['code'],'UNSUPPORTED_CORRECTION_VALUE')
        return body,self.store.history(self.ident,self.principal)['corrections'][0]

    def test_missing_structured_assertion_rejected_and_retained_after_restart(self):
        body,failed=self.fail_without_assertion()
        self.assertEqual(failed['status'],'INVALID');self.assertIsNone(failed['after_json'])
        self.assertEqual(json.loads(failed['proposal_json'])['fields'][0]['raw_value'],'Z008')
        restored=PersistentReviewStore(self.authority,ReviewJournal(self.path,self.actor))
        task=restored.get(self.ident,self.principal)
        self.assertEqual(task.state.value,'ESCALATED')
        self.assertEqual(task.current_event.revision.version,1)
        self.assertEqual(task.current_event.revision.content.location,'ZOO8')
        self.assertEqual(restored.history(self.ident,self.principal)['corrections'][0],failed)
        self.assertEqual(restored.original_reconstruction(self.ident,self.principal),self.original)

    def test_explicit_retry_creates_new_revision_without_rewriting_failure(self):
        body,failed=self.fail_without_assertion()
        task=self.store.get(self.ident,self.principal)
        for action in ('claim','start'):
            response=self.post('transition',{'action':action,'expected_etag':task.etag,'reason':'Synthetic explicit retry'})
            self.assertEqual(response.status_code,200,response.text)
            task=self.store.get(self.ident,self.principal)
        assertion='human:synthetic-retry'
        retry={**body,'request_id':str(uuid4()),'expected_etag':task.etag,
               'human_evidence':[{'evidence_id':assertion,'raw_text':'Z008','source_evidence_ids':[self.span.evidence_id],
                                  'page':self.span.page,'note':'Explicit synthetic reading from the selected source'}],
               'fields':[{**body['fields'][0],'evidence_ids':[assertion]}]}
        response=self.post('corrections',retry);self.assertEqual(response.status_code,200,response.text)
        current=response.json()['current_event']
        self.assertEqual(current['revision']['version'],2)
        self.assertEqual(current['revision']['previous_revision_id'],body['expected_revision_id'])
        self.assertEqual(current['revision']['content']['location'],'Z008')
        self.assertEqual(current['human_verification']['status'],'NOT_VERIFIED')
        response=self.post('revalidate',{'expected_etag':response.json()['etag']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['current_event']['policy']['decision'],'ABSTAIN')
        restored=PersistentReviewStore(self.authority,ReviewJournal(self.path,self.actor))
        history=restored.history(self.ident,self.principal)
        self.assertEqual(history['corrections'][0],failed)
        self.assertEqual([c['status'] for c in history['corrections']],['INVALID','SUBMITTED'])
        self.assertEqual(restored.original_reconstruction(self.ident,self.principal),self.original)
        self.assertEqual(next(s for s in restored.evidence(self.ident,self.principal) if s.evidence_id==self.span.evidence_id),self.span)
        snapshot=restored.snapshot(self.ident,self.principal)
        preview=self.client.post('/api/calendar/v1/preview',json={'bundle':{'packet':snapshot['packet']}})
        self.assertEqual(preview.status_code,200,preview.text);self.assertFalse(preview.json()['write_authorized'])
        self.assertTrue(all(o['action']=='BLOCKED' for o in preview.json()['preview']['operations']))
