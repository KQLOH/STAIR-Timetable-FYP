"""Synthetic behavior tests, not accuracy evidence or completeness grants."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_m7_analysis import make
from backend.analysis.api import router
from backend.analysis.models import ObservedInput
from backend.analysis.student import observed_analyze

class ObservedWorkloadTests(unittest.TestCase):
    def result(self,events,**extra):return observed_analyze(ObservedInput(packet=make(events).packet,**extra))
    def test_no_dates_timezone_authentication_or_recurrence_needed(self):
        r=self.result([{'date':None,'source_timezone':None,'recurrence':None}]);f=r['student_facts']
        self.assertEqual(f['printed_class_hours'],1);self.assertEqual(f['hours_by_day'],{'Monday':1})
        self.assertIsNone(f['detected_date_range']);self.assertIsNone(f['source_timezone'])
        self.assertFalse(r['trusted_complete']);self.assertEqual(r['recommendations'],[])
    def test_source_supported_dates_timezone_are_metadata_not_term(self):
        f=self.result([{}, {'date':'2026-01-07','day_of_week':3}])['student_facts']
        self.assertEqual(f['detected_date_range'],{'start':'2026-01-05','end':'2026-01-07'})
        self.assertEqual(f['source_timezone'],'Etc/UTC')
    def test_mixed_unknown_timezone_is_not_assumed(self):
        f=self.result([{}, {'source_timezone':None}])['student_facts'];self.assertIsNone(f['source_timezone'])
    def test_exact_duplicate_does_not_double_hours(self):
        f=self.result([{},{}])['student_facts'];self.assertEqual(f['class_count'],1);self.assertEqual(f['printed_class_hours'],1);self.assertEqual(len(f['possible_duplicates']),1)
    def test_overlap_is_possible_not_verified_free_time(self):
        f=self.result([{'end_time':'11:00'},{'course_code':'SYN202','start_time':'10:00','end_time':'12:00'}])['student_facts']
        self.assertEqual(f['printed_class_hours'],4);self.assertEqual(len(f['possible_overlaps']),1)
    def test_missing_times_excluded_without_hiding_other_classes(self):
        f=self.result([{}, {'start_time':None}])['student_facts'];self.assertEqual(f['class_count'],1);self.assertEqual(len(f['excluded_unresolved_candidates']),1)
    def test_weekly_recurrence_never_expanded(self):
        f=self.result([{'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-04-20'}}])['student_facts'];self.assertEqual(f['printed_class_hours'],1)
    def test_invalid_date_filter_rejected(self):
        with self.assertRaises(ValueError):ObservedInput(packet=make().packet,start='2026-01-10',end='2026-01-01')
    def test_partial_date_filter_supported(self):
        f=self.result([{}, {'date':'2026-01-07','day_of_week':3}],start='2026-01-06')['student_facts'];self.assertEqual(f['class_count'],1)
    def test_weekday_only_not_assigned_to_a_filter_date(self):
        f=self.result([{'date':None}],start='2026-01-06',end='2026-01-07')['student_facts'];self.assertEqual(f['class_count'],1);self.assertEqual(f['occupied_local_slots'][0]['day'],'Monday')
    def test_dated_overnight_overlap_and_daily_hours(self):
        f=self.result([{'start_time':'23:00','end_time':'01:00','end_day_offset':1},{'course_code':'SYN202','date':'2026-01-06','day_of_week':2,'start_time':'00:30','end_time':'02:00'}])['student_facts']
        self.assertEqual(f['hours_by_day'],{'2026-01-05':1,'2026-01-06':2.5});self.assertEqual(len(f['possible_overlaps']),1)
    def test_sunday_overnight_weekday_overlap(self):
        f=self.result([{'date':None,'day_of_week':7,'start_time':'23:00','end_time':'01:00','end_day_offset':1},{'course_code':'SYN202','date':None,'day_of_week':1,'start_time':'00:30','end_time':'02:00'}])['student_facts']
        self.assertEqual(len(f['possible_overlaps']),1);self.assertEqual(f['hours_by_day']['Monday'],2.5)
    def test_anonymous_route_has_no_authority_or_calendar_capability(self):
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            r=client.post('/api/analysis/v1/observed',json={'packet':make().packet.model_dump(mode='json')})
            self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['trusted_complete']);self.assertFalse(r.json()['calendar_authorized'])
            for extra in ('attestation_id','availability','approved','complete_schedule_verified'):
                self.assertEqual(client.post('/api/analysis/v1/observed',json={'packet':make().packet.model_dump(mode='json'),extra:True}).status_code,422)

    def test_uncertain_non_time_detail_does_not_hide_supported_duration(self):
        q=make();event=q.packet.events[0].model_copy(update={'extraction_status':'AMBIGUOUS'})
        packet=q.packet.model_copy(update={'events':(event,)})
        r=observed_analyze(ObservedInput(packet=packet));self.assertEqual(r['student_facts']['printed_class_hours'],1)
        self.assertEqual(r['student_facts']['uncertain_details_class_count'],1);self.assertFalse(r['trusted_complete'])
    def test_ambiguous_time_never_counted_as_certain_duration(self):
        from backend.models.event import FieldObservation
        q=make();event=q.packet.events[0];observations=tuple(FieldObservation(field_name='start_time',state='UNRESOLVED',raw_value='unclear',evidence_ids=o.evidence_ids) if o.field_name=='start_time' else o for o in event.revision.observations)
        event=event.model_copy(update={'extraction_status':'AMBIGUOUS','revision':event.revision.model_copy(update={'observations':observations})})
        r=observed_analyze(ObservedInput(packet=q.packet.model_copy(update={'events':(event,)})))
        self.assertEqual(r['student_facts']['class_count'],0)


    def alternative_readings(self,field,alternatives):
        from backend.models.event import CandidateAlternative
        q=make();event=q.packet.events[0]
        observations=tuple(o.model_copy(update={'alternatives':tuple(
            CandidateAlternative(raw_value=raw,normalized_value=normalized,evidence_ids=o.evidence_ids)
            for raw,normalized in alternatives)}) if o.field_name==field else o for o in event.revision.observations)
        event=event.model_copy(update={'revision':event.revision.model_copy(update={'observations':observations})})
        return ObservedInput(packet=q.packet.model_copy(update={'events':(event,)}))

    def test_unknown_normalized_time_alternatives_are_not_certain_hours(self):
        request=self.alternative_readings('start_time',[('09:00',None),('10:00',None)])
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            response=client.post('/api/analysis/v1/observed',json=request.model_dump(mode='json'))
        self.assertEqual(response.status_code,200)
        result=response.json();facts=result['student_facts']
        self.assertEqual(facts['class_count'],0);self.assertIsNone(facts['printed_class_hours'])
        self.assertEqual(len(facts['excluded_unresolved_candidates']),1)
        self.assertFalse(result['trusted_complete']);self.assertEqual(result['recommendations'],[])

    def test_single_alternative_must_match_the_selected_time(self):
        request=self.alternative_readings('start_time',[('10:00','10:00')])
        self.assertEqual(observed_analyze(request)['student_facts']['class_count'],0)

    def test_equivalent_json_and_plain_time_readings_remain_supported(self):
        request=self.alternative_readings('start_time',[('9 AM','"09:00"'),('09:00','09:00')])
        self.assertEqual(observed_analyze(request)['student_facts']['printed_class_hours'],1)

    def test_uncertain_venue_does_not_hide_supported_class_hours(self):
        request=self.alternative_readings('location',[('SYNTHETIC ROOM A',None),('SYNTHETIC ROOM B',None)])
        facts=observed_analyze(request)['student_facts']
        self.assertEqual(facts['class_count'],1);self.assertEqual(facts['printed_class_hours'],1)
        self.assertIsNone(facts['occupied_local_slots'][0]['location'])

    def test_unknown_course_identity_alternatives_are_excluded(self):
        request=self.alternative_readings('course_code',[('SYN101',None),('SYN202',None)])
        self.assertEqual(observed_analyze(request)['student_facts']['class_count'],0)
