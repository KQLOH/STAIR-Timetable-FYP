"""Invented review records; real Windows user-bound storage, never Gold records."""
from datetime import timedelta
from hashlib import sha256
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.review.auth import TrustedHostAuthority, utc_now
from backend.review.persistence import PersistentReviewStore, ReviewJournal
from backend.review.models import CorrectionSubmission, FieldCorrection, HumanEvidenceInput, ReviewError
from backend.review.windows_security import WindowsProtection, current_actor
from test_m5_policy import fixture


@unittest.skipUnless(os.name == 'nt', 'Windows logon and DPAPI integration')
class ReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='stair-private-synthetic-')
        self.addCleanup(self.temp.cleanup); self.path = Path(self.temp.name)
        self.request, _ = fixture('CS101 Monday 2033-02-07 09:00-11:00 Lecture Room A101')
        source = self.request.source_packet.source
        self.bindings = {(source.source_document_id, source.content_sha256)}
        self.actor = 'SYNTHETIC-PERSISTENCE-ONLY'
        self.authority = TrustedHostAuthority(test_only_source_bindings=self.bindings)
        self.principal, self.token = self.authority.issue(self.actor, {'read','queue','assign','correct','revalidate','oversee'},
            source_bindings=self.bindings, mode='TEST_ONLY')
        self.journal = ReviewJournal(self.path, self.actor)
        self.store = PersistentReviewStore(self.authority, self.journal)
        self.ident = self.store.ingest(self.request, self.principal)[0]

    def restart(self):
        return PersistentReviewStore(self.authority, ReviewJournal(self.path, self.actor))

    def correction(self):
        task = self.store.get(self.ident, self.principal)
        task = self.store.assign(self.ident, self.actor, task.etag, self.principal)
        task = self.store.start(self.ident, task.etag, self.principal)
        source = self.store.evidence(self.ident, self.principal)[0]
        return CorrectionSubmission(request_id=uuid4(), expected_revision_id=task.current_event.revision.revision_id,
            expected_etag=task.etag, reason='Invented source assertion for persistence regression',
            human_evidence=(HumanEvidenceInput(evidence_id='human:synthetic-persist',raw_text='Z082',
                source_evidence_ids=(source.evidence_id,),page=source.page,note='Synthetic only'),),
            fields=(FieldCorrection(field_name='location',state='PRESENT',value='Z082',raw_value='Z082',evidence_ids=('human:synthetic-persist',)),))

    def test_real_windows_identity_and_dpapi(self):
        self.assertTrue(current_actor().startswith('windows-logon:S-1-'))
        cipher=WindowsProtection();raw=b'SYNTHETIC private evidence';encrypted=cipher.protect(raw)
        self.assertNotIn(raw,encrypted);self.assertEqual(cipher.unprotect(encrypted),raw)
        with self.assertRaises(RuntimeError):cipher.unprotect(encrypted[:-4]+b'xxxx')

    def test_restart_preserves_actor_timestamp_revisions_and_validation(self):
        original=self.store.original_reconstruction(self.ident,self.principal)
        proposal=self.correction(); result=self.store.submit(self.ident,proposal,self.principal)
        task=self.store.revalidate(self.ident,result['task'].etag,self.principal)
        history=self.store.history(self.ident,self.principal)
        restored=self.restart()
        self.assertEqual(restored.get(self.ident,self.principal),task)
        self.assertEqual(restored.history(self.ident,self.principal),history)
        self.assertEqual(restored.original_reconstruction(self.ident,self.principal),original)
        self.assertEqual(history['corrections'][0]['actor_id'],self.actor)
        self.assertTrue(history['corrections'][0]['recorded_at'])
        self.assertEqual(task.current_event.human_verification.status,'NOT_VERIFIED')
        self.assertEqual(task.current_event.policy.decision,'ABSTAIN')
        self.assertEqual(task.current_event.calendar.approval_status,'NOT_APPROVED')

    def test_stale_revision_and_replay_rejected_after_restart(self):
        proposal=self.correction(); self.store.submit(self.ident,proposal,self.principal)
        restored=self.restart()
        with self.assertRaises(ReviewError):restored.submit(self.ident,proposal,self.principal)
        task=restored.get(self.ident,self.principal)
        forged=proposal.model_copy(update={'request_id':uuid4(),'expected_etag':task.etag,'expected_revision_id':uuid4()})
        with self.assertRaisesRegex(ReviewError,'STALE_EVENT_REVISION'):restored.submit(self.ident,forged,self.principal)
        self.assertEqual(restored.get(self.ident,self.principal).current_event.revision.version,2)

    def test_new_authority_requires_fresh_grant(self):
        authority=TrustedHostAuthority(test_only_source_bindings=self.bindings)
        restored=PersistentReviewStore(authority,ReviewJournal(self.path,self.actor))
        with self.assertRaises(ReviewError):restored.get(self.ident,self.principal)
        principal,_=authority.issue(self.actor,{'read','oversee'},source_bindings=self.bindings,mode='TEST_ONLY')
        self.assertEqual(restored.get(self.ident,principal).task_id,self.store.get(self.ident,self.principal).task_id)

    def test_wrong_scope_and_actor_do_not_gain_access(self):
        foreign,_=self.authority.issue('SYNTHETIC-OTHER',{'read','oversee'},source_bindings={('OTHER','a'*64)})
        with self.assertRaises(ReviewError):self.restart().get(self.ident,foreign)
        with self.assertRaises(ReviewError):PersistentReviewStore(self.authority,ReviewJournal(self.path,'WRONG-OWNER'))

    def test_plaintext_and_grants_not_written(self):
        self.store.submit(self.ident,self.correction(),self.principal)
        for path in self.path.rglob('*'):
            if path.is_file():
                raw=path.read_bytes()
                for secret in (b'CS101',b'Z082',self.token.encode(),b'Invented source assertion'):
                    self.assertNotIn(secret,raw)

    def test_corruption_fails_closed(self):
        record=next(self.journal.records.iterdir());record.write_bytes(b'TAMPERED')
        with self.assertRaises(ReviewError):self.restart()

    def test_deleted_tail_fails_closed(self):
        record=next(self.journal.records.iterdir());record.unlink()
        with self.assertRaises(ReviewError):self.restart()

    def test_unpublished_staging_is_not_replayed(self):
        (self.path/'synthetic.staging').write_bytes(b'not a committed record')
        self.assertEqual(len(self.restart().queue_summary(self.principal)['tasks']),1)

    def test_failed_save_does_not_acknowledge_or_change_memory(self):
        task=self.store.get(self.ident,self.principal)
        with patch.object(self.journal,'append',side_effect=OSError('Synthetic disk failure')):
            with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_WRITE_FAILED'):self.store.assign(self.ident,self.actor,task.etag,self.principal)
        self.assertEqual(self.restart().get(self.ident,self.principal),task)

    def test_two_store_instances_share_current_etag(self):
        other=self.restart();task=self.store.get(self.ident,self.principal)
        self.store.assign(self.ident,self.actor,task.etag,self.principal)
        with self.assertRaisesRegex(ReviewError,'STALE_REVIEW_TASK'):other.assign(self.ident,self.actor,task.etag,self.principal)

    def test_expiry_is_not_removed_by_restart(self):
        with patch.object(self.authority,'clock',return_value=utc_now()+timedelta(hours=2)):
            with self.assertRaises(ReviewError):self.restart().get(self.ident,self.principal)

    def test_storage_metadata_is_honest(self):
        self.assertEqual(self.store.queue_summary(self.principal)['storage'],'LOCAL_WINDOWS_DPAPI_RESTART_PERSISTENT')
        self.assertEqual(self.store.snapshot(self.ident,self.principal)['storage'],'LOCAL_WINDOWS_DPAPI_RESTART_PERSISTENT')

    def test_audit_chain_tampering_rejected_even_when_reencrypted(self):
        with self.journal.locked():
            state,_,_=self.journal.load()
            state['tasks'][self.ident]['audits'][0]['reason']='tampered'
            self.journal.append(state)
        with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_INVALID_STATE'):self.restart()

    def test_separate_python_process_restores_exact_history(self):
        proposal=self.correction();result=self.store.submit(self.ident,proposal,self.principal)
        self.store.revalidate(self.ident,result['task'].etag,self.principal)
        from backend.reconstruction.models import digest
        expected=digest(self.store.history(self.ident,self.principal))
        program='''
import json,sys
from backend.review.auth import TrustedHostAuthority
from backend.review.persistence import ReviewJournal,PersistentReviewStore
from backend.reconstruction.models import digest
directory,actor,ident,binding=sys.argv[1:]
bindings={tuple(json.loads(binding))}
authority=TrustedHostAuthority(test_only_source_bindings=bindings)
principal,_=authority.issue(actor,{'read','oversee'},source_bindings=bindings,mode='TEST_ONLY')
store=PersistentReviewStore(authority,ReviewJournal(directory,actor))
print(digest(store.history(ident,principal)))
'''
        r=subprocess.run([sys.executable,'-B','-c',program,str(self.path),self.actor,self.ident,json.dumps(next(iter(self.bindings)))],
                         cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),expected)

    def test_record_capacity_refused_before_publication(self):
        before=list(self.journal.records.iterdir())
        with patch.object(self.journal,'load',return_value=({},10000,'a'*64)):
            with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_CAPACITY'):self.journal.append({})
        self.assertEqual(list(self.journal.records.iterdir()),before)
