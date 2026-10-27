"""Synthetic isolated sandbox tests. No real timetables, gold labels or calendar accounts."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from pydantic import ValidationError
from backend.calendar.access import CalendarAccess
from backend.calendar.eligibility import assess
from backend.calendar.mapping import map_event
from backend.calendar.models import CalendarError,CalendarInput,Entry,Snapshot
from backend.calendar.planning import build_preview
from backend.calendar.sandbox import LocalSandbox
from backend.calendar.testing import SyntheticFixtureAuthority
from backend.review.auth import TrustedHostAuthority,utc_now
from backend.reconstruction.models import digest


class CalendarTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data/temp';root.mkdir(parents=True,exist_ok=True)
        self.temp=TemporaryDirectory(prefix='m6-test-',dir=root)
        self.addCleanup(self.temp.cleanup)
        self.authority=TrustedHostAuthority()
        self.actor,self.token=self.authority.issue('synthetic-m6-owner',{'read'})
        self.access=CalendarAccess(self.authority)
        self.id='test:synthetic-local-calendar'
        self.access.enrol(self.actor,self.id,{'read','preview','approve','execute','reconcile'})
        self.directory=Path(self.temp.name)/'calendar'
        self.store=LocalSandbox(self.directory,self.id,self.access,test_only=True)
        self.fixtures=SyntheticFixtureAuthority(self.store)
        self.bundle=self.fixtures.make()

    def write(self,bundle=None,failures=None):
        plan=self.store.preview(bundle or self.bundle,self.actor)
        approval=self.store.approve(plan.preview_digest,self.actor)
        outcomes=self.store.execute(approval.approval_id,self.actor,failures=failures)
        return plan,approval,outcomes

    def test_deterministic_mapping(self):
        event=self.bundle.packet.events[0]
        a,b=map_event(self.bundle,event),map_event(self.bundle,event)
        self.assertEqual(a,b)
        self.assertEqual(a.title,'SYN101 · Lecture')
        self.assertEqual(a.occurrences[0].start.isoformat(),'2026-01-05T09:00:00+00:00')
        self.assertTrue(a.mapping_evidence)

    def test_default_application_eligibility_blocked_even_for_fixture(self):
        e=assess(self.bundle,self.bundle.packet.events[0])
        self.assertEqual(e.status,'BLOCKED')
        self.assertEqual(e.actual_write_eligibility,'BLOCKED')
        self.assertIn('M5_ACCEPT_DISABLED',e.reason_codes)

    def test_fixture_grant_not_applied_to_modified_input(self):
        changed=self.bundle.model_copy(update={'reconstruction_status':'SEARCH_INCOMPLETE'})
        plan=self.store.preview(changed,self.actor)
        self.assertTrue(all(op.action=='BLOCKED' for op in plan.operations))

    def test_test_authority_cannot_attach_to_development_sandbox(self):
        prod=Path(__file__).resolve().parents[1]/'data/calendar_sandbox'/('synthetic-test-'+uuid4().hex)
        # Do not create persistent production storage in this test: verify factory rejects an explicit non-test boundary.
        class Disabled: snapshot_scope='DEVELOPMENT_BLOCKED'
        with self.assertRaises(ValueError): SyntheticFixtureAuthority(Disabled())

    def test_missing_timezone(self):
        b=self.fixtures.make(changes={'source_timezone':None})
        self.assertIn('TIMEZONE_UNRESOLVED',self.store.preview(b,self.actor).eligibility[0].reason_codes)

    def test_missing_timezone_authority(self):
        b=self.bundle.model_copy(update={'context':self.bundle.context.model_copy(update={'timezone_evidence_ids':()})})
        with self.assertRaises(CalendarError): map_event(b,b.packet.events[0])

    def test_invalid_date_contract(self):
        body=self.bundle.model_dump(mode='json');body['packet']['events'][0]['revision']['content']['date']='2026-02-30'
        with self.assertRaises(ValidationError): CalendarInput.model_validate(body)

    def test_missing_date_never_uses_semester_default(self):
        b=self.fixtures.make(changes={'date':None})
        self.assertIn('DATE_UNRESOLVED',self.store.preview(b,self.actor).eligibility[0].reason_codes)

    def test_date_weekday_conflict(self):
        b=self.fixtures.make(changes={'date':'2026-01-06'})
        self.assertIn('DATE_WEEKDAY_CONFLICT',self.store.preview(b,self.actor).eligibility[0].reason_codes)

    def test_unknown_location(self):
        b=self.fixtures.make(changes={'location':'TBA'})
        self.assertFalse(self.store.preview(b,self.actor).eligibility[0].sandbox_write_permitted)
        self.assertIsNone(b.packet.events[0].revision.content.location)

    def weekly(self,**changes):
        data={'recurrence':{'frequency':'WEEKLY','interval':1,'by_day':[1],'term_start':'2026-01-05','term_end':'2026-01-25','source_rule':'weekly'},
              'teaching_weeks':[1,2,3]}
        data.update(changes)
        return self.fixtures.make(changes=data,term=('2026-01-05','2026-01-25'),weeks=((1,'2026-01-05','2026-01-11'),(2,'2026-01-12','2026-01-18'),(3,'2026-01-19','2026-01-25')))

    def test_explicit_recurrence_and_excluded_weeks(self):
        b=self.weekly(excluded_weeks=[2])
        mapped=map_event(b,b.packet.events[0])
        self.assertEqual([o.source_local_date.isoformat() for o in mapped.occurrences],['2026-01-05','2026-01-19'])
        self.assertEqual([d.isoformat() for d in mapped.excluded_dates],['2026-01-12'])

    def test_explicit_exception_and_replacement(self):
        b=self.weekly(recurrence={'frequency':'WEEKLY','interval':1,'by_day':[1],'term_start':'2026-01-05','term_end':'2026-01-25',
            'source_rule':'weekly','excluded_dates':['2026-01-12'],'replacement_dates':['2026-01-14']})
        mapped=map_event(b,b.packet.events[0])
        self.assertEqual([o.source_local_date.isoformat() for o in mapped.occurrences],['2026-01-05','2026-01-14','2026-01-19'])

    def test_unverified_week_mapping_blocks(self):
        b=self.weekly();b=b.model_copy(update={'context':b.context.model_copy(update={'weeks':()})})
        with self.assertRaises(CalendarError) as exc: map_event(b,b.packet.events[0])
        self.assertEqual(exc.exception.code,'WEEK_MAPPING_UNRESOLVED')

    def test_no_holiday_inference(self):
        b=self.weekly()
        self.assertEqual(len(map_event(b,b.packet.events[0]).occurrences),3)

    def test_different_teaching_weeks_are_distinct_occurrences(self):
        a,b=self.weekly(teaching_weeks=[1]),self.weekly(teaching_weeks=[3])
        self.assertNotEqual(map_event(a,a.packet.events[0]).occurrences,map_event(b,b.packet.events[0]).occurrences)

    def test_no_implicit_weekly_recurrence(self):
        b=self.fixtures.make(changes={'teaching_weeks':[1,2]})
        with self.assertRaises(CalendarError) as exc: map_event(b,b.packet.events[0])
        self.assertEqual(exc.exception.code,'WEEKS_WITHOUT_SUPPORTED_RECURRENCE')

    def test_invalid_temporal_order(self):
        b=self.fixtures.make(changes={'start_time':'11:00','end_time':'09:00'})
        self.assertEqual(self.store.preview(b,self.actor).operations[0].action,'BLOCKED')

    def test_explicit_cross_midnight(self):
        b=self.fixtures.make(changes={'start_time':'23:00','end_time':'01:00','end_day_offset':1})
        m=map_event(b,b.packet.events[0])
        self.assertEqual(m.occurrences[0].end.date().isoformat(),'2026-01-06')

    def test_end_of_day(self):
        b=self.fixtures.make(changes={'start_time':'23:00','end_time':'24:00'})
        self.assertEqual(map_event(b,b.packet.events[0]).occurrences[0].end.date().isoformat(),'2026-01-06')

    def test_source_identity_not_title_or_time(self):
        b=self.fixtures.make(previous=self.bundle,changes={'start_time':'09:15','location':'SYNTHETIC OTHER ROOM'})
        self.assertEqual(b.packet.events[0].identity.stable_key,self.bundle.packet.events[0].identity.stable_key)

    def test_preview_does_not_write_calendar(self):
        a=self.store.preview(self.bundle,self.actor);b=self.store.preview(self.bundle,self.actor)
        self.assertEqual(a.preview_digest,b.preview_digest)
        self.assertEqual(a.snapshot_digest,b.snapshot_digest)
        self.assertFalse(self.store.snapshot(self.actor).entries)

    def test_synthetic_end_to_end_no_duplicate_reconciliation(self):
        plan,approval,result=self.write()
        self.assertEqual(plan.operations[0].action,'CREATE')
        self.assertEqual(result[0].status,'CONFIRMED')
        repeat=self.store.execute(approval.approval_id,self.actor)
        self.assertTrue(repeat[0].duplicate_submission)
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)
        self.assertEqual(self.store.reconcile(approval.approval_id,self.actor)[0].status,'CONFIRMED')
        self.assertFalse(result[0].external_calendar)
        self.assertEqual(result[0].confirmation_kind,'LOCAL_SANDBOX_OBSERVATION')

    def test_update_and_exact_field_diff(self):
        self.write()
        b=self.fixtures.make(previous=self.bundle,changes={'location':'SYNTHETIC ROOM 2'})
        plan,approval,result=self.write(b)
        self.assertEqual(plan.operations[0].action,'UPDATE')
        self.assertIn('location',plan.operations[0].changed_fields)
        self.assertEqual(result[0].status,'CONFIRMED')
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)

    def test_no_op(self):
        self.write();plan,approval,result=self.write()
        self.assertEqual(plan.operations[0].action,'NO_OP')
        self.assertEqual(result[0].status,'CONFIRMED')
        self.assertEqual(self.store.snapshot(self.actor).calendar_version,1)

    def test_different_tutorial_groups_and_multi_sessions_not_merged(self):
        base=self.bundle.packet.events[0].revision.content.model_dump(mode='json')
        b=self.fixtures.make(events=[{**base,'session_id':'synthetic-session:T1','session_type':'Tutorial','section':'T1'},
                                    {**base,'session_id':'synthetic-session:T2','session_type':'Tutorial','section':'T2'}])
        plan,approval,result=self.write(b)
        self.assertEqual(len(plan.operations),2)
        self.assertEqual(len(self.store.snapshot(self.actor).entries),2)
        self.assertEqual({e.payload.section for e in self.store.snapshot(self.actor).entries},{'T1','T2'})

    def test_changed_source_without_lineage_conflicts(self):
        self.write()
        b=self.fixtures.make(previous=self.bundle,capture_id='synthetic:capture:new')
        plan=self.store.preview(b,self.actor)
        self.assertEqual(plan.operations[0].action,'CONFLICT')
        self.assertIn('AMBIGUOUS_PREDECESSOR',plan.operations[0].reason_codes)

    def test_authorized_predecessor_allows_update(self):
        self.write()
        b=self.fixtures.make(previous=self.bundle,capture_id='synthetic:capture:new',lineage=True)
        plan,_,out=self.write(b)
        self.assertEqual(plan.operations[0].action,'UPDATE')
        self.assertEqual(out[0].status,'CONFIRMED')

    def test_incomplete_observation_never_deletes(self):
        self.write()
        plan=self.store.preview(self.fixtures.empty_observation(self.bundle),self.actor)
        self.assertEqual(plan.operations[0].action,'MISSING_OBSERVATION')
        self.assertIn('NO_AUTOMATIC_DELETE',plan.operations[0].reason_codes)
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)

    def test_missing_observation_invalidates_old_approval(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        self.store.preview(self.fixtures.empty_observation(self.bundle),self.actor)
        with self.assertRaises(CalendarError): self.store.execute(a.approval_id,self.actor)

    def test_stale_source_revision_approval(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        b=self.fixtures.make(previous=self.bundle,changes={'location':'SYNTHETIC NEW ROOM'})
        self.store.preview(b,self.actor)
        with self.assertRaises(CalendarError) as exc: self.store.execute(a.approval_id,self.actor)
        self.assertEqual(exc.exception.code,'STALE_SOURCE_REVISION')

    def test_stale_preview_snapshot(self):
        p=self.store.preview(self.bundle,self.actor)
        other=self.fixtures.make(changes={'course_code':'SYN202'},events=[{**self.bundle.packet.events[0].revision.content.model_dump(mode='json'),
            'session_id':'synthetic-session:2','course_code':'SYN202'}])
        self.write(other)
        with self.assertRaises(CalendarError): self.store.approve(p.preview_digest,self.actor)

    def test_approval_expiry(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        self.store.clock=lambda:utc_now()+timedelta(minutes=11)
        with self.assertRaises(CalendarError) as exc: self.store.execute(a.approval_id,self.actor)
        self.assertEqual(exc.exception.code,'APPROVAL_EXPIRED')

    def test_rejected_approval_cannot_write(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor,decision='REJECT')
        with self.assertRaises(CalendarError): self.store.execute(a.approval_id,self.actor)

    def test_forged_actor_rejected(self):
        with self.assertRaises(CalendarError): self.store.preview(self.bundle,replace(self.actor,actor_id='forged'))

    def test_m5_grant_alone_is_not_calendar_permission(self):
        other,_=self.authority.issue('synthetic-m5-reviewer',{'read'})
        with self.assertRaises(CalendarError): self.store.preview(self.bundle,other)

    def test_different_actor_cannot_reuse_approval(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        other,_=self.authority.issue('synthetic-other-owner',{'read'})
        self.access.enrol(other,self.id,{'execute'})
        with self.assertRaises(CalendarError): self.store.execute(a.approval_id,other)

    def test_revoked_identity_rejected(self):
        self.authority.revoke(self.actor)
        with self.assertRaises(CalendarError): self.store.snapshot(self.actor)

    def test_target_scope_separation(self):
        other=LocalSandbox(Path(self.temp.name)/'other','test:other',self.access,test_only=True)
        with self.assertRaises(CalendarError): other.snapshot(self.actor)

    def test_timeout_after_apply_reconcile_then_no_duplicate(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        result=self.store.execute(a.approval_id,self.actor,failures={p.operations[0].operation_key:'TIMEOUT_AFTER'})
        self.assertEqual(result[0].status,'OUTCOME_UNKNOWN')
        self.assertEqual(self.store.execute(a.approval_id,self.actor)[0].status,'OUTCOME_UNKNOWN')
        self.assertEqual(self.store.reconcile(a.approval_id,self.actor)[0].status,'CONFIRMED')
        self.assertEqual(self.store.execute(a.approval_id,self.actor)[0].status,'CONFIRMED')
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)

    def test_timeout_before_requires_reconciliation_and_fresh_approval(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        self.store.execute(a.approval_id,self.actor,failures={p.operations[0].operation_key:'TIMEOUT_BEFORE'})
        with self.assertRaises(CalendarError): self.store.approve(p.preview_digest,self.actor)
        self.assertEqual(self.store.reconcile(a.approval_id,self.actor)[0].status,'PREVIEW_REQUIRED')
        self.assertEqual(self.store.execute(a.approval_id,self.actor)[0].status,'PREVIEW_REQUIRED')
        _,_,out=self.write();self.assertEqual(out[0].status,'CONFIRMED')

    def test_partial_failure_no_rollback_or_blind_resend(self):
        base=self.bundle.packet.events[0].revision.content.model_dump(mode='json')
        b=self.fixtures.make(events=[{**base,'session_id':'synthetic-session:1'},{**base,'session_id':'synthetic-session:2'}])
        p=self.store.preview(b,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        out=self.store.execute(a.approval_id,self.actor,failures={p.operations[1].operation_key:'FAIL_BEFORE'})
        self.assertEqual({o.status for o in out},{'CONFIRMED','FAILED_CONFIRMED'})
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)
        self.store.execute(a.approval_id,self.actor)
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)
        _,_,out=self.write(b);self.assertTrue(all(o.status=='CONFIRMED' for o in out))
        self.assertEqual(len(self.store.snapshot(self.actor).entries),2)

    def test_process_reopen_recovers_pending_operation(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        with self.assertRaises(RuntimeError):
            self.store.execute(a.approval_id,self.actor,failures={p.operations[0].operation_key:'CRASH_AFTER_SUBMIT'})
        reopened=LocalSandbox(self.directory,self.id,self.access,test_only=True)
        self.assertEqual(reopened.execute(a.approval_id,self.actor)[0].status,'OUTCOME_UNKNOWN')
        self.assertEqual(reopened.reconcile(a.approval_id,self.actor)[0].status,'PREVIEW_REQUIRED')
        self.assertFalse(reopened.snapshot(self.actor).entries)

    def test_reconciliation_mismatch_blocks_blind_retry(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        self.store.execute(a.approval_id,self.actor,failures={p.operations[0].operation_key:'TIMEOUT_AFTER_MISMATCH'})
        self.assertEqual(self.store.reconcile(a.approval_id,self.actor)[0].status,'RECONCILIATION_REQUIRED')
        self.assertEqual(self.store.execute(a.approval_id,self.actor)[0].status,'RECONCILIATION_REQUIRED')

    def test_journal_hash_chain_and_reopen_state(self):
        self.write();d=self.store.diagnostics(self.actor)
        previous=None
        for record in d['journal']:
            self.assertEqual(record['previous_hash'],previous);previous=record['record_hash']
        reopened=LocalSandbox(self.directory,self.id,self.access,test_only=True)
        self.assertEqual(reopened.snapshot(self.actor),self.store.snapshot(self.actor))

    def test_corrupt_record_fails_closed(self):
        path=next(self.store.journal.records.glob('*.json'));path.write_text('{broken',encoding='utf-8')
        with self.assertRaises(CalendarError) as exc: self.store.snapshot(self.actor)
        self.assertEqual(exc.exception.code,'SANDBOX_JOURNAL_CORRUPT')

    def test_orphan_staging_not_treated_as_committed(self):
        (self.directory/'orphan.staging').write_text('{interrupted',encoding='utf-8')
        self.assertFalse(self.store.snapshot(self.actor).entries)
        self.assertGreater(self.store.diagnostics(self.actor)['staging_file_count'],0)

    def test_filesystem_lock_prevents_concurrent_writer(self):
        with self.store.journal.locked():
            with self.assertRaises(CalendarError): self.store.snapshot(self.actor)

    def test_duplicate_identity_preview_conflict(self):
        base=self.bundle.packet.events[0].revision.content.model_dump(mode='json')
        b=self.fixtures.make(events=[{**base,'session_id':'synthetic-session:same'},{**base,'session_id':'synthetic-session:same'}])
        p=self.store.preview(b,self.actor)
        self.assertTrue(all(o.action=='CONFLICT' for o in p.operations))

    def test_duplicate_target_detection(self):
        payload=map_event(self.bundle,self.bundle.packet.events[0])
        snapshot=Snapshot(sandbox_id=self.id,scope='TEST_ONLY',calendar_version=1,entries=(Entry(entry_id='a',payload=payload),Entry(entry_id='b',payload=payload)))
        p=build_preview(self.bundle,snapshot,test_authorized=True)
        self.assertEqual(p.operations[0].action,'CONFLICT')
        self.assertIn('MULTIPLE_UPDATE_TARGETS',p.operations[0].reason_codes)

    def test_unmanaged_target_not_overwritten(self):
        payload=map_event(self.bundle,self.bundle.packet.events[0])
        snap=Snapshot(sandbox_id=self.id,scope='TEST_ONLY',calendar_version=1,entries=(Entry(entry_id='unmanaged',managed=False,payload=payload),))
        self.assertIn('UNMANAGED_TARGET',build_preview(self.bundle,snap,test_authorized=True).operations[0].reason_codes)

    def test_input_immutability_and_no_gold_or_calendar_promotion(self):
        original=self.bundle.model_dump_json();self.write()
        self.assertEqual(self.bundle.model_dump_json(),original)
        e=self.bundle.packet.events[0]
        self.assertEqual(e.human_verification.status,'NOT_VERIFIED')
        self.assertEqual(e.calendar.approval_status,'NOT_APPROVED')
        self.assertEqual(e.calendar.sync_status,'NOT_SUBMITTED')
        self.assertEqual(self.bundle.packet.complete_schedule_verification.status,'NOT_VERIFIED')

    def test_persistence_path_cannot_escape(self):
        with self.assertRaises(CalendarError): LocalSandbox(Path(self.temp.name).parent.parent,'test:escape',self.access,test_only=True)

    def test_source_restore_after_missing_observation_needs_fresh_approval(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        self.store.preview(self.fixtures.empty_observation(self.bundle),self.actor)
        restored=self.store.preview(self.bundle,self.actor)
        self.assertNotEqual(restored.preview_digest,p.preview_digest)
        with self.assertRaises(CalendarError): self.store.execute(a.approval_id,self.actor)

    def test_same_cohort_overlap_produces_conflict(self):
        base=self.bundle.packet.events[0].revision.content.model_dump(mode='json')
        b=self.fixtures.make(events=[{**base,'session_id':'synthetic-session:1','cohort_id':'synthetic-C1'},
                                    {**base,'session_id':'synthetic-session:2','cohort_id':'synthetic-C1'}])
        self.assertTrue(all(o.action=='CONFLICT' for o in self.store.preview(b,self.actor).operations))

    def test_nonexistent_dst_local_time_rejected(self):
        b=self.fixtures.make(changes={'source_timezone':'America/New_York','date':'2026-03-08','day_of_week':7,'start_time':'02:15','end_time':'03:15'})
        with self.assertRaises(CalendarError) as exc:map_event(b,b.packet.events[0])
        self.assertEqual(exc.exception.code,'NONEXISTENT_SOURCE_LOCAL_TIME')

    def test_ambiguous_dst_local_time_rejected(self):
        b=self.fixtures.make(changes={'source_timezone':'America/New_York','date':'2026-11-01','day_of_week':7,'start_time':'01:15','end_time':'01:45'})
        with self.assertRaises(CalendarError) as exc:map_event(b,b.packet.events[0])
        self.assertEqual(exc.exception.code,'AMBIGUOUS_SOURCE_LOCAL_TIME')

    def test_concurrent_approvals_use_compare_and_swap(self):
        p=self.store.preview(self.bundle,self.actor)
        first=self.store.approve(p.preview_digest,self.actor)
        second=self.store.approve(p.preview_digest,self.actor)
        self.store.execute(first.approval_id,self.actor)
        with self.assertRaises(CalendarError) as exc:self.store.execute(second.approval_id,self.actor)
        self.assertEqual(exc.exception.code,'STALE_APPROVAL')
        self.assertEqual(len(self.store.snapshot(self.actor).entries),1)

    def test_unicode_mapping_round_trip_no_false_update(self):
        b=self.fixtures.make(changes={'course_name':'Synthetic Cafe\u0301'})
        self.write(b)
        p,_,result=self.write(b)
        self.assertEqual(p.operations[0].action,'NO_OP')
        self.assertFalse(p.operations[0].changed_fields)
        self.assertEqual(result[0].status,'CONFIRMED')

    def test_old_preview_blocked_after_unlinked_capture(self):
        p=self.store.preview(self.bundle,self.actor);a=self.store.approve(p.preview_digest,self.actor)
        b=self.fixtures.make(previous=self.bundle,capture_id='synthetic:capture:unlinked')
        self.store.preview(b,self.actor)
        with self.assertRaises(CalendarError):self.store.execute(a.approval_id,self.actor)

    def test_preview_tampering_rejected_under_authorized_boundary(self):
        p=self.store.preview(self.bundle,self.actor)
        with self.store.journal.locked():
            state,_=self.store.journal.load()
            state['previews'][p.preview_digest]['operations'][0]['action']='UPDATE'
            self.store.journal.append(state,'SYNTHETIC_TAMPER_TEST',self.actor.actor_id)
        with self.assertRaises(CalendarError) as exc:self.store.approve(p.preview_digest,self.actor)
        self.assertEqual(exc.exception.code,'PREVIEW_DIGEST_MISMATCH')

    def test_cancelled_source_requires_review_not_create_or_delete(self):
        b=self.fixtures.make(changes={'source_class_status':'cancelled'})
        p=self.store.preview(b,self.actor)
        self.assertEqual(p.operations[0].action,'BLOCKED')
        self.assertIn('SOURCE_STATUS_REVIEW_REQUIRED',p.eligibility[0].reason_codes)

    def test_invalid_recurrence_interval_and_count(self):
        for change,code in (({'interval':2},'RECURRENCE_INTERVAL_UNSUPPORTED'),({'count':20},'RECURRENCE_COUNT_UNSUPPORTED_BY_TERM')):
            with self.subTest(change=change):
                b=self.weekly(recurrence={'frequency':'WEEKLY','interval':1,'by_day':[1],'term_start':'2026-01-05','term_end':'2026-01-25','source_rule':'weekly',**change})
                with self.assertRaises(CalendarError) as exc: map_event(b,b.packet.events[0])
                self.assertEqual(exc.exception.code,code)

    def test_blank_course_identity_blocks_mapping(self):
        b=self.fixtures.make(changes={'course_code':'   '})
        self.assertIn('MISSING_COURSE_IDENTITY',self.store.preview(b,self.actor).eligibility[0].reason_codes)


if __name__=='__main__':unittest.main()
