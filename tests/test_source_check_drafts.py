"""Synthetic source-check drafts; never genuine human decisions or verification."""
from tempfile import TemporaryDirectory
from pathlib import Path
import json
import unittest
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_m7_analysis import make
from test_student_schedule_analysis import SyntheticSnapshotStore
from backend.analysis.schedule_checks import ScheduleChecks,CheckStart,InventorySubmission
from backend.analysis.schedule_drafts import DraftSubmission
from backend.review.persistence import ReviewJournal
from backend.review.api import router,ReviewBridge
from backend.review.models import ReviewError

class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='stair-draft-synthetic-');self.addCleanup(self.temp.cleanup)
        self.q=make([{'course_code':'SYN'+str(100+i),'start_time':f'{8+i:02d}:00','end_time':f'{9+i:02d}:00'} for i in range(9)])
        self.store=SyntheticSnapshotStore(self.q);s=self.q.packet.source;self.bindings={(s.source_document_id,s.content_sha256)}
        self.owner,self.token=self.store.authority.issue('SYNTHETIC-OWNER',{'read','oversee','correct'},source_bindings=self.bindings,mode='TEST_ONLY')
        self.manifest={'source_document_id':s.source_document_id,'content_sha256':s.content_sha256,'page_count':1}
        self.journal=lambda:ReviewJournal(Path(self.temp.name)/'checks','SYNTHETIC-DRAFT-ONLY')
        self.service=ScheduleChecks(self.store,self.journal,lambda s:self.manifest)
        self.check=self.service.start(CheckStart(task_id='synthetic-task',scope=self.q.scope),self.owner)
        self.rows=[{'candidate_id':e.candidate_id,'decision':'INCLUDE','page':'1','source_session':'Synthetic location '+str(i),'note':'Typed synthetic note '+str(i),'evidence_ids':[next(o.evidence_ids[0] for o in e.revision.observations if o.evidence_ids)]} for i,e in enumerate(self.q.packet.events)]
        self.form={'rows':self.rows,'checked_pages':[1],'source_confirmed':True,'scope_confirmed':True,'note':'Synthetic full source check, not a real human check','extra':None}
        self.client_id=uuid4()
        self.app=FastAPI();self.app.include_router(router);self.app.state.m5_review_bridge=ReviewBridge(self.store);self.app.state.m7_schedule_checks=self.service
        self.client=TestClient(self.app,client=('127.0.0.1',40000));self.addCleanup(self.client.close);self.headers={'Authorization':'Bearer '+self.token}
    def save_draft(self):return self.service.save_draft(self.check['check_id'],DraftSubmission(expected_version=1,client_id=self.client_id,form=self.form),self.owner)
    def primary(self):return {'expected_version':self.check['version'],**{k:v for k,v in self.form.items() if k!='extra'}}
    def test_nine_invalid_entries_preserved_without_source_check_audit(self):
        self.form['rows'][4]['page']='not a page';self.form['rows'][2]['source_session']='x'*201
        self.save_draft();record=self.service.get(self.check['check_id'],self.owner)
        self.assertEqual(record['status'],'DRAFT');self.assertEqual(len(record['audit']),1);self.assertIsNone(record['primary'])
        draft=self.service.recovery_drafts(self.check['check_id'],self.owner)['drafts'][0]
        self.assertEqual(draft['form'],self.form);self.assertIn('UNVALIDATED',draft['purpose'])
    def test_restart_recovers_every_typed_field(self):
        self.save_draft();fresh=ScheduleChecks(self.store,self.journal,lambda s:self.manifest)
        self.assertEqual(fresh.recovery_drafts(self.check['check_id'],self.owner)['drafts'][0]['form'],self.form)
    def test_structural_422_identifies_field_without_echoing_private_value(self):
        secret='SYNTHETIC_PRIVATE_VALUE'*15;body=self.primary();body['rows'][3]={**body['rows'][3],'source_session':secret}
        result=self.client.post('/api/review/schedule-checks/'+self.check['check_id']+'/primary',json=body,headers=self.headers)
        self.assertEqual(result.status_code,422);self.assertNotIn(secret,result.text)
        fields=result.json()['detail']['field_errors'];self.assertEqual(fields[0]['path'],['rows',3,'source_session']);self.assertIn('200',fields[0]['message'])
    def test_missing_evidence_422_identifies_exact_class(self):
        body=self.primary();body['rows'][6]={**body['rows'][6],'evidence_ids':[]}
        result=self.client.post('/api/review/schedule-checks/'+self.check['check_id']+'/primary',json=body,headers=self.headers)
        self.assertEqual(result.status_code,422);self.assertEqual(result.json()['detail']['field_errors'][0]['path'],['rows',6,'evidence_ids'])
        self.assertEqual(result.json()['detail']['code'],'INCLUDED_SESSION_EVIDENCE_REQUIRED')
    def test_corrected_explicit_save_then_reopen_all_fields(self):
        self.save_draft();body=self.primary();result=self.client.post('/api/review/schedule-checks/'+self.check['check_id']+'/primary',json=body,headers=self.headers)
        self.assertEqual(result.status_code,200,result.text);check=result.json();self.assertIsNotNone(check['primary']);self.assertFalse(check['current_complete_verification'])
        reopened=self.service.get(self.check['check_id'],self.owner)
        for old,new in zip(self.rows,reopened['primary']['rows']):
            self.assertEqual({**old,'page':1},new)
        self.assertEqual(self.service.recovery_drafts(self.check['check_id'],self.owner)['drafts'],[])
    def test_different_actor_cannot_read_or_overwrite_recovery(self):
        self.save_draft();other,_=self.store.authority.issue('SYNTHETIC-OTHER',{'read','oversee','correct'},source_bindings=self.bindings,mode='TEST_ONLY')
        with self.assertRaises(ReviewError):self.service.recovery_drafts(self.check['check_id'],other)
        with self.assertRaises(ReviewError):self.service.save_draft(self.check['check_id'],DraftSubmission(expected_version=1,client_id=uuid4(),form=self.form),other)
    def test_wrong_source_and_unauthorized_requests_refused(self):
        self.save_draft();other,_=self.store.authority.issue('SYNTHETIC-WRONG-SOURCE',{'read','oversee','correct'},source_bindings={('other','0'*64)})
        with self.assertRaises(ReviewError):self.service.recovery_drafts(self.check['check_id'],other)
        self.assertEqual(self.client.get('/api/review/schedule-checks/'+self.check['check_id']+'/draft').status_code,403)
    def test_stale_revision_draft_cannot_be_applied(self):
        self.save_draft();self.store.packet=self.store.packet.model_copy(update={'events':self.store.packet.events[:-1]})
        with self.assertRaises(ReviewError):self.service.recovery_drafts(self.check['check_id'],self.owner)
    def test_multiple_browser_drafts_do_not_overwrite_each_other(self):
        self.save_draft();second={**self.form,'note':'Synthetic second tab'}
        self.service.save_draft(self.check['check_id'],DraftSubmission(expected_version=1,client_id=uuid4(),form=second),self.owner)
        drafts=self.service.recovery_drafts(self.check['check_id'],self.owner)['drafts'];self.assertEqual(len(drafts),2);self.assertEqual({d['form']['note'] for d in drafts},{self.form['note'],second['note']})
    def test_recovery_text_and_credentials_not_plaintext_on_disk(self):
        self.form['note']='SYNTHETIC_PRIVATE_RECOVERY_NOTE';self.save_draft()
        for path in Path(self.temp.name).rglob('*.dpapi'):self.assertNotIn(b'SYNTHETIC_PRIVATE_RECOVERY_NOTE',path.read_bytes())
    def test_unvalidated_draft_has_no_verification_or_calendar_effect(self):
        self.save_draft();record=self.service.get(self.check['check_id'],self.owner)
        self.assertFalse(record['current_complete_verification']);self.assertFalse(record['calendar_authorized']);self.assertEqual(record['status'],'DRAFT')

    def test_catalog_finds_original_draft_across_task_order_without_writes(self):
        self.save_draft()
        other=self.service.start(CheckStart(task_id='another-task-same-run',scope=self.q.scope),self.owner)
        journal=self.service._transaction()
        with journal.locked():before=journal.load()
        catalog=self.service.available_checks('another-task-same-run',self.owner)
        found={c['check_id']:c for c in catalog['checks']}
        self.assertEqual(found[self.check['check_id']]['recovery_rows'],9)
        self.assertEqual(found[other['check_id']]['recovery_drafts'],0)
        with journal.locked():self.assertEqual(before,journal.load())
    def test_catalog_never_exposes_other_owner_drafts(self):
        self.save_draft();other,_=self.store.authority.issue('SYNTHETIC-OTHER',{'read','oversee','correct'},source_bindings=self.bindings,mode='TEST_ONLY')
        self.assertEqual(self.service.available_checks('synthetic-task',other)['checks'],[])
    def test_catalog_rejects_wrong_source_and_read_only_permissions(self):
        other,_=self.store.authority.issue('SYNTHETIC-WRONG',{'read','oversee','correct'},source_bindings={('other','0'*64)})
        with self.assertRaises(ReviewError):self.service.available_checks('synthetic-task',other)
        readonly,_=self.store.authority.issue('SYNTHETIC-READONLY',{'read','oversee'},source_bindings=self.bindings,mode='TEST_ONLY')
        with self.assertRaises(ReviewError):self.service.available_checks('synthetic-task',readonly)
    def test_catalog_retains_but_does_not_offer_stale_draft(self):
        self.save_draft();self.store.packet=self.store.packet.model_copy(update={'events':self.store.packet.events[:-1]})
        result=self.service.available_checks('synthetic-task',self.owner)
        self.assertEqual(result['checks'],[]);self.assertEqual(result['stale_recovery_checks'],1)
        with self.service._transaction().locked():self.assertIn(self.check['check_id'],self.service._load(self.service._transaction())['drafts'])
    def test_catalog_api_is_authorized_and_does_not_return_private_draft_text(self):
        path='/api/review/schedule-checks/for-task/'+str(uuid4())+'/available'
        self.save_draft();self.assertEqual(self.client.get(path).status_code,403)
        response=self.client.get(path,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['checks'][0]['recovery_rows'],9)
        self.assertNotIn('Typed synthetic note',response.text)
