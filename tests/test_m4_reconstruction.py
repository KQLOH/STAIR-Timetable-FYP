"""M4 synthetic contract tests; no timetable datasets or gold labels are read."""
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.main import app
from backend.models.evidence import SourceDocument, EvidenceSpan, BoundingBox, ParseIssue
from backend.models.event import CanonicalTimetable, FieldState
from backend.reconstruction.models import (Context, EvidenceLink, TermAuthority, PageCoordinates,
    development_settings, canonical_bytes, digest)
from backend.reconstruction.candidates import build_graph, normalize
from backend.reconstruction.constraints import event_findings, timetable_findings, feasible
from backend.reconstruction.decoder import decode, components
from backend.reconstruction.scoring import score, objective
from backend.reconstruction.service import reconstruct, compare_modes


def packet(*texts, route='text', confidence=None):
    source=SourceDocument(source_document_id='synthetic-capture',content_sha256=sha256('\n'.join(texts).encode()).hexdigest(),
                          route=route,media_type='text/plain',content_hash_basis='decoded_text_utf8')
    spans=tuple(EvidenceSpan(evidence_id=f's{i}',source_document_id=source.source_document_id,route=route,
                             raw_text=text,page=1,confidence=confidence,
                             bbox=BoundingBox(x0=100,y0=30+i*30,x1=550,y1=50+i*30,coordinate_space='PDF_POINTS'))
                for i,text in enumerate(texts))
    return CanonicalTimetable(source=source,evidence=spans)


SINGLE='CS101 Monday 09:00-11:00 Lecture Room: A1'


def output(p,mode='constrained',context=Context(),settings=None):
    return reconstruct(p,mode,context,settings)['canonical_output']


def selected(group):
    return {f:next((n for n in nodes if n.state==FieldState.PRESENT),nodes[-1]) for f,nodes in group.domains.items()}


class CandidateTests(unittest.TestCase):
    def test_single_lecture(self):
        o=output(packet(SINGLE))
        event=o['packet']['events'][0]
        self.assertEqual(event['revision']['content']['course_code'],'CS101')
        self.assertEqual(event['revision']['content']['session_type'],'Lecture')
        self.assertEqual(event['revision']['content']['location'],'A1')

    def test_multiple_sessions_one_cell(self):
        o=output(packet('CS101 Monday Lecture 09:00-10:00; Tutorial Tuesday 10:00-11:00 Group G2'))
        self.assertEqual(len(o['packet']['events']),2)
        self.assertEqual({e['revision']['content']['session_type'] for e in o['packet']['events']},{'Lecture','Tutorial'})
        self.assertTrue(all(e['revision']['content']['course_code']=='CS101' for e in o['packet']['events']))

    def test_compound_without_delimiter(self):
        self.assertEqual(len(output(packet('CS101 Monday Lecture 09:00-10:00 Tutorial Tuesday 10:00-11:00'))['packet']['events']),2)

    def test_laboratory_alias_preserves_raw(self):
        event=output(packet('CS101 Monday 09:00-10:00 Laboratory'))['packet']['events'][0]
        self.assertEqual(event['revision']['content']['session_type'],'Practical')
        obs=next(x for x in event['revision']['observations'] if x['field_name']=='session_type')
        self.assertEqual(obs['raw_value'],'Laboratory')

    def test_unknown_venue(self):
        event=output(packet('CS101 Monday 09:00-10:00 Room: TBA'))['packet']['events'][0]
        obs=next(x for x in event['revision']['observations'] if x['field_name']=='location')
        self.assertIsNone(event['revision']['content']['location'])
        self.assertEqual(obs['state'],'UNRESOLVED')

    def test_missing_venue_no_default(self):
        event=output(packet('CS101 Monday 09:00-10:00'))['packet']['events'][0]
        self.assertIsNone(event['revision']['content']['location'])
        self.assertIsNone(event['revision']['content']['source_timezone'])

    def test_unknown_weeks(self):
        event=output(packet('CS101 Monday 09:00-10:00 Weeks: UNKNOWN'))['packet']['events'][0]
        self.assertEqual(next(x for x in event['revision']['observations'] if x['field_name']=='teaching_weeks')['state'],'UNRESOLVED')

    def test_unknown_session_type_stays_missing(self):
        event=output(packet('CS101 Monday 09:00-10:00'))['packet']['events'][0]
        self.assertIsNone(event['revision']['content']['session_type'])

    def test_missing_core_retained_skeleton(self):
        o=output(packet('CS101 Lecture Time: TBA'))
        self.assertFalse(o['packet']['events'])
        self.assertEqual(len(o['unresolved_candidates']),1)
        self.assertEqual(next(x for x in o['unresolved_candidates'][0]['revision']['observations'] if x['field_name']=='start_time')['state'],'UNRESOLVED')

    def test_header_geometry_inheritance(self):
        p=packet('Monday','09:00-11:00','CS101 Lecture')
        spans=list(p.evidence)
        spans[0]=spans[0].model_copy(update={'bbox':BoundingBox(x0=100,y0=0,x1=550,y1=20,coordinate_space='PDF_POINTS')})
        spans[1]=spans[1].model_copy(update={'bbox':BoundingBox(x0=0,y0=90,x1=80,y1=110,coordinate_space='PDF_POINTS')})
        spans[2]=spans[2].model_copy(update={'bbox':BoundingBox(x0=100,y0=90,x1=550,y1=110,coordinate_space='PDF_POINTS')})
        o=output(CanonicalTimetable(source=p.source,evidence=tuple(spans)))
        self.assertEqual(len(o['packet']['events']),1)
        self.assertEqual(o['packet']['events'][0]['revision']['content']['day_of_week'],1)
        self.assertTrue(any('source-header-scope' in n['transformations'] for d in o['graph']['groups'][0]['domains'].values() for n in d))

    def test_merged_explicit_source_group(self):
        p=packet('Monday','09:00-11:00','CS101 Lecture','CS102 Tutorial')
        p=CanonicalTimetable(source=p.source,evidence=tuple(s.model_copy(update={'transformations':('source-group:merged-synthetic',)}) for s in p.evidence))
        self.assertEqual(len(output(p)['packet']['events']),2)

    def test_cross_page_continuation(self):
        p=packet('CS101 Monday Lecture','09:00-11:00 Room: A1')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0],p.evidence[1].model_copy(update={'page':2})))
        c=Context(links=(EvidenceLink(source_evidence_id='s0',target_evidence_id='s1',kind='event_continuation',reason_evidence_ids=('s1',)),))
        o=output(p,context=c)
        self.assertEqual(len(o['packet']['events']),1)
        self.assertIn('EXPLICIT_CROSS_PAGE_CONTINUATION',o['graph']['groups'][0]['diagnostics'])

    def test_unproven_cross_page_not_joined(self):
        p=packet('CS101 Monday Lecture','09:00-11:00 Room: A1')
        self.assertFalse(output(p)['packet']['events'])

    def test_ambiguous_session_association(self):
        o=output(packet('CS101 Monday 09:00-10:00 or 11:00-12:00 Lecture'))
        self.assertIn('AMBIGUOUS_TEMPORAL_PAIRING',o['graph']['groups'][0]['diagnostics'])
        obs=next(x for x in o['packet']['events'][0]['revision']['observations'] if x['field_name']=='start_time')
        self.assertEqual(len(obs['alternatives']),2)
        self.assertEqual(o['packet']['events'][0]['extraction_status'],'AMBIGUOUS')

    def test_duplicate_evidence_collapsed(self):
        p=packet(SINGLE)
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0],p.evidence[0].model_copy(update={'evidence_id':'copy'})))
        o=output(p)
        self.assertEqual(len(o['packet']['events']),1)
        self.assertIn('DUPLICATE_EVIDENCE_COLLAPSED',o['graph']['diagnostics'])

    def test_deterministic_input_order(self):
        p=packet(SINGLE,'CS102 Tuesday 12:00-13:00 Tutorial')
        reversed_packet=CanonicalTimetable(source=p.source,evidence=tuple(reversed(p.evidence)))
        self.assertEqual(reconstruct(p,'constrained')['canonical_digest'],reconstruct(reversed_packet,'constrained')['canonical_digest'])

    def test_coordinate_rounding_and_ocr_mapping(self):
        p=packet(SINGLE,route='ocr')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0].model_copy(update={'bbox':BoundingBox(x0=100.25,y0=30,x1=500,y1=50,coordinate_space='PIXELS')}),))
        c=Context(coordinates=(PageCoordinates(page=1,dpi=72,rotation_normalized=True),))
        g=build_graph(p,development_settings(),c)
        self.assertNotIn('COORDINATE_MAPPING_UNFROZEN',g.diagnostics)
        self.assertEqual(g.evidence[0].bbox.coordinate_space,'PIXELS')

    def test_unknown_geometry_explicit(self):
        p=packet(SINGLE)
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0].model_copy(update={'bbox':None}),))
        self.assertIn('GEOMETRY_UNAVAILABLE',output(p)['graph']['diagnostics'])

    def test_prior_extraction_failures_retained(self):
        p=packet(SINGLE)
        p=p.model_copy(update={'issues':(ParseIssue(code='SYNTHETIC_PARSE_FAILURE',evidence_ids=('s0',)),)})
        self.assertIn('INPUT_ISSUE:SYNTHETIC_PARSE_FAILURE',output(p)['graph']['diagnostics'])

    def test_source_term_dates_are_not_course_identifiers(self):
        g=build_graph(packet(SINGLE+' weekly term: 2026-01-01 to 2026-03-01'),development_settings())
        self.assertEqual(len(g.groups),1)
        self.assertEqual({n.value for n in g.groups[0].domains['course_code'] if n.state==FieldState.PRESENT},{'CS101'})
        recurrence=next(n for n in g.groups[0].domains['recurrence'] if n.state==FieldState.PRESENT)
        self.assertEqual(recurrence.value['term_start'],'2026-01-01')
        self.assertFalse(any(n.state==FieldState.PRESENT for n in g.groups[0].domains['date']))

    def test_mixed_evidence_routes_rejected(self):
        p=packet(SINGLE,route='ocr')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0].model_copy(update={'route':'native_pdf'}),))
        with self.assertRaises(ValueError): output(p)

    def test_venue_boundary_preserves_timezone_field(self):
        o=output(packet(SINGLE+' Timezone: Etc/UTC'))
        self.assertEqual(o['packet']['events'][0]['revision']['content']['location'],'A1')

    def test_evidence_confidence_not_manufactured(self):
        o=output(packet(SINGLE))
        self.assertTrue(all(n['confidence'] is None for d in o['graph']['groups'][0]['domains'].values() for n in d))

    def test_zero_candidates(self):
        o=output(packet('Synthetic title with no sessions'))
        self.assertFalse(o['packet']['events'])
        self.assertFalse(o['unresolved_candidates'])

    def test_week_normalization(self):
        self.assertEqual(normalize('teaching_weeks','1-3, 5'),(1,2,3,5))
        self.assertIsNone(normalize('teaching_weeks','5-1'))
        self.assertIsNone(normalize('teaching_weeks','TBA'))

    def test_explicit_course_name(self):
        o=output(packet('Monday 09:00-10:00 Course Name: Software Engineering [Lecture]'))
        self.assertEqual(o['packet']['events'][0]['revision']['content']['course_name'],'Software Engineering')

    def test_competing_course_codes_preserved(self):
        o=output(packet('CS101 or CS102 Monday 09:00-10:00 Lecture'))
        self.assertEqual(len(o['graph']['groups']),1)
        obs=next(x for x in o['packet']['events'][0]['revision']['observations'] if x['field_name']=='course_code')
        self.assertEqual({x['raw_value'] for x in obs['alternatives']},{'CS101','CS102'})

    def test_pure_time_continuation(self):
        p=packet('CS101 Monday Lecture','09:00-11:00')
        c=Context(links=(EvidenceLink(source_evidence_id='s0',target_evidence_id='s1',kind='event_continuation',reason_evidence_ids=('s1',)),))
        self.assertEqual(len(output(p,context=c)['packet']['events']),1)

    def test_ambiguous_continuation_not_forced(self):
        p=packet('CS101 Monday Lecture; CS102 Tuesday Tutorial','09:00-11:00')
        c=Context(links=(EvidenceLink(source_evidence_id='s0',target_evidence_id='s1',kind='event_continuation',reason_evidence_ids=('s1',)),))
        o=output(p,context=c)
        self.assertFalse(o['packet']['events'])
        self.assertIn('AMBIGUOUS_CONTINUATION_SCOPE',o['graph']['diagnostics'])


class ConstraintTests(unittest.TestCase):
    def test_temporal_order_p1_and_b3(self):
        p=packet('CS101 Monday 11:00-09:00 Lecture')
        self.assertFalse(output(p)['packet']['events'])
        b3=output(p,'unconstrained')
        self.assertEqual(len(b3['packet']['events']),1)
        self.assertEqual(b3['packet']['events'][0]['validation']['status'],'FAILED')
        self.assertTrue(any(f['rule_id']=='T01' and f['passed'] is False for f in b3['findings']))

    def test_explicit_overnight(self):
        self.assertEqual(len(output(packet('CS101 Monday 23:00-01:00 overnight'))['packet']['events']),1)
        self.assertFalse(output(packet('CS101 Monday 23:00-01:00'))['packet']['events'])

    def test_end_of_day(self):
        self.assertEqual(len(output(packet('CS101 Monday 23:00-24:00'))['packet']['events']),1)

    def test_recurrence_unknown_authority_blocks_p1(self):
        p=packet(SINGLE+' weekly')
        self.assertFalse(output(p)['packet']['events'])
        self.assertEqual(len(output(p,'unconstrained')['packet']['events']),1)

    def test_recurrence_with_source_bounds_and_attested_authority(self):
        p=packet(SINGLE+' weekly term: 2026-01-01 to 2026-03-01','Synthetic attested term bounds: 2026-01-01 to 2026-04-01')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0],p.evidence[1].model_copy(update={'route':'authority'})))
        c=Context(term=TermAuthority(start='2026-01-01',end='2026-04-01',authority_record_id='synthetic-attestation',evidence_ids=('s1',)))
        self.assertEqual(len(output(p,context=c)['packet']['events']),1)

    def test_non_authority_term_not_trusted(self):
        p=packet(SINGLE+' weekly term: 2026-01-01 to 2026-03-01','Synthetic plain text bounds')
        c=Context(term=TermAuthority(start='2026-01-01',end='2026-04-01',authority_record_id='synthetic',evidence_ids=('s1',)))
        self.assertFalse(output(p,context=c)['packet']['events'])

    def test_same_cohort_overlap(self):
        p=packet('CS101 Monday 09:00-11:00 Cohort C1 Weeks 1-3','CS102 Monday 10:00-12:00 Cohort C1 Weeks 1-3')
        self.assertEqual(len(output(p)['packet']['events']),1)
        b3=output(p,'unconstrained')
        self.assertEqual(len(b3['packet']['events']),2)
        self.assertTrue(any(f['rule_id']=='C01' and f['passed'] is False for f in b3['findings']))

    def test_term_authority_cannot_invent_source_bounds(self):
        p=packet(SINGLE+' weekly','Synthetic term 2026-01-01 to 2026-04-01')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0],p.evidence[1].model_copy(update={'route':'authority'})))
        c=Context(term=TermAuthority(start='2026-01-01',end='2026-04-01',authority_record_id='synthetic',evidence_ids=('s1',)))
        self.assertFalse(output(p,context=c)['packet']['events'])

    def test_out_of_term_recurrence(self):
        p=packet(SINGLE+' weekly term: 2026-01-01 to 2026-05-01','Synthetic term 2026-01-01 to 2026-04-01')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0],p.evidence[1].model_copy(update={'route':'authority'})))
        c=Context(term=TermAuthority(start='2026-01-01',end='2026-04-01',authority_record_id='synthetic',evidence_ids=('s1',)))
        self.assertFalse(output(p,context=c)['packet']['events'])

    def test_invalid_source_term_date_remains_unresolved(self):
        p=packet(SINGLE+' weekly term: 2026-02-30 to 2026-03-01')
        o=output(p)
        self.assertFalse(o['packet']['events'])
        self.assertEqual(len(o['unresolved_candidates']),1)
        self.assertIn('2026-02-30',o['packet']['evidence'][0]['raw_text'])

    def test_authority_does_not_generate_sessions(self):
        p=packet(SINGLE)
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0].model_copy(update={'route':'authority'}),))
        self.assertFalse(output(p)['packet']['events'])

    def test_timezone_authority_gate(self):
        p=packet(SINGLE+' Timezone: Etc/UTC','Synthetic authority confirms Etc/UTC')
        p=CanonicalTimetable(source=p.source,evidence=(p.evidence[0],p.evidence[1].model_copy(update={'route':'authority'})))
        c=Context(timezone_authority_ids=('s1',))
        o=output(p,context=c)
        self.assertTrue(any(f['rule_id']=='T02' and f['passed'] is True for f in o['findings']))
        self.assertEqual(o['packet']['events'][0]['calendar']['eligibility'],'NOT_ASSESSED')

    def test_hard_overlap_cannot_be_evaded_by_week_omission(self):
        g=build_graph(packet(SINGLE+' Cohort C1 Weeks 1-3','CS102 Monday 10:00-12:00 Cohort C1 Weeks 1-3'),development_settings())
        assignments=[]
        for group in g.groups:
            s=selected(group); s['teaching_weeks']=group.domains['teaching_weeks'][-1]
            assignments.append((group,s))
        self.assertTrue(any(f.rule_id=='C01' and f.passed is False for f in timetable_findings(assignments,g)))

    def test_distinct_dates_do_not_conflict(self):
        p=packet('CS101 2026-01-01 09:00-11:00 Cohort C1','CS102 2026-01-08 10:00-12:00 Cohort C1')
        self.assertEqual(len(output(p)['packet']['events']),2)

    def test_date_weekday_diagnostic_only(self):
        o=output(packet('CS101 Monday 2026-01-01 09:00-10:00'))
        self.assertEqual(len(o['packet']['events']),1)
        self.assertTrue(any(f['rule_id']=='DATE_DAY_DIAGNOSTIC' for f in o['findings']))

    def test_distinct_cohorts(self):
        p=packet('CS101 Monday 09:00-11:00 Cohort C1 Weeks 1-3','CS102 Monday 10:00-12:00 Cohort C2 Weeks 1-3')
        self.assertEqual(len(output(p)['packet']['events']),2)

    def test_tutorial_group_distinctions(self):
        p=packet('CS101 Monday 09:00-11:00 Tutorial Cohort C1 Group G1 Weeks 1-3','CS101 Monday 10:00-12:00 Tutorial Cohort C1 Group G2 Weeks 1-3')
        self.assertEqual(len(output(p)['packet']['events']),2)

    def test_different_week_ranges(self):
        p=packet('CS101 Monday 09:00-11:00 Cohort C1 Weeks 1-3','CS102 Monday 10:00-12:00 Cohort C1 Weeks 4-6')
        self.assertEqual(len(output(p)['packet']['events']),2)

    def test_unknown_cohort_not_assumed(self):
        p=packet('CS101 Monday 09:00-11:00','CS102 Monday 10:00-12:00')
        self.assertEqual(len(output(p)['packet']['events']),2)

    def test_possible_duplicate_without_cohort_is_diagnostic(self):
        o=output(packet(SINGLE,SINGLE))
        self.assertEqual(len(o['packet']['events']),2)
        self.assertTrue(any(f['rule_id']=='DUPLICATE_DIAGNOSTIC' for f in o['findings']))

    def test_explicit_alternative_groups(self):
        p=packet('CS101 Monday 09:00-11:00 Cohort C1 Weeks 1-3','CS102 Monday 10:00-12:00 Cohort C1 Weeks 1-3')
        self.assertEqual(len(output(p,context=Context(alternative_group_evidence_ids=('s0','s1')))['packet']['events']),2)

    def test_schema_provenance(self):
        settings=development_settings(); g=build_graph(packet(SINGLE),settings); group=g.groups[0]; s=selected(group)
        s['course_code']=s['course_code'].model_copy(update={'evidence_ids':('unknown',)})
        findings=event_findings(group,s,g)
        self.assertFalse(next(f.passed for f in findings if f.rule_id=='S02'))
        self.assertFalse(feasible(findings,'unconstrained'))

    def test_invalid_vocabulary_shared(self):
        settings=development_settings(); g=build_graph(packet(SINGLE),settings); group=g.groups[0]; s=selected(group)
        s['session_type']=s['session_type'].model_copy(update={'value':'Laboratory'})
        self.assertFalse(feasible(event_findings(group,s,g),'unconstrained'))

    def test_exclusive_ownership(self):
        g=build_graph(packet(SINGLE,'CS102 Tuesday 12:00-13:00'),development_settings())
        s1,s2=[selected(x) for x in g.groups]
        s2['course_code']=s2['course_code'].model_copy(update={'ownership_units':s1['course_code'].ownership_units})
        self.assertFalse(next(f.passed for f in timetable_findings(list(zip(g.groups,(s1,s2))),g) if f.rule_id=='OWNERSHIP'))

    def test_timezone_gate_never_prunes(self):
        p=packet(SINGLE)
        self.assertEqual(len(output(p)['packet']['events']),1)
        self.assertTrue(any(f['rule_id']=='T02' and f['passed'] is False for f in output(p)['findings']))

    def test_week_diagnostic_not_new_hard_rule(self):
        p=packet(SINGLE+' Weeks 1-3 except Weeks 4')
        o=output(p)
        self.assertEqual(len(o['packet']['events']),1)
        self.assertTrue(any(f['rule_id']=='WEEK_DIAGNOSTIC' for f in o['findings']))

    def test_status_separation(self):
        o=output(packet(SINGLE)); e=o['packet']['events'][0]
        self.assertIsNone(e['policy']['decision'])
        self.assertEqual(e['human_review']['status'],'NOT_REVIEWED')
        self.assertEqual(e['human_verification']['status'],'NOT_VERIFIED')
        self.assertEqual(e['identity']['status'],'UNRESOLVED')
        self.assertIsNone(e['identity']['stable_key'])
        self.assertEqual(e['calendar']['approval_status'],'NOT_APPROVED')
        self.assertEqual(e['calendar']['sync_status'],'NOT_SUBMITTED')
        self.assertEqual(o['packet']['complete_schedule_verification']['status'],'NOT_VERIFIED')


class MatchingAndSearchTests(unittest.TestCase):
    def test_matched_inputs_scores_and_budgets(self):
        compared=compare_modes(packet('CS101 Monday 11:00-09:00 Lecture'))
        self.assertTrue(compared['matching_checks_passed'])
        a,b=[r['canonical_output'] for r in compared['runs'].values()]
        self.assertEqual(a['candidate_graph_digest'],b['candidate_graph_digest'])
        self.assertEqual(a['base_score_digest'],b['base_score_digest'])
        self.assertEqual(a['components'][0]['assignments_visited'],b['components'][0]['assignments_visited'])
        self.assertEqual(a['components'][0]['operations'],b['components'][0]['operations'])
        self.assertFalse(compared['registered_comparator_ready'])

    def test_candidate_identity_matches(self):
        p=packet(SINGLE)
        a,b=output(p),output(p,'unconstrained')
        self.assertEqual(a['packet']['events'][0]['candidate_id'],b['packet']['events'][0]['candidate_id'])
        self.assertEqual(a['packet']['events'][0]['identity'],b['packet']['events'][0]['identity'])

    def test_deterministic_tie(self):
        p=packet('CS101 Monday 09:00-10:00 or 11:00-12:00')
        a=output(p); b=output(p,'unconstrained')
        self.assertEqual(a['packet']['events'][0]['candidate_id'],b['packet']['events'][0]['candidate_id'])
        self.assertEqual(reconstruct(p,'constrained')['canonical_digest'],reconstruct(p,'constrained')['canonical_digest'])

    def test_search_incomplete_no_incumbent_prediction(self):
        settings=development_settings(max_assignments=3)
        for mode in ('constrained','unconstrained'):
            o=output(packet(SINGLE),mode,settings=settings)
            self.assertEqual(o['status'],'SEARCH_INCOMPLETE')
            self.assertFalse(o['packet']['events'])
            self.assertEqual(len(o['unresolved_candidates']),1)

    def test_operation_cap(self):
        o=output(packet(SINGLE),settings=development_settings(max_operations=2))
        self.assertEqual(o['components'][0]['reason'],'OPERATION_CAP')

    def test_component_cap(self):
        p=packet(SINGLE+' Cohort C1','CS102 Tuesday 12:00-13:00 Cohort C1')
        o=output(p,settings=development_settings(max_component_groups=1))
        self.assertEqual(o['components'][0]['reason'],'COMPONENT_GROUP_CAP')
        self.assertFalse(o['packet']['events'])

    def test_wallclock_abort_no_canonical_output(self):
        settings=development_settings(watchdog_seconds=1); g=build_graph(packet(SINGLE),settings)
        counter=iter(range(100))
        result=decode(g,settings,'constrained',clock=lambda:next(counter))
        self.assertEqual(result['status'],'EXECUTION_ABORTED')
        self.assertIsNone(result['canonical_output'])

    def test_settings_digest_guards_mutation(self):
        settings=development_settings(); g=build_graph(packet(SINGLE),settings)
        settings.base_weights['completeness']=0
        with self.assertRaises(ValueError): decode(g,settings,'constrained')

    def test_nonzero_score_requires_explicit_maps(self):
        with self.assertRaises(ValidationError): development_settings(base_weights={'extraction':10000,'relations':0,'completeness':0})
        with self.assertRaises(ValidationError): development_settings(soft_weights={'P01':500,'P02':0})

    def test_explicit_support_and_soft_penalty_matching(self):
        settings=development_settings(base_weights={'extraction':5000,'relations':0,'completeness':5000},
            route_support_tables={'text':((0,0),(5000,5000),(10000,10000))},soft_weights={'P01':1000,'P02':500},
            provenance_min_support_units=9000,typical_relation_min_units=9000)
        g=build_graph(packet(SINGLE,confidence=.6),settings); group=g.groups[0]
        record=score(group,selected(group),g,settings)
        self.assertEqual(record['components']['extraction'],5000)
        self.assertLess(objective(record,settings,'constrained'),objective(record,settings,'unconstrained'))
        self.assertTrue(compare_modes(packet(SINGLE,confidence=.6),settings=settings)['matching_checks_passed'])

    def test_missing_route_map_rejected(self):
        settings=development_settings(base_weights={'extraction':10000,'relations':0,'completeness':0},route_support_tables={'ocr':((0,0),(10000,10000))})
        with self.assertRaises(ValueError): output(packet(SINGLE),settings=settings)

    def test_time_relationships_preserve_pairing(self):
        settings=development_settings(base_weights={'extraction':0,'relations':5000,'completeness':5000})
        o=output(packet('CS101 Monday 09:00-10:00 or 11:00-12:00 Lecture'),settings=settings)
        v=o['packet']['events'][0]['revision']['content']
        self.assertIn((v['start_time'],v['end_time']),{('09:00','10:00'),('11:00','12:00')})

    def test_candidate_caps_disclosed(self):
        settings=development_settings(max_field_values=1)
        o=output(packet('CS101 or CS102 Monday 09:00-10:00 Lecture'),settings=settings)
        self.assertIn('FIELD_VALUE_CAP',o['graph']['groups'][0]['diagnostics'])

    def test_comparability_not_granted_for_incomplete_search(self):
        result=compare_modes(packet(SINGLE),settings=development_settings(max_assignments=2))
        self.assertFalse(result['matching_checks_passed'])

    def test_canonical_nfc_and_no_execution_metadata(self):
        self.assertEqual(canonical_bytes({'s':'e\u0301'}),canonical_bytes({'s':'é'}))
        r=reconstruct(packet(SINGLE),'constrained')
        self.assertNotIn('elapsed_seconds',json.dumps(r['canonical_output']))
        self.assertEqual(r['canonical_digest'],digest(r['canonical_output']))

    def test_m1_roundtrip(self):
        o=output(packet(SINGLE))
        CanonicalTimetable.model_validate(o['packet'])
        self.assertTrue(o['packet']['evidence'][0]['bbox'])

    def test_predecessor_provenance_kept_separate(self):
        p=CanonicalTimetable.model_validate(output(packet(SINGLE))['packet'])
        result=reconstruct(p,'unconstrained')
        provenance=result['execution_metadata']['input_revision_provenance'][0]
        self.assertEqual(provenance['revision']['revision_id'],str(p.events[0].revision.revision_id))
        self.assertIsNone(result['canonical_output']['packet']['events'][0]['policy']['decision'])

    def test_original_issue_evidence_preserved(self):
        p=packet(SINGLE)
        p=p.model_copy(update={'issues':(ParseIssue(code='SYNTHETIC_FAILURE',evidence_ids=('s0',),detail='Synthetic unreadable cell'),)})
        o=output(p)
        self.assertEqual(o['packet']['issues'][0]['detail'],'Synthetic unreadable cell')
        self.assertIn(o['packet']['issues'][0]['evidence_ids'][0],{s['evidence_id'] for s in o['packet']['evidence']})


class APITests(unittest.TestCase):
    def setUp(self): self.client=TestClient(app)

    def request(self,p=None,**changes):
        body={'packet':(p or packet(SINGLE)).model_dump(mode='json'),'decoder_mode':'constrained','profile':'m4-development-v1'}
        body.update(changes)
        return self.client.post('/api/reconstruct',json=body)

    def test_explicit_reconstruction_api(self):
        r=self.request()
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(r.json()['canonical_output']['packet']['events']),1)

    def test_missing_mode_rejected(self):
        r=self.client.post('/api/reconstruct',json={'packet':packet(SINGLE).model_dump(mode='json'),'profile':'m4-development-v1'})
        self.assertEqual(r.status_code,422)

    def test_bad_mode_rejected(self): self.assertEqual(self.request(decoder_mode='LayoutLMv3').status_code,422)

    def test_registered_profile_blocked(self): self.assertEqual(self.request(profile='registered').status_code,409)

    def test_malformed_body_safe(self):
        r=self.client.post('/api/reconstruct',content=b'{private invalid',headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,422)
        self.assertNotIn('private',r.text)

    def test_unsupported_media_type(self): self.assertEqual(self.client.post('/api/reconstruct',content=b'x').status_code,415)

    def test_size_limit(self):
        self.assertEqual(self.client.post('/api/reconstruct',content=b' '*1048577,headers={'Content-Type':'application/json'}).status_code,413)

    def test_unknown_context_reference(self):
        self.assertEqual(self.request(context={'timezone_authority_ids':['missing']}).status_code,422)

    def test_cross_document_evidence_rejected(self):
        body=packet(SINGLE).model_dump(mode='json'); body['evidence'][0]['source_document_id']='other'
        r=self.client.post('/api/reconstruct',json={'packet':body,'decoder_mode':'constrained','profile':'m4-development-v1'})
        self.assertEqual(r.status_code,422)

    def test_live_pdf_ocr_extract_to_reconstruct(self):
        from test_m31_ocr_and_b2 import synthetic_pdf
        from backend.services.ocr_runtime import detect_runtime, OCRRuntimeError
        try: detect_runtime()
        except OCRRuntimeError as exc: self.skipTest('Live OCR unavailable: '+exc.code)
        extracted=self.client.post('/api/extract?pipeline=B1',content=synthetic_pdf(),headers={'X-Filename':'synthetic-m4.pdf','Content-Type':'application/pdf'})
        self.assertEqual(extracted.status_code,200)
        data=extracted.json()
        self.assertEqual(data['data']['source']['route'],'ocr')
        p=CanonicalTimetable.model_validate(data['data'])
        result=self.request(p)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['canonical_output']['packet']['source']['route'],'ocr')
        self.assertGreaterEqual(len(result.json()['canonical_output']['packet']['events']),1)

    def test_b2_and_b2h_packets_compatible(self):
        from test_m31_ocr_and_b2 import document
        from backend.services.extraction_adapter import adapt_baseline
        doc=document((SINGLE,(10,10,500,50),{}))
        for pipeline in ('B2','B2H-v1'):
            p=adapt_baseline(doc,pipeline)
            self.assertEqual(self.request(p).status_code,200)

    def test_native_pdf_extract_to_reconstruct(self):
        from test_m31_ocr_and_b2 import synthetic_pdf
        extracted=self.client.post('/api/extract?pipeline=evidence',content=synthetic_pdf(),headers={'X-Filename':'synthetic-m4.pdf','Content-Type':'application/pdf'})
        self.assertEqual(extracted.status_code,200)
        p=CanonicalTimetable.model_validate(extracted.json()['data'])
        self.assertEqual(p.source.route,'native_pdf')
        self.assertEqual(self.request(p).status_code,200)


if __name__=='__main__': unittest.main()
