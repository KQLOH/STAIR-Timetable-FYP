"""Protected genuine HTTP/service contracts; independent invented fixtures only."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.calendar.models import CalendarError,CalendarInput
from backend.calendar.sandbox import LocalSandbox
from backend.calendar.workflow import TestOnlyCalendarBridge
from backend.review.auth import utc_now
from s3c_fixtures import Scenario


class S3cWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='m6-test-s3c-',dir=Path(__file__).resolve().parents[1]/'data/temp')
        self.addCleanup(self.temp.cleanup);self.s=Scenario(Path(self.temp.name)/'calendar');self.addCleanup(self.s.client.close)
    def approval(self):return self.s.approve(self.s.preview())

    def test_default_deny_and_legacy_endpoints_unchanged(self):
        self.s.app.state.s3c_calendar_bridge=None
        for method,path in [('GET','/session'),('GET','/state'),('GET','/history'),('POST','/preview'),('POST','/approve'),('POST','/execute'),('POST','/reconcile'),('POST','/revoke-approval'),('POST','/session/end')]:
            self.assertEqual(self.s.call(method,path,{}).status_code,403)
        for path in ('approve','execute','sync','delete','reconcile'):
            self.assertEqual(self.s.client.post('/api/calendar/v1/'+path,json={}).status_code,404)
        self.assertEqual(self.s.client.post('/api/calendar/v1/test-only/grants',json={}).status_code,404)
        self.assertEqual(self.s.client.get('/api/review/queue').status_code,403)

    def test_session_scope_permissions_and_no_secret(self):
        r=self.s.call('GET','/session');self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['mode'],'TEST_ONLY');self.assertEqual(r.json()['target_sandbox_id'],self.s.target)
        self.assertEqual(len(r.json()['fixtures']),3);self.assertNotIn(self.s.token,r.text);self.assertEqual(r.headers['cache-control'],'no-store')

    def test_no_token_forged_operator_and_university_token_denied(self):
        for headers in ({},{'Authorization':'Bearer SYNTHETIC-NOT-A-GRANT'},{'Authorization':'Bearer SYNTHETIC-TAR-SESSION'}):
            self.assertEqual(self.s.call('GET','/session',headers=headers).status_code,403)
        with self.assertRaises(CalendarError):self.s.bridge.session(replace(self.s.actor,actor_id='FORGED'))

    def test_source_and_target_scope_denied(self):
        actor,token=self.s.auth.issue('SYNTHETIC-OTHER',{'read'},source_bindings={('synthetic:other','f'*64)},mode='APPLICATION_OPERATOR')
        self.s.access.enrol(actor,self.s.target,{'read'})
        self.assertEqual(self.s.call('GET','/session',headers={'Authorization':'Bearer '+token}).status_code,403)
        self.s.access._scopes.clear();self.assertEqual(self.s.call('GET','/session').status_code,403)

    def test_nonlocal_and_wrong_origin_denied(self):
        with TestClient(self.s.app,client=('203.0.113.1',45000)) as client:
            self.assertEqual(client.get('/api/calendar/v1/test-only/session',headers=self.s.headers).status_code,403)
        self.assertEqual(self.s.call('GET','/session',headers=self.s.headers|{'Origin':'https://unapproved.invalid'}).status_code,403)

    def test_review_grant_cannot_be_calendar_grant(self):
        actor,token=self.s.auth.issue('SYNTHETIC-REVIEWER',{'read','correct'},mode='TEST_ONLY',source_bindings=self.s.bindings)
        self.s.access.enrol(actor,self.s.target,{'read','approve'})
        self.assertEqual(self.s.call('GET','/session',headers={'Authorization':'Bearer '+token}).status_code,403)

    def test_valid_test_only_grant_for_other_source_denied(self):
        from backend.review.auth import TrustedHostAuthority
        from backend.calendar.access import CalendarAccess
        scope={('synthetic:unrelated','f'*64)};authority=TrustedHostAuthority(test_only_source_bindings=scope)
        actor,token=authority.issue('SYNTHETIC-OTHER-CAPTURE',{'read'},mode='TEST_ONLY',source_bindings=scope)
        access=CalendarAccess(authority);access.enrol(actor,self.s.target,{'read','preview','execute'})
        self.s.store.access=access
        self.assertEqual(self.s.call('GET','/session',headers={'Authorization':'Bearer '+token}).status_code,403)

    def test_other_enrolled_operator_cannot_execute_owned_approval(self):
        approval=self.approval()
        actor,token=self.s.auth.issue('SYNTHETIC-OTHER-OWNER',{'read'},mode='TEST_ONLY',source_bindings=self.s.bindings)
        self.s.access.enrol(actor,self.s.target,{'read','execute','reconcile'})
        headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.s.call('POST','/execute',{'approval_id':approval['approval_id']},headers=headers).status_code,403)
        self.assertEqual(self.s.call('GET','/history',headers=headers).json()['approvals'],[])

    def test_grant_expiry_revocation_and_self_end(self):
        self.s.auth.clock=lambda:utc_now()+timedelta(minutes=61)
        self.assertEqual(self.s.call('GET','/session').status_code,403);self.s.auth.clock=utc_now
        self.assertEqual(self.s.call('POST','/session/end',{}).status_code,200)
        self.assertEqual(self.s.call('GET','/session').status_code,403)

    def test_preview_mapping_exact_diff_and_no_execution(self):
        plan=self.s.preview();self.assertTrue(all(o['action']=='CREATE' for o in plan['operations']))
        self.assertTrue(all(e['status']=='TEST_ELIGIBLE' and e['actual_write_eligibility']=='BLOCKED' for e in plan['eligibility']))
        self.assertEqual(plan['operations'][0]['after']['occurrences'][0]['start'][:4],'2033')
        self.assertEqual(self.s.call('GET','/state').json()['entries'],[])
        self.assertTrue(all(e.human_verification.status=='NOT_VERIFIED' and e.calendar.approval_status=='NOT_APPROVED' for e in self.s.base.packet.events))

    def test_exact_owner_confirmation_required(self):
        plan=self.s.preview();body=self.s.approval_body(plan)
        for field,value in [('snapshot_digest','f'*64),('input_digest','f'*64),('target_sandbox_id','test:other'),('operation_keys',list(reversed(body['operation_keys'])))]:
            response=self.s.call('POST','/approve',body|{field:value});self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.s.call('POST','/approve',body|{'owner_confirmation':False}).status_code,422)
        self.assertEqual(self.s.call('POST','/approve',body|{'actor_id':'forged'}).status_code,422)

    def test_create_replay_noop_update_semantics(self):
        approval=self.approval();first=self.s.execute(approval);self.assertEqual(first.status_code,200)
        self.assertTrue(all(o['status']=='CONFIRMED' for o in first.json()['outcomes']))
        replay=self.s.execute(approval);self.assertTrue(all(o['duplicate_submission'] for o in replay.json()['outcomes']))
        self.assertEqual(len(self.s.call('GET','/state').json()['entries']),2)
        noop=self.s.preview();self.assertTrue(all(o['action']=='NO_OP' for o in noop['operations']))
        self.assertEqual(self.s.execute(self.s.approve(noop)).status_code,200)
        update=self.s.preview('update');self.assertTrue(all(o['action']=='UPDATE' for o in update['operations']))
        self.assertTrue(any('location' in o['changed_fields'] for o in update['operations']))
        self.assertEqual(self.s.execute(self.s.approve(update)).status_code,200)
        entries=self.s.call('GET','/state').json()['entries'];self.assertEqual(len(entries),2)
        self.assertEqual(next(e['payload']['location'] for e in entries if e['payload']['course_code']=='SYN101'),'SYNTHETIC ROOM C')

    def test_changed_source_invalidates_approved_intent(self):
        approval=self.approval();self.s.preview('update')
        self.assertEqual(self.s.execute(approval).status_code,409)
        self.assertEqual(self.s.call('GET','/state').json()['entries'],[])

    def test_replay_only_cannot_initiate_an_unsubmitted_set(self):
        approval=self.approval()
        r=self.s.call('POST','/execute',{'approval_id':approval['approval_id'],'replay_only':True})
        self.assertEqual(r.json()['detail'],'NO_RECORDED_ATTEMPT');self.assertEqual(self.s.call('GET','/state').json()['entries'],[])
        self.assertEqual(self.s.execute(approval).status_code,200)
        r=self.s.call('POST','/execute',{'approval_id':approval['approval_id'],'replay_only':True})
        self.assertTrue(all(o['duplicate_submission'] for o in r.json()['outcomes']))

    def test_approval_expiry_and_durable_revocation(self):
        approval=self.approval();self.s.store.clock=lambda:utc_now()+timedelta(minutes=11)
        self.assertEqual(self.s.execute(approval).json()['detail'],'APPROVAL_EXPIRED');self.s.store.clock=utc_now
        r=self.s.call('POST','/revoke-approval',{'approval_id':approval['approval_id']});self.assertTrue(r.json()['revoked'])
        reopened=LocalSandbox(self.s.directory,self.s.target,self.s.access,test_only=True);self.s.bridge.sandbox=reopened
        self.assertEqual(self.s.execute(approval).json()['detail'],'APPROVAL_REVOKED')

    def test_new_grant_same_actor_cannot_reuse_old_approval(self):
        approval=self.approval();actor,token=self.s.auth.issue(self.s.actor.actor_id,{'read'},mode='TEST_ONLY',source_bindings=self.s.bindings)
        self.s.access.enrol(actor,self.s.target,{'read','execute','reconcile'})
        response=self.s.call('POST','/execute',{'approval_id':approval['approval_id']},headers={'Authorization':'Bearer '+token})
        self.assertEqual(response.json()['detail'],'APPROVAL_GRANT_MISMATCH')

    def test_legacy_unbound_approval_cannot_execute_through_s3c(self):
        approval=self.approval()
        with self.s.store.journal.locked():
            state,_=self.s.store.journal.load();state.pop('approval_grants')
            self.s.store.journal.append(state,'SYNTHETIC_LEGACY_APPROVAL_FIXTURE',self.s.actor.actor_id)
        self.assertEqual(self.s.execute(approval).json()['detail'],'APPROVAL_GRANT_MISMATCH')
        self.assertEqual(len(self.s.call('GET','/history').json()['approvals']),1)
        self.assertEqual(self.s.call('GET','/state').json()['entries'],[])

    def test_timeout_before_requires_fresh_preview_after_reconciliation(self):
        approval=self.approval();response=self.s.execute(approval,'TIMEOUT_BEFORE')
        self.assertEqual(response.json()['outcomes'][0]['status'],'OUTCOME_UNKNOWN')
        self.assertEqual(self.s.execute(approval).json()['outcomes'][0]['status'],'OUTCOME_UNKNOWN')
        reconciled=self.s.call('POST','/reconcile',{'approval_id':approval['approval_id']}).json()
        self.assertTrue(any(o['status']=='PREVIEW_REQUIRED' for o in reconciled['outcomes']))
        self.assertEqual(self.s.execute(self.s.approve(self.s.preview())).status_code,200)
        self.assertEqual(len(self.s.call('GET','/state').json()['entries']),2)

    def test_timeout_after_observed_state_is_confirmed_without_duplicate(self):
        approval=self.approval();self.s.execute(approval,'TIMEOUT_AFTER')
        response=self.s.call('POST','/reconcile',{'approval_id':approval['approval_id']})
        self.assertTrue(all(o['status']=='CONFIRMED' for o in response.json()['outcomes']))
        self.assertEqual(len(self.s.call('GET','/state').json()['entries']),2)

    def test_timeout_mismatch_stays_unresolved(self):
        approval=self.approval();self.s.execute(approval,'TIMEOUT_AFTER_MISMATCH')
        response=self.s.call('POST','/reconcile',{'approval_id':approval['approval_id']})
        self.assertTrue(any(o['status']=='RECONCILIATION_REQUIRED' for o in response.json()['outcomes']))

    def test_partial_failure_preserves_confirmed_work(self):
        approval=self.approval();r=self.s.execute(approval,'PARTIAL_FAILURE').json()
        self.assertEqual({o['status'] for o in r['outcomes']},{'CONFIRMED','FAILED_CONFIRMED'})
        self.assertEqual(len(self.s.call('GET','/state').json()['entries']),1)
        self.s.call('POST','/reconcile',{'approval_id':approval['approval_id']})
        self.assertEqual(self.s.execute(self.s.approve(self.s.preview())).status_code,200)
        self.assertEqual(len(self.s.call('GET','/state').json()['entries']),2)

    def test_interrupted_submission_recovery(self):
        approval=self.approval();r=self.s.execute(approval,'CRASH_AFTER_SUBMIT')
        self.assertEqual(r.status_code,409);self.assertEqual(r.json()['detail'],'EXECUTION_INTERRUPTED_RECONCILE_REQUIRED')
        reopened=LocalSandbox(self.s.directory,self.s.target,self.s.access,test_only=True);reopened.fixtures=self.s.factory;self.s.bridge.sandbox=reopened
        response=self.s.call('POST','/reconcile',{'approval_id':approval['approval_id']})
        self.assertEqual(response.json()['outcomes'][0]['status'],'PREVIEW_REQUIRED')

    def test_journal_history_persists_after_reopen(self):
        approval=self.approval();self.s.execute(approval)
        history=self.s.call('GET','/history').json()
        reopened=LocalSandbox(self.s.directory,self.s.target,self.s.access,test_only=True);self.s.bridge.sandbox=reopened
        self.assertEqual(self.s.call('GET','/history').json(),history)
        self.assertEqual(len(self.s.call('GET','/state').json()['entries']),2)
        self.assertTrue(any(r['operation']=='APPROVAL' for r in history['journal']));self.assertGreater(history['staging_file_count'],0)

    def test_normal_ocr_and_client_authority_injection_rejected(self):
        body={'fixture_id':'base','bundle':self.s.base.model_dump(mode='json'),'approved':True}
        self.assertEqual(self.s.call('POST','/preview',body).status_code,422)
        public=self.s.client.post('/api/calendar/v1/preview',json={'bundle':self.s.base.model_dump(mode='json')})
        self.assertFalse(public.json()['write_authorized']);self.assertTrue(all(o['action']=='BLOCKED' for o in public.json()['preview']['operations']))
        from test_m5_policy import fixture
        _,run=fixture();g=run.graph()
        from backend.models.event import CanonicalTimetable
        packet=CanonicalTimetable(source=g.source,evidence=g.evidence,events=run.events())
        bundle=CalendarInput(packet=packet)
        with self.assertRaises(ValueError):TestOnlyCalendarBridge(self.s.store,{'forged':('Not registered',bundle)})

    def test_read_only_permission_cannot_preview_or_approve(self):
        self.s.access.enrol(self.s.actor,self.s.target,{'read'})
        self.assertEqual(self.s.call('POST','/preview',{'fixture_id':'base'}).status_code,403)

    def test_failure_simulation_disabled_unless_host_explicit(self):
        approval=self.approval();self.s.bridge.allow_failure_simulation=False
        self.assertEqual(self.s.execute(approval,'TIMEOUT_AFTER').status_code,403)
        self.assertEqual(self.s.execute(approval).status_code,200)

    def test_missing_timezone_blocked_not_invented(self):
        plan=self.s.preview('blocked');self.assertEqual(next(e for e in plan['eligibility'] if e['candidate_id']==self.s.blocked.packet.events[0].candidate_id)['status'],'BLOCKED')

    def test_external_writes_impossible_and_stale_snapshot_refused(self):
        plan=self.s.preview();approval=self.s.approve(plan);self.s.execute(approval)
        self.assertEqual(self.s.call('POST','/approve',self.s.approval_body(plan)).status_code,409)
        self.assertFalse(self.s.call('GET','/history').json()['external_calendar'])
        for path in ('google','oauth','sync','delete'):
            self.assertEqual(self.s.call('POST','/'+path,{}).status_code,404)
