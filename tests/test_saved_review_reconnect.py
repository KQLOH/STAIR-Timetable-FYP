"""Synthetic corrections + real DPAPI restart + read-only frontend restore contracts."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.review.api import router,ReviewBridge
from backend.calendar.api import router as calendar_router
from backend.review.auth import TrustedHostAuthority
from backend.review.persistence import ReviewJournal,PersistentReviewStore
from backend.review.models import CorrectionSubmission,FieldCorrection,HumanEvidenceInput
from test_m5_policy import fixture

ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(os.name=='nt','Windows DPAPI review integration')
class SavedReviewReconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='stair-reconnect-SYNTHETIC-');cls.root=Path(cls.temp.name)
        request,_=fixture('CS101 Monday 2033-02-07 09:00-10:00 Tutorial Room TBA; source amendment Z018',
            'MA202 Monday 2033-02-07 11:00-12:00 Tutorial Room B201 Lecturer: Alex Rlver; source amendment Alex River')
        source=request.source_packet.source;bindings={(source.source_document_id,source.content_sha256)}
        actor='SYNTHETIC-SAVED-RECONNECT'
        authority=TrustedHostAuthority(test_only_source_bindings=bindings)
        principal,_=authority.issue(actor,{'read','queue','assign','correct','revalidate','oversee'},source_bindings=bindings,mode='TEST_ONLY')
        store=PersistentReviewStore(authority,ReviewJournal(cls.root/'journal',actor))
        ids=store.ingest(request,principal);assert len(ids)==2
        originals={i:store.original_reconstruction(i,principal) for i in ids}
        for i in ids:
            task=store.get(i,principal);task=store.assign(i,actor,task.etag,principal);task=store.start(i,task.etag,principal)
            field,value=('location','Z018') if task.current_event.revision.content.course_code=='CS101' else ('lecturer','Alex River')
            span=next(s for s in store.evidence(i,principal) if value in s.raw_text)
            eid='human:synthetic-'+uuid4().hex
            submission=CorrectionSubmission(request_id=uuid4(),expected_etag=task.etag,expected_revision_id=task.current_event.revision.revision_id,
                reason='Explicitly synthetic source-amendment reading; not a real student correction',
                human_evidence=(HumanEvidenceInput(evidence_id=eid,raw_text=value,source_evidence_ids=(span.evidence_id,),page=span.page,note='Invented source amendment'),),
                fields=(FieldCorrection(field_name=field,state='PRESENT',raw_value=value,value=value,evidence_ids=(eid,)),))
            store.submit(i,submission,principal)
        pending=store.snapshot(ids[0],principal)
        for i in ids:
            task=store.get(i,principal);store.revalidate(i,task.etag,principal)
        history={i:store.history(i,principal) for i in ids}
        before={p.name:p.read_bytes() for p in store.journal.records.iterdir()}
        authority2=TrustedHostAuthority(test_only_source_bindings=bindings)
        store2=PersistentReviewStore(authority2,ReviewJournal(cls.root/'journal',actor))
        principal2,token=authority2.issue(actor,{'read','oversee'},source_bindings=bindings,mode='TEST_ONLY')
        app=FastAPI();app.include_router(router);app.include_router(calendar_router);app.state.m5_review_bridge=ReviewBridge(store2)
        headers={'Authorization':'Bearer '+token}
        with TestClient(app,client=('127.0.0.1',44000)) as client:
            assert client.get('/api/review/tasks/'+ids[0]+'/snapshot').status_code==403
            session=client.get('/api/review/session',headers=headers).json()
            queue=client.get('/api/review/queue',headers=headers).json()
            snapshot=client.get('/api/review/tasks/'+ids[0]+'/snapshot',headers=headers).json()
            preview=client.post('/api/calendar/v1/preview',json={'bundle':{'packet':snapshot['packet']}}).json()
            cls.asserted_read_only=all(store2.history(i,principal2)==history[i] and store2.original_reconstruction(i,principal2)==originals[i] for i in ids)
        assert before=={p.name:p.read_bytes() for p in store2.journal.records.iterdir()}
        cls.originals_untouched=cls.asserted_read_only
        cls.fixture_path=cls.root/'synthetic-reconnect.json'
        original_result=request.model_dump(mode='json')
        result={'extraction':original_result['source_packet'],'reconstruction':original_result['reconstruction'],'stages':{},'pipeline':'evidence'}
        original=result['reconstruction']['canonical_output']['packet'].copy()
        original['events']=original['events']+result['reconstruction']['canonical_output']['unresolved_candidates']
        cls.fixture_path.write_text(json.dumps({'session':session,'queue':queue,'snapshot':snapshot,'pending_snapshot':pending,
            'original':original,'original_result':result,'preview':preview}),encoding='utf-8')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        result=subprocess.run(['node',str(ROOT/'tests/saved_review_reconnect_checks.cjs'),str(self.fixture_path),case],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_restart_and_read_restore_preserve_history(self):self.assertTrue(self.originals_untouched)
    def test_restore_current_values_without_new_corrections(self):self.check('restore')
    def test_unauthorized_cannot_restore(self):self.check('unauthorized')
    def test_source_hash_and_identity_must_match(self):self.check('binding')
    def test_competing_runs_abstain(self):self.check('ambiguous')
    def test_machine_only_run_cannot_replace_saved_corrections(self):self.check('machine_run')
    def test_late_source_change_cannot_apply(self):self.check('source_change')
    def test_pending_revalidation_still_needs_action(self):self.check('pending')
    def test_subset_keeps_unmatched_automatic_classes(self):self.check('subset')
    def test_independent_verification_requires_current_record(self):self.check('verification')
