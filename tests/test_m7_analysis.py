"""Isolated SYNTHETIC M7 fixtures. No research data, providers or calendar writes."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.analysis.models import AnalysisInput, Scope, Availability
from backend.analysis.authority import CompletenessAuthority, Verifier
from backend.analysis.service import analyze, minutes
from backend.calendar.mapping import normalize_occurrences
from backend.calendar.testing import SyntheticFixtureAuthority
from backend.models.event import CanonicalTimetable, CompleteScheduleVerification, ExtractionStatus
from backend.reconstruction.models import digest
from backend.main import app


def make(events=None, *, term=None, weeks=()):
    isolated = type('IsolatedSyntheticOnly', (), {'snapshot_scope':'TEST_ONLY'})()
    fixture = SyntheticFixtureAuthority(isolated)
    base = {'course_code':'SYN101', 'session_type':'Lecture', 'cohort_id':'synthetic-cohort', 'date':'2026-01-05',
            'day_of_week':1, 'start_time':'09:00', 'end_time':'10:00', 'source_timezone':'Etc/UTC'}
    bundle = fixture.make(events=[{**base, **event} for event in (events if events is not None else [{}])],term=term,weeks=weeks)
    return AnalysisInput(event_set_id='synthetic-event-set', profile='m7-development-v1',packet=bundle.packet,context=bundle.context,
        scope=Scope(scope_id='synthetic-scope',timetable_id='synthetic-table',start='2026-01-05',end='2026-01-11',timezone='Etc/UTC',cohort_id='synthetic-cohort'),
        declared_kind='SYNTHETIC_DEMO')


def inventory(request):
    sessions=[]
    for e in request.packet.events:
        occurrences,excluded=normalize_occurrences(request,e)
        sessions.append({'source_session_id':e.identity.stable_source_session_id, 'candidate_id':e.candidate_id,
                         'revision_id':str(e.revision.revision_id),'page':1,'region_id':'synthetic-region',
                         'source_reconciliation_record_id':'SYNTHETIC-SOURCE-ADJUDICATION',
                         'occurrence_digest':digest({'occurrences':[o.model_dump(mode='json') for o in occurrences],
                                                    'excluded_dates':sorted(d.isoformat() for d in excluded)})})
    return {'source_document_id':request.packet.source.source_document_id,'source_sha256':request.packet.source.content_sha256,
            'required_pages':[1], 'checked_pages':[1], 'required_regions':['synthetic-region'],'checked_regions':['synthetic-region'],
            'sessions':sessions,'unresolved':[], 'excluded_scope':[], 'provenance_record_id':'SYNTHETIC-INDEPENDENT-INVENTORY',
            'recurrence_adjudication_record_id':'SYNTHETIC-INDEPENDENT-RECURRENCE',
            'field_reconciliation_record_id':'SYNTHETIC-INDEPENDENT-FIELD-RECONCILIATION'}


def attest(request):
    authority=CompletenessAuthority()
    verifier=authority.install_verifier('synthetic-independent-verifier',independent_of=('synthetic-implementer','synthetic-primary-annotator'),
                                      expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
    token=authority.record(request,inventory(request),verifier)
    return request.model_copy(update={'attestation_id':token}),authority,verifier


def metric(result,kind): return next(f for f in result['facts'] if f['type']==kind)
def value(result,kind): return metric(result,kind)['value']


class FactTests(unittest.TestCase):
    def test_occurrence_cap_fails_closed(self):
        from backend.analysis import service
        with patch.dict(service.SETTINGS, {'occurrence_cap':1}):
            r=analyze(make([{}, {'start_time':'11:00','end_time':'12:00'}]))
        self.assertIsNone(value(r,'observed_scheduled_hours'))
        self.assertIn('ANALYSIS_OCCURRENCE_LIMIT_EXCEEDED',r['completeness']['reason_codes'])
    def test_course_and_type_breakdowns(self):
        r=analyze(make([{}, {'course_code':'SYN202','session_type':'Tutorial','start_time':'11:00','end_time':'13:00'}]))
        self.assertEqual(value(r,'hours_by_course'),{'SYN101':1,'SYN202':2})
        self.assertEqual(value(r,'hours_by_session_type'),{'Lecture':1,'Tutorial':2})
    def test_ambiguous_candidates_not_counted_simultaneously(self):
        q=make([{},{}]);events=q.packet.events
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'events':tuple(e.model_copy(update={'extraction_status':ExtractionStatus.AMBIGUOUS}) for e in events)})})
        self.assertEqual(value(analyze(q),'normalized_session_count'),0)
    def test_missing_type_not_fabricated(self):
        r=analyze(make([{'session_type':None}]));self.assertEqual(metric(r,'hours_by_session_type')['status'],'INSUFFICIENT_EVIDENCE')
    def test_recurrence_exceptions(self):
        q=make([{'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-18',
                             'excluded_dates':['2026-01-12'],'replacement_dates':['2026-01-13']}}],term=('2026-01-05','2026-01-18'))
        q=q.model_copy(update={'scope':q.scope.model_copy(update={'end':q.scope.end+timedelta(days=7)})})
        r=analyze(q);self.assertEqual(value(r,'hours_by_day'),{'2026-01-05':1,'2026-01-13':1})
    def test_single_session(self):
        r=analyze(make());self.assertEqual(value(r,'observed_scheduled_hours'),1)
        self.assertEqual(value(r,'normalized_session_count'),1)
        self.assertEqual(r['completeness']['label'],'OBSERVED EVENTS ONLY')
    def test_deterministic_and_source_linked(self):
        q=make();r=analyze(q);self.assertEqual(r,analyze(q))
        self.assertTrue(all(f['fact_id'] and f['event_refs'] for f in r['facts']))
        self.assertEqual(r['facts'][0]['event_refs'][0]['revision_id'],str(q.packet.events[0].revision.revision_id))
    def test_daily_totals_and_ties(self):
        r=analyze(make([{}, {'date':'2026-01-06','day_of_week':2}]))
        self.assertEqual(value(r,'hours_by_day'),{'2026-01-05':1,'2026-01-06':1})
        self.assertEqual(value(r,'tied_busiest_active_days'),['2026-01-05','2026-01-06'])
    def test_overlap_union_not_sum(self):
        r=analyze(make([{'end_time':'11:00'},{'start_time':'10:00','end_time':'12:00'}]))
        self.assertEqual(value(r,'observed_scheduled_hours'),3)
        self.assertEqual(len(value(r,'confirmed_conflicts')),1)
    def test_half_open_adjacent(self):
        r=analyze(make([{}, {'start_time':'10:00','end_time':'11:00'}]))
        self.assertEqual(value(r,'confirmed_conflicts'),[])
        self.assertEqual(len(value(r,'observed_consecutive_sessions')),1)
    def test_distinct_cohorts_not_conflict(self):
        q=make([{}, {'cohort_id':'other'}]);q=q.model_copy(update={'scope':q.scope.model_copy(update={'cohort_id':None})})
        self.assertEqual(value(analyze(q),'confirmed_conflicts'),[])
    def test_distinct_sections_not_conflict(self):
        r=analyze(make([{'section':'A'},{'section':'B'}]))
        self.assertEqual(value(r,'confirmed_conflicts'),[])
        self.assertIn('SECTION_MEMBERSHIP_UNRESOLVED',r['completeness']['reason_codes'])
    def test_unknown_cohort_not_conflict(self):
        self.assertEqual(value(analyze(make([{'cohort_id':None},{}])),'confirmed_conflicts'),[])
    def test_cohort_filter(self):
        r=analyze(make([{}, {'cohort_id':'other','end_time':'12:00'}]));self.assertEqual(value(r,'observed_scheduled_hours'),1)
    def test_duplicate_source_session(self):
        r=analyze(make([{'session_id':'synthetic-session:same'},{'session_id':'synthetic-session:same'}]))
        self.assertEqual(value(r,'normalized_session_count'),1);self.assertEqual(value(r,'observed_scheduled_hours'),1)
    def test_conflicting_source_revision_not_simultaneous(self):
        r=analyze(make([{'session_id':'synthetic-session:same'},{'session_id':'synthetic-session:same','end_time':'12:00'}]))
        self.assertIsNone(value(r,'observed_scheduled_hours'))
    def test_same_cell_multiple_distinct_times(self):
        r=analyze(make([{}, {'start_time':'11:00','end_time':'12:00'}]));self.assertEqual(value(r,'normalized_session_count'),2)
    def test_missing_location_still_hours(self):
        self.assertEqual(value(analyze(make([{'location':None}])),'observed_scheduled_hours'),1)
    def test_missing_time(self):
        r=analyze(make([{'start_time':None}]));self.assertEqual(metric(r,'observed_scheduled_hours')['status'],'INSUFFICIENT_EVIDENCE')
        self.assertEqual(metric(r,'confirmed_conflicts')['status'],'NOT_ASSESSED')
    def test_missing_timezone(self):
        self.assertIsNone(value(analyze(make([{'source_timezone':None}])),'observed_scheduled_hours'))
    def test_unknown_time(self):
        self.assertIsNone(value(analyze(make([{'start_time':'TBA'}])),'observed_scheduled_hours'))
    def test_no_guessed_recurrence(self):
        r=analyze(make([{'date':None}]));self.assertIsNone(value(r,'observed_scheduled_hours'))
    def test_overnight(self):
        r=analyze(make([{'start_time':'23:00','end_time':'01:00','end_day_offset':1}]))
        self.assertEqual(value(r,'hours_by_day'),{'2026-01-05':1,'2026-01-06':1})
    def test_overnight_without_offset_blocked(self):
        self.assertIsNone(value(analyze(make([{'start_time':'23:00','end_time':'01:00'}])),'observed_scheduled_hours'))
    def test_end_24(self):
        self.assertEqual(value(analyze(make([{'start_time':'23:00','end_time':'24:00'}])),'observed_scheduled_hours'),1)
    def test_different_horizon(self):
        q=make();q=q.model_copy(update={'scope':q.scope.model_copy(update={'start':q.scope.end})})
        self.assertEqual(value(analyze(q),'normalized_session_count'),0)
    def test_weekday_conflict(self):
        self.assertIsNone(value(analyze(make([{'day_of_week':2}])),'observed_scheduled_hours'))
    def test_weekly_and_excluded_weeks(self):
        q=make([{'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-25'},
                 'teaching_weeks':[1,2,3],'excluded_weeks':[2]}],term=('2026-01-05','2026-01-25'),
               weeks=((1,'2026-01-05','2026-01-11'),(2,'2026-01-12','2026-01-18'),(3,'2026-01-19','2026-01-25')))
        q=q.model_copy(update={'scope':q.scope.model_copy(update={'end':q.scope.end+timedelta(days=14)})})
        r=analyze(q);self.assertEqual(value(r,'observed_scheduled_hours'),2);self.assertEqual(value(r,'hours_by_teaching_week'),{'1':1,'3':1})
    def test_weekly_without_term_authority(self):
        q=make([{'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-25'}}])
        self.assertIsNone(value(analyze(q),'observed_scheduled_hours'))
    def test_dst_missing_local_time(self):
        q=make([{'date':'2026-03-08','day_of_week':7,'source_timezone':'America/New_York','start_time':'02:30','end_time':'03:30'}])
        self.assertIsNone(value(analyze(q),'observed_scheduled_hours'))
    def test_dst_ambiguous_local_time(self):
        q=make([{'date':'2026-11-01','day_of_week':7,'source_timezone':'America/New_York','start_time':'01:30','end_time':'02:30'}])
        self.assertTrue(any(i['reason_code']=='AMBIGUOUS_SOURCE_LOCAL_TIME' for i in analyze(q)['diagnostics']))
    def test_elapsed_duration_across_dst(self):
        q=make([{'date':'2026-03-08','day_of_week':7,'source_timezone':'America/New_York','start_time':'01:30','end_time':'03:30'}])
        q=q.model_copy(update={'scope':Scope(scope_id='dst',timetable_id='synthetic-table',start='2026-03-08',end='2026-03-08',timezone='America/New_York',cohort_id='synthetic-cohort')})
        self.assertEqual(value(analyze(q),'observed_scheduled_hours'),1)
    def test_no_implicit_long_block_threshold(self):
        self.assertEqual(metric(analyze(make()),'long_scheduled_blocks')['status'],'NOT_ASSESSED')
    def test_explicit_long_block(self):
        q=make().model_copy(update={'long_block_minutes':60});self.assertEqual(len(value(analyze(q),'long_scheduled_blocks')),1)


class CompletenessTests(unittest.TestCase):
    def test_m5_accept_does_not_verify_schedule(self):
        from backend.models.event import PolicyDecision
        q=make();e=q.packet.events[0]
        e=e.model_copy(update={'policy':PolicyDecision(decision='ACCEPT',revision_id=e.revision.revision_id,policy_version='synthetic-untrusted')})
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'events':(e,)})})
        self.assertFalse(analyze(q)['completeness']['trusted_complete'])
    def test_m6_eligibility_does_not_verify_schedule(self):
        from backend.models.event import CalendarState
        q=make();e=q.packet.events[0]
        e=e.model_copy(update={'calendar':CalendarState(eligibility='ELIGIBLE',eligibility_revision_id=e.revision.revision_id,eligibility_policy_version='synthetic')})
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'events':(e,)})})
        self.assertFalse(analyze(q)['completeness']['trusted_complete'])
    def test_changed_source_hash(self):
        q,a,_=attest(make());source=q.packet.source.model_copy(update={'content_sha256':'f'*64})
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'source':source})})
        self.assertFalse(analyze(q,a)['completeness']['trusted_complete'])
    def test_region_coverage_missing(self):
        q,a,v=attest(make());inv=inventory(q);inv['checked_regions']=[]
        with self.assertRaises(ValueError):a.record(q,inv,v)
    def test_normalization_issue_blocks_even_record(self):
        q=make([{'cohort_id':None}]);q,a,_=attest(q)
        self.assertFalse(analyze(q,a)['completeness']['trusted_complete'])
    def test_trusted_synthetic_attestation(self):
        q,a,_=attest(make());self.assertTrue(analyze(q,a)['completeness']['trusted_complete'])
    def test_missing_record(self):
        self.assertFalse(analyze(make())['completeness']['trusted_complete'])
    def test_forged_actor(self):
        q,a,v=attest(make())
        with self.assertRaises(ValueError):a.record(q,inventory(q),replace(v))
    def test_untrusted_attestation_id(self):
        self.assertFalse(analyze(make().model_copy(update={'attestation_id':'forged'}))['completeness']['trusted_complete'])
    def test_changed_scope(self):
        q,a,_=attest(make());q=q.model_copy(update={'scope':q.scope.model_copy(update={'scope_id':'changed'})})
        self.assertFalse(analyze(q,a)['completeness']['trusted_complete'])
    def test_changed_revision(self):
        q,a,_=attest(make());e=q.packet.events[0];from uuid import uuid4
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'events':(e.model_copy(update={'revision':e.revision.model_copy(update={'revision_id':uuid4()})}),)})})
        self.assertFalse(analyze(q,a)['completeness']['trusted_complete'])
    def test_missing_inventory_coverage(self):
        q,a,v=attest(make());inv=inventory(q);inv['checked_pages']=[]
        with self.assertRaises(ValueError):a.record(q,inv,v)
    def test_unresolved_inventory(self):
        q,a,v=attest(make());inv=inventory(q);inv['unresolved']=['unreadable class']
        with self.assertRaises(ValueError):a.record(q,inv,v)
    def test_count_equality_not_bijection(self):
        q,a,v=attest(make());inv=inventory(q);inv['sessions'][0]['candidate_id']='different'
        with self.assertRaises(ValueError):a.record(q,inv,v)
    def test_recurrence_inventory_digest(self):
        q,a,v=attest(make());inv=inventory(q);inv['sessions'][0]['occurrence_digest']='0'*64
        with self.assertRaises(ValueError):a.record(q,inv,v)
    def test_revocation(self):
        q,a,_=attest(make());a.revoke(q.attestation_id);self.assertFalse(analyze(q,a)['completeness']['trusted_complete'])
    def test_expired_verifier(self):
        q,a,v=attest(make());a.clock=lambda:v.expires_at+timedelta(seconds=1)
        self.assertFalse(analyze(q,a)['completeness']['trusted_complete'])
    def test_cannot_verify_own_inventory(self):
        with self.assertRaises(ValueError):CompletenessAuthority().install_verifier('same',independent_of=('same',),expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
    def test_m1_status_alone_not_authority(self):
        q=make();record=CompleteScheduleVerification(status='COMPLETE_SCHEDULE_VERIFIED',timetable_id='synthetic-table',actor_id='forged',
            recorded_at=datetime.now(timezone.utc),source_inventory_digest='0'*64,event_set_digest='0'*64,evidence_record_id='forged')
        q=q.model_copy(update={'packet':q.packet.model_copy(update={'complete_schedule_verification':record})})
        self.assertFalse(analyze(q)['completeness']['trusted_complete'])
    def test_review_declaration_not_completeness(self):
        q=make().model_copy(update={'declared_kind':'HUMAN_REVIEWED_APPLICATION'})
        self.assertFalse(analyze(q)['completeness']['trusted_complete'])
    def test_incomplete_declaration(self):
        q=make().model_copy(update={'declared_kind':'EXPLICITLY_INCOMPLETE'})
        with self.assertRaises(ValueError):attest(q)
    def test_no_mutation_of_m1_or_calendar_status(self):
        q=make();before=q.model_dump_json();r=analyze(q)
        self.assertEqual(before,q.model_dump_json());self.assertFalse(r['calendar_operations']);self.assertFalse(r['external_calls'])


class RecommendationTests(unittest.TestCase):
    def test_wrong_availability_offset(self):
        q,a=self.available();q=q.model_copy(update={'availability':(Availability(start='2026-01-05T10:00:00+02:00',end='2026-01-05T15:00:00+02:00'),)})
        self.assertEqual(analyze(q,a)['window_status'],'NOT_ASSESSED')
    def test_threshold_change_is_explicit_input_digest(self):
        q,a=self.available();r=analyze(q,a);changed=analyze(q.model_copy(update={'long_block_minutes':30}),a)
        self.assertNotEqual(r['input_digest'],changed['input_digest'])
    def test_recommendation_cap_disclosed(self):
        from backend.analysis import service
        q,a=self.available()
        with patch.dict(service.SETTINGS,{'recommendation_cap':1}):r=analyze(q,a)
        self.assertEqual(len(r['recommendations']),1);self.assertEqual(r['window_status'],'OUTPUT_CAP_REACHED')
    def available(self):
        q,a,v=attest(make());q=q.model_copy(update={'availability':(Availability(start='2026-01-05T08:00:00Z',end='2026-01-05T13:00:00Z'),)})
        return q,a
    def test_no_free_time_or_recommendations_without_completeness(self):
        q,_=self.available();r=analyze(q)
        self.assertEqual(r['recommendations'],[]);self.assertIsNone(value(r,'verified_available_windows'))
    def test_fact_linked_windows(self):
        q,a=self.available();r=analyze(q,a);self.assertTrue(r['recommendations'])
        known={f['fact_id'] for f in r['facts']}
        for rec in r['recommendations']:
            self.assertTrue(set(rec['fact_ids'])<=known);self.assertTrue(rec['event_refs'])
            self.assertEqual(rec['version'],'m7-development-v1')
            self.assertNotIn('preference',rec['explanation'])
    def test_no_windows_without_explicit_availability(self):
        q,a,_=attest(make());self.assertEqual(analyze(q,a)['window_status'],'NOT_ASSESSED')
    def test_no_feasible_window(self):
        q,a=self.available();q=q.model_copy(update={'availability':(Availability(start='2026-01-05T09:00:00Z',end='2026-01-05T10:00:00Z'),)})
        r=analyze(q,a);self.assertEqual(r['window_status'],'NO_FEASIBLE_WINDOW');self.assertEqual(r['recommendations'],[])
    def test_buffer_and_boundaries(self):
        q,a=self.available();r=analyze(q,a)
        for rec in r['recommendations']:
            w=rec['window'];s=datetime.fromisoformat(w['start']);e=datetime.fromisoformat(w['end'])
            self.assertGreaterEqual(s.hour,8);self.assertLessEqual(e.hour,13)
            self.assertTrue(e<=datetime(2026,1,5,8,45,tzinfo=timezone.utc) or s>=datetime(2026,1,5,10,15,tzinfo=timezone.utc))
    def test_overlapping_availability_rejected(self):
        q,a=self.available();q=q.model_copy(update={'availability':q.availability*2})
        self.assertIsNone(value(analyze(q,a),'verified_available_windows'))
    def test_availability_outside_horizon(self):
        q,a=self.available();q=q.model_copy(update={'availability':(Availability(start='2026-01-04T08:00:00Z',end='2026-01-05T13:00:00Z'),)})
        self.assertEqual(analyze(q,a)['window_status'],'NOT_ASSESSED')
    def test_no_false_whole_free_day(self):
        q,a=self.available();self.assertEqual(value(analyze(q,a),'verified_free_days'),[])
    def test_full_free_day_explicit(self):
        q,a=self.available();q=q.model_copy(update={'availability':(Availability(start='2026-01-06T00:00:00Z',end='2026-01-07T00:00:00Z'),)})
        self.assertEqual(value(analyze(q,a),'verified_free_days'),['2026-01-06'])
    def test_complete_workload_scoped(self):
        q,a,_=attest(make());self.assertEqual(value(analyze(q,a),'total_scope_workload_hours'),1)
    def test_registered_not_substituted(self):
        q,a=self.available();r=analyze(q,a);self.assertEqual(r['registered_p1a_status'],'NOT_EXECUTED');self.assertFalse(r['registered_c1_ready'])


class IntegrationTests(unittest.TestCase):
    def test_no_source_text_or_actor_in_response(self):
        r=self.post(make()).json();text=json.dumps(r)
        self.assertNotIn('raw_text',text);self.assertNotIn('actor_id',text)
    def test_bad_scope(self):
        q=make().model_dump(mode='json');q['scope']['end']='2030-01-01'
        self.assertEqual(self.client.post('/api/analysis/v1/analyze',json=q).status_code,422)
    def setUp(self):self.client=TestClient(app)
    def post(self,q):return self.client.post('/api/analysis/v1/analyze',json=q.model_dump(mode='json'))
    def test_api_actual_facts(self):
        r=self.post(make());self.assertEqual(r.status_code,200);self.assertEqual(value(r.json(),'observed_scheduled_hours'),1)
    def test_api_no_record_minting(self):
        for path in ('attest','verify','approve','sync'):
            self.assertEqual(self.client.post('/api/analysis/v1/'+path,json={}).status_code,404)
    def test_api_forged_record_blocked(self):
        r=self.post(make().model_copy(update={'attestation_id':'fake'}));self.assertFalse(r.json()['completeness']['trusted_complete'])
    def test_api_registered_blocked(self):
        self.assertEqual(self.post(make().model_copy(update={'profile':'registered'})).status_code,409)
    def test_api_bad_contract_redacted(self):
        r=self.client.post('/api/analysis/v1/analyze',json={'token':'SECRET','path':'C:\\private\\secret'})
        self.assertEqual(r.status_code,422);self.assertNotIn('SECRET',r.text)
    def test_api_media(self):
        self.assertEqual(self.client.post('/api/analysis/v1/analyze',content='{}').status_code,415)
    def test_api_size(self):
        self.assertEqual(self.client.post('/api/analysis/v1/analyze',content='x'*(2*1024*1024+1),headers={'content-type':'application/json'}).status_code,413)
    def test_unverified_tar_packet(self):
        from backend.services.event_adapter import canonical_api_data
        from backend.services.timetable_parser import parse_intranet_response
        from tests.fixtures import timetable_html
        q=make().model_copy(update={'packet':CanonicalTimetable.model_validate(canonical_api_data(parse_intranet_response(timetable_html()))['canonical'])})
        self.assertFalse(analyze(q)['completeness']['trusted_complete'])
    def test_m52_partial_reconstruction(self):
        from tests.test_m52_layout import packet,simple
        from backend.layout.service import reconstruct_layout
        output=reconstruct_layout(packet(simple()))[0]['canonical_output']
        p=CanonicalTimetable.model_validate({**output['packet'],'events':output['packet']['events']+output['unresolved_candidates']})
        r=analyze(make().model_copy(update={'packet':p}));self.assertEqual(r['recommendations'],[])
        self.assertEqual(value(r,'observed_candidate_count'),len(p.events))
    def test_frontend(self):
        from tests.test_m51_integration import NODE,ROOT
        run=subprocess.run([NODE,str(ROOT/'tests/m7_frontend_checks.js')],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
