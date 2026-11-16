"""Explicitly synthetic TEST_ONLY assertions, never student/Gold records."""
import json
import unittest
from uuid import uuid4
import test_s3b_review as review_tests
from backend.main import app
from backend.review.api import ReviewBridge, MAX_REVIEW_INGEST_BYTES
from backend.review.store import ReviewStore

class StudentCorrectionFlowTests(unittest.TestCase):
    setUp=review_tests.S3bReviewTests.setUp
    enable=review_tests.S3bReviewTests.enable
    post=review_tests.S3bReviewTests.post

    def test_large_bound_review_admission_is_queue_authorized_only(self):
        self.enable()
        app.state.m5_review_bridge=ReviewBridge(ReviewStore(self.authority))
        body=self.request.model_dump_json()+' '*(2*1024*1024)
        r=self.client.post('/api/review/ingest',content=body,headers={**self.headers,'Content-Type':'application/json'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len(r.json()['task_ids']),1)
        source=self.request.source_packet.source
        _,token=self.authority.issue('SYNTHETIC-READ-ONLY',{'read'},source_bindings={(source.source_document_id,source.content_sha256)},mode='TEST_ONLY')
        r=self.client.post('/api/review/ingest',content=body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        self.assertEqual(r.status_code,403)
        r=self.client.post('/api/review/ingest',content=' '*(MAX_REVIEW_INGEST_BYTES+1),headers={**self.headers,'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)
        r=self.client.post('/api/policy/assess',content=body,headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)

    def test_explicit_two_field_assertions_keep_history_and_block_writes(self):
        self.enable();before=self.task.current_event
        original=self.store.original_reconstruction(self.ident,self.actor)
        source_spans=self.store.evidence(self.ident,self.actor)
        parent=source_spans[0]
        body={'request_id':str(uuid4()),'expected_revision_id':str(before.revision.revision_id),
              'expected_etag':self.task.etag,'reason':'Invented TEST_ONLY source reading demonstration',
              'fields':[],'human_evidence':[]}
        for field,value in [('location','Z082'),('lecturer','Mira Vale')]:
            eid='human:'+uuid4().hex
            body['human_evidence'].append({'evidence_id':eid,'raw_text':value,'source_evidence_ids':[parent.evidence_id],
                                          'page':parent.page,'bbox':None,'note':'Synthetic assertion; no real timetable or human verification'})
            body['fields'].append({'field_name':field,'state':'PRESENT','value':value,'raw_value':value,'evidence_ids':[eid]})
        r=self.post('corrections',body);self.assertEqual(r.status_code,200,r.text)
        changed=r.json();event=changed['current_event']
        self.assertEqual(event['revision']['content']['location'],'Z082')
        self.assertEqual(event['revision']['content']['lecturer'],'Mira Vale')
        self.assertEqual(event['revision']['previous_revision_id'],str(before.revision.revision_id))
        self.assertEqual(event['revision']['version'],2)
        self.assertEqual(changed['state'],'REVALIDATION_REQUIRED')
        stale=self.post('corrections',{**body,'request_id':str(uuid4())})
        self.assertEqual(stale.status_code,409)
        r=self.post('revalidate',{'expected_etag':changed['etag']});self.assertEqual(r.status_code,200,r.text)
        current=r.json()['current_event']
        self.assertEqual(current['validation']['revision_id'],event['revision']['revision_id'])
        self.assertEqual(current['policy']['decision'],'ABSTAIN')
        self.assertEqual(current['human_verification']['status'],'NOT_VERIFIED')
        self.assertEqual(current['calendar']['approval_status'],'NOT_APPROVED')
        self.assertEqual(current['revision']['content']['recurrence'],before.revision.content.recurrence)
        snapshot=self.client.get(f'/api/review/tasks/{self.ident}/snapshot',headers=self.headers).json()
        self.assertEqual(len(snapshot['packet']['events']),1)
        self.assertEqual(snapshot['packet']['evidence'][:len(source_spans)],[s.model_dump(mode='json') for s in source_spans])
        history=self.client.get(f'/api/review/tasks/{self.ident}/history',headers=self.headers).json()
        self.assertEqual(len(history['revisions']),2)
        record=history['corrections'][0]
        self.assertEqual(record['actor_id'],self.actor.actor_id);self.assertTrue(record['recorded_at'])
        self.assertEqual(json.loads(record['before_json'])['revision'],before.revision.model_dump(mode='json'))
        self.assertEqual(json.loads(record['proposal_json'])['human_evidence'],body['human_evidence'])
        self.assertEqual(self.store.original_reconstruction(self.ident,self.actor),original)
        r=self.client.post('/api/calendar/v1/preview',json={'bundle':{'packet':snapshot['packet']}})
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['write_authorized'])
        self.assertTrue(all(op['action']=='BLOCKED' for op in r.json()['preview']['operations']))

if __name__=='__main__':unittest.main()
