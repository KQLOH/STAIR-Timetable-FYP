"""Invented sources and mocked credential dialogs; never real reviewer approvals."""
import ctypes,json
from ctypes import wintypes
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import unittest
from fastapi.testclient import TestClient
from test_m7_analysis import make,value
from test_student_schedule_analysis import SyntheticSnapshotStore
from backend.analysis.schedule_checks import ScheduleChecks,CheckStart,InventorySubmission,VerificationSubmission,CheckAnalysis
from backend.analysis.temporal_scope import TemporalSubmission
from backend.review.auth import utc_now
from backend.review.local_host import LocalReviewHost,create_host_app,ORIGIN
from backend.review.persistence import ReviewJournal
from backend.review.models import ReviewError
from backend.review.independent import IndependentAccess

class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='stair-fr7-ops-synthetic-');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.q=make();self.store=SyntheticSnapshotStore(self.q);self.store.storage_kind='SYNTHETIC_ONLY'
        self.host=LocalReviewHost(self.store,'SYNTHETIC-PRIMARY',identity=lambda:'SYNTHETIC-PRIMARY',grant_mode='TEST_ONLY')
        self.file=self.root/'source.txt';self.file.write_bytes(self.q.packet.evidence[0].raw_text.encode())
        self.app=create_host_app(self.host)
        source=self.q.packet.source
        _,pending=self.host.request_access({'source_document_id':source.source_document_id,'content_sha256':source.content_sha256})
        self.host.authorize_from_console(pending['nonce'],self.file)
        self.owner=next(iter(self.host.channels.values())).principal
        manifest=self.host.source_manifests[(source.source_document_id,source.content_sha256)]
        self.checks=ScheduleChecks(self.store,lambda:ReviewJournal(self.root/'checks','SYNTHETIC'),lambda _:manifest)
        self.app.state.m7_schedule_checks=self.checks
        self.identity=Mock(return_value='SYNTHETIC-INDEPENDENT')
        self.access=IndependentAccess(self.host,self.checks,authenticate=self.identity)
        self.host.independent=self.access;self.app.state.independent_access=self.access
        self.check=self.checks.start(CheckStart(task_id='synthetic-task',scope=self.q.scope),self.owner)
    def primary(self):
        self.check=self.checks.submit(self.check['check_id'],InventorySubmission(expected_version=self.check['version'],checked_pages=(1,),rows=[{'decision':'INCLUDE','candidate_id':e.candidate_id,'page':1,'source_session':'Synthetic session '+str(i),'evidence_ids':[next(o.evidence_ids[0] for o in e.revision.observations if o.evidence_ids)]} for i,e in enumerate(self.store.packet.events)],source_confirmed=True,scope_confirmed=True,note='Synthetic source inventory only'),self.owner)
    def channel(self):
        self.primary();code=self.access.invite_from_console(self.check['check_id'],self.file)
        cookie,pending=self.access.request(code);self.access.authenticate_from_console(pending['nonce'])
        return cookie,pending,self.access.authenticate(cookie,pending['binding'])
    def verify_body(self,record):
        check=self.checks.get(record.invitation.check_id,record.principal)
        return VerificationSubmission(expected_version=check['version'],source_confirmed=True,inventory_confirmed=True,temporal_context_confirmed=True,note='SYNTHETIC independent confirmation only')
    def context(self,**changes):
        e=self.store.packet.events[0];ref=next(o.evidence_ids[0] for o in e.revision.observations if o.evidence_ids)
        reading={'page':1,'evidence_ids':[ref],'raw_reading':'SYNTHETIC source: 2026-01-05 to 2026-01-18; scope also 2026-01-11; Etc/UTC; synthetic-cohort; weekly; weeks 1-2; no exclusions','note':'Invented source reading, not real human data','source_confirmed':True}
        body={'expected_version':self.check['version'],'scope':self.q.scope.model_dump(mode='json'),'timezone':'Etc/UTC','cohort_id':'synthetic-cohort','term':{'start':'2026-01-05','end':'2026-01-18'},'weeks':[{'week':1,'start':'2026-01-05','end':'2026-01-11','reading':{**reading,'raw_reading':'SYNTHETIC week 1 2026-01-05 to 2026-01-11'}},{'week':2,'start':'2026-01-12','end':'2026-01-18','reading':{**reading,'raw_reading':'SYNTHETIC week 2 2026-01-12 to 2026-01-18'}}],'events':[],'reading':reading};body.update(changes);return TemporalSubmission.model_validate(body)
    def test_native_authentication_required_before_access(self):
        self.primary();code=self.access.invite_from_console(self.check['check_id'],self.file);cookie,p=self.access.request(code)
        with self.assertRaises(ReviewError):self.access.authenticate(cookie,p['binding'])
    def test_owner_identity_refused(self):
        self.identity.return_value=self.owner.actor_id
        with self.assertRaises(ReviewError):self.channel()
        self.assertFalse(self.access.channels)
    def test_correction_author_identity_refused(self):
        self.store.correction_authors=['SYNTHETIC-INDEPENDENT']
        with self.assertRaises(ReviewError):self.channel()
    def test_sign_in_cancellation_does_not_create_grant(self):
        self.identity.side_effect=RuntimeError('REVIEWER_SIGN_IN_CANCELLED')
        with self.assertRaises(RuntimeError):self.channel()
        self.assertFalse(self.access.channels)
    def test_separate_read_only_identity_and_final_confirmation(self):
        cookie,p,record=self.channel();self.assertNotEqual(record.principal,self.owner);self.assertEqual(record.principal.permissions,frozenset({'read','oversee'}))
        pending=self.access.request_verification(record,self.verify_body(record))
        self.assertFalse(self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)['completeness']['trusted_complete'])
        self.access.confirm_from_console(pending['nonce'])
        result=self.checks.analyze(self.check['check_id'],CheckAnalysis(availability=[{'start':'2026-01-05T08:00','end':'2026-01-05T12:00'}]),self.owner)
        self.assertTrue(result['completeness']['trusted_complete']);self.assertTrue(result['completeness']['synthetic']);self.assertFalse(result['calendar_operations'])
        self.assertEqual(self.identity.call_count,2)
    def test_final_confirmation_cannot_use_another_identity(self):
        _,_,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record));self.identity.return_value=self.owner.actor_id
        with self.assertRaises(ReviewError):self.access.confirm_from_console(pending['nonce'])
        self.assertFalse(self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)['completeness']['trusted_complete'])
    def test_revocation_removes_verified_analysis(self):
        _,_,record=self.channel();p=self.access.request_verification(record,self.verify_body(record));self.access.confirm_from_console(p['nonce']);self.access.revoke_from_console()
        self.assertFalse(self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)['completeness']['trusted_complete'])
    def test_invitation_replay_refused(self):
        self.primary();code=self.access.invite_from_console(self.check['check_id'],self.file);_,p=self.access.request(code);self.access.authenticate_from_console(p['nonce'])
        with self.assertRaises(ReviewError):self.access.request(code)
    def test_expired_invitation_refused(self):
        self.primary();code=self.access.invite_from_console(self.check['check_id'],self.file);self.access.clock=lambda:10**12
        with self.assertRaises(ReviewError):self.access.request(code)
    def test_wrong_source_cannot_be_shared(self):
        self.primary();wrong=self.root/'wrong.txt';wrong.write_text('SYNTHETIC wrong')
        with self.assertRaises(ReviewError):self.access.invite_from_console(self.check['check_id'],wrong)
    def test_scope_change_invalidates_reviewer_channel(self):
        cookie,p,_=self.channel();self.check=self.checks.get(self.check['check_id'],self.owner);self.checks.save_temporal(self.check['check_id'],self.context(),self.owner)
        with self.assertRaises(ReviewError):self.access.authenticate(cookie,p['binding'])
    def test_unknown_values_are_not_filled(self):
        body=self.context(timezone=None,cohort_id=None,term=None,weeks=[])
        result=self.checks.save_temporal(self.check['check_id'],body,self.owner)
        self.assertEqual(result['temporal']['submission']['timezone'],None);self.assertIsNone(result['verification'])
    def test_wrong_source_reading_rejected(self):
        body=self.context();bad=body.model_copy(update={'reading':body.reading.model_copy(update={'evidence_ids':('another-source',)})})
        with self.assertRaises(ReviewError):self.checks.save_temporal(self.check['check_id'],bad,self.owner)
    def test_unconfirmed_context_rejected(self):
        body=self.context();bad=body.model_copy(update={'reading':body.reading.model_copy(update={'source_confirmed':False})})
        with self.assertRaises(ReviewError):self.checks.save_temporal(self.check['check_id'],bad,self.owner)
    def test_bad_week_mapping_rejected(self):
        body=self.context();body=body.model_copy(update={'weeks':(body.weeks[0],body.weeks[0])})
        with self.assertRaises(ReviewError):self.checks.save_temporal(self.check['check_id'],body,self.owner)
    def test_existing_timezone_not_overwritten(self):
        body=self.context(timezone='Europe/London',scope={**self.q.scope.model_dump(mode='json'),'timezone':'Europe/London'});body=body.model_copy(update={'reading':body.reading.model_copy(update={'raw_reading':body.reading.raw_reading+' Europe/London'})})
        with self.assertRaises(ReviewError):self.checks.save_temporal(self.check['check_id'],body,self.owner)
    def test_expired_confirmation_cannot_record_verification(self):
        _,_,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record));now=self.access.clock();self.access.clock=lambda:now+121
        with self.assertRaises(ReviewError):self.access.confirm_from_console(pending['nonce'])
        self.assertFalse(self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)['completeness']['trusted_complete'])
    def test_expired_reviewer_channel_loses_access(self):
        cookie,p,_=self.channel();now=self.access.clock();self.access.clock=lambda:now+901
        with self.assertRaises(ReviewError):self.access.authenticate(cookie,p['binding'])
    def test_missing_source_session_blocks_invitation(self):
        self.primary();body=InventorySubmission(expected_version=self.check['version'],checked_pages=(1,),rows=[],source_confirmed=True,scope_confirmed=True,note='Synthetic incomplete source')
        self.checks.submit(self.check['check_id'],body,self.owner)
        with self.assertRaises(ReviewError):self.access.invite_from_console(self.check['check_id'],self.file)
    def test_wrong_class_group_cannot_create_a_false_free_schedule(self):
        self.check=self.checks.start(CheckStart(task_id='synthetic-task',scope=self.q.scope.model_copy(update={'cohort_id':'SYNTHETIC-WRONG-GROUP'})),self.owner)
        _,_,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record))
        with self.assertRaises(ReviewError):self.access.confirm_from_console(pending['nonce'])
        self.assertFalse(self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)['completeness']['trusted_complete'])
    def test_bad_temporal_order_does_not_gain_verification(self):
        event=self.store.packet.events[0];content=event.revision.content.model_copy(update={'start_time':'11:00','end_time':'09:00'})
        self.store.packet=self.store.packet.model_copy(update={'events':(event.model_copy(update={'revision':event.revision.model_copy(update={'content':content})}),)})
        self.check=self.checks.start(CheckStart(task_id='synthetic-task',scope=self.q.scope),self.owner)
        _,_,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record))
        with self.assertRaises(ReviewError):self.access.confirm_from_console(pending['nonce'])
    def test_runtime_invitation_never_survives_restart(self):
        cookie,p,record=self.channel();fresh=IndependentAccess(self.host,self.checks,authenticate=self.identity)
        with self.assertRaises(ReviewError):fresh.authenticate(cookie,p['binding'])
        self.assertFalse(fresh.invites)
    def test_weekly_scope_exclusions_and_original_preservation(self):
        original=self.store.packet.model_dump_json();event=self.store.packet.events[0]
        body=self.context(scope={**self.q.scope.model_dump(mode='json'),'end':'2026-01-18'},events=[{'candidate_id':event.candidate_id,'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-18','source_rule':'weekly'},'teaching_weeks':[1,2],'excluded_weeks':[2]}])
        self.check=self.checks.save_temporal(self.check['check_id'],body,self.owner)
        cookie,p,record=self.channel();approval=self.access.request_verification(record,self.verify_body(record));self.access.confirm_from_console(approval['nonce'])
        result=self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)
        self.assertTrue(result['completeness']['trusted_complete']);self.assertEqual(result['verified_workload']['class_contact_hours'],1)
        self.assertEqual(self.store.packet.model_dump_json(),original)
        self.assertEqual(result['verified_workload']['contact_hours_by_calendar_week']['2026-01-12'],0)
    def test_missing_week_mapping_prevents_verification(self):
        event=self.store.packet.events[0]
        body=self.context(events=[{'candidate_id':event.candidate_id,'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-18','source_rule':'weekly'},'teaching_weeks':[1,2]}])
        body=body.model_copy(update={'weeks':body.weeks[:1]});self.check=self.checks.save_temporal(self.check['check_id'],body,self.owner)
        _,_,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record))
        with self.assertRaises(ReviewError):self.access.confirm_from_console(pending['nonce'])
        self.assertFalse(self.checks.analyze(self.check['check_id'],CheckAnalysis(),self.owner)['completeness']['trusted_complete'])
    def test_reviewer_cannot_write_owner_temporal_context(self):
        _,_,record=self.channel()
        with self.assertRaises(ReviewError):self.checks.save_temporal(self.check['check_id'],self.context(),record.principal)
    def test_changed_inventory_invalidates_pending_confirmation(self):
        cookie,p,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record));self.check=self.checks.get(self.check['check_id'],self.owner);self.primary()
        with self.assertRaises(ReviewError):self.access.confirm_from_console(pending['nonce'])
    def test_no_feasible_windows_after_synthetic_confirmation(self):
        _,_,record=self.channel();pending=self.access.request_verification(record,self.verify_body(record));self.access.confirm_from_console(pending['nonce'])
        result=self.checks.analyze(self.check['check_id'],CheckAnalysis(availability=[{'start':'2026-01-05T09:00','end':'2026-01-05T10:00'}]),self.owner)
        self.assertEqual(value(result,'verified_available_windows'),[])
    def test_http_has_no_authentication_or_role_switch_route(self):
        with TestClient(self.app,base_url=ORIGIN,client=('127.0.0.1',40000)) as client:
            headers={'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'}
            self.assertEqual(client.post('/__local_review__/authenticate-reviewer',json={'actor':'SYNTHETIC'},headers=headers).status_code,403)
            self.assertEqual(client.get('/api/independent-review/current',headers=headers).status_code,403)
            self.assertEqual(client.post('/api/independent-review/corrections',json={},headers=headers).status_code,403)
    def test_real_boundary_supports_separate_cookie(self):
        self.primary();code=self.access.invite_from_console(self.check['check_id'],self.file)
        with TestClient(self.app,base_url=ORIGIN,client=('127.0.0.1',40000)) as client:
            h={'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'}
            response=client.post('/__local_review__/independent-pending',json={'invitation':code},headers=h);self.assertEqual(response.status_code,200,response.text)
            p=response.json();self.access.authenticate_from_console(p['nonce']);h['X-STAIR-Independent-Binding']=p['binding']
            data=client.get('/api/independent-review/current',headers=h);self.assertEqual(data.status_code,200,data.text);self.assertTrue(data.json()['reviewer']['synthetic'])
            self.assertEqual(client.post('/api/review/ingest',headers=h,json={}).status_code,403)

class NativeCredentialTests(unittest.TestCase):
    def test_cancel_or_failed_password_clears_native_buffers(self):
        from backend.review.windows_independent import authenticate_independent
        for status,logon in ((1223,False),(0,False),(0,True)):
            with self.subTest(status=status,logon=logon):
                kernel=Mock();security=Mock();ui=Mock();kernel.GetConsoleWindow.return_value=None;buffers=[]
                def prompt(info,target,reserved,error,user,length,password,plen,save,flags):
                    user.value='SYNTHETIC-ACCOUNT';password.value='SYNTHETIC-SECRET';buffers.extend((user,password));self.assertTrue(flags&2);return status
                def login(user,domain,password,kind,provider,token):
                    self.assertEqual(kind,2);self.assertFalse(isinstance(password,str))
                    if logon:ctypes.cast(token,ctypes.POINTER(wintypes.HANDLE))[0]=wintypes.HANDLE(1)
                    return logon
                ui.CredUIPromptForCredentialsW.side_effect=prompt;security.LogonUserW.side_effect=login
                with patch('ctypes.WinDLL',side_effect=[kernel,security,ui]),patch('backend.review.windows_independent.token_actor',return_value='SYNTHETIC-AUTHENTICATED'):
                    if status or not logon:
                        with self.assertRaises(RuntimeError):authenticate_independent()
                    else:self.assertEqual(authenticate_independent(),'SYNTHETIC-AUTHENTICATED')
                self.assertTrue(all(not b.value for b in buffers))
