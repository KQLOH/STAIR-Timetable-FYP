"""Invented course-name-only grids, not research accuracy labels."""
import unittest
from types import SimpleNamespace
from test_s2a_ruled import fixture,output,contents
from backend.layout.ruled import weekday_cells,ordered

ROWS=[('24-HOUR SYNTHETIC TIMETABLE - TEST DATA',20,15),('09:00-11:00',200,100),('11:00-13:00',850,100),('Monday',20,195),('Applied Computing',210,180),('ROOM A101',210,235),('Tuesday',20,380),('Data Science',210,370),('ROOM C303',210,425),('Discrete Mathematics',850,180),('ROOM B202',850,235)]
class NamedGridTests(unittest.TestCase):
    def test_name_only_cells_recover_identity_day_and_time(self):
        p,s=fixture(rows=ROWS);events=contents(output(p,s));self.assertEqual(len(events),3)
        found={e['course_name']:e for e in events};self.assertEqual(set(found),{'Applied Computing','Data Science','Discrete Mathematics'})
        self.assertEqual((found['Data Science']['day_of_week'],found['Data Science']['start_time'],found['Data Science']['end_time']),(2,'09:00','11:00'))
        self.assertTrue(all(e['course_code'] is None and e['recurrence'] is None and e['date'] is None for e in events))
    def test_wrapped_title_and_explicit_room_keep_roles(self):
        rows=[x for x in ROWS if x[0]!='Applied Computing']+[('Environmental',210,180),('Systems',210,212)]
        p,s=fixture(rows=rows);e=next(e for e in contents(output(p,s)) if e['course_name']=='Environmental Systems');self.assertEqual(e['location'],'A101')
    def test_labeled_course_name_does_not_include_label(self):
        rows=[('Course name: Design Studies',x,y) if text=='Applied Computing' else (text,x,y) for text,x,y in ROWS]
        p,s=fixture(rows=rows);self.assertIn('Design Studies',[e['course_name'] for e in contents(output(p,s))])
    def test_name_without_day_axis_is_not_promoted(self):
        p,s=fixture(rows=[(text,x,y) for text,x,y in ROWS if text not in {'Monday','Tuesday'}]);events=contents(output(p,s))
        self.assertTrue(all(not e['course_name'] for e in events))
    def test_break_and_person_metadata_are_not_course_names(self):
        for replacement in ('Lunch Break','Dr. Synthetic Person','Room A101'):
            with self.subTest(replacement=replacement):
                rows=[(replacement,x,y) if text=='Applied Computing' else (text,x,y) for text,x,y in ROWS]
                p,s=fixture(rows=rows);self.assertNotIn(replacement,[e['course_name'] for e in contents(output(p,s))])
    def test_unknown_meridiem_remains_unresolved(self):
        p,s=fixture(rows=[(text,x,y) for text,x,y in ROWS if not text.startswith('24-HOUR')]);events=contents(output(p,s))
        self.assertEqual(sum(bool(e['course_name']) for e in events),3);self.assertTrue(all(e['start_time'] is None and e['end_time'] is None for e in events))
    def test_correlated_short_weekday_axis_only(self):
        cs=[{'col':0,'ambiguous':False,'text':v,'row':i} for i,v in enumerate(('Mo','T u','We','Th','Fr'))]
        self.assertEqual(list(weekday_cells(cs).values()),[1,2,3,4,5])
        self.assertEqual(weekday_cells(cs[:1]),{})
        self.assertEqual(weekday_cells([cs[2],dict(cs[0],row=3),dict(cs[1],row=4)]),{})
    def test_low_height_separator_keeps_horizontal_order(self):
        def w(text,x,y,h):return SimpleNamespace(text=text,evidence_id=text,bbox=SimpleNamespace(x0=x,x1=x+20,y0=y,y1=y+h))
        words=[w('09:00',10,10,20),w('11:00',70,10,20),w('-',45,18,3)]
        self.assertEqual([w.text for w in ordered(words)],['09:00','-','11:00'])
    def test_field_provenance_and_review_gates_remain(self):
        p,s=fixture(rows=ROWS);result=output(p,s);all_events=result['canonical_output']['packet']['events']+result['canonical_output']['unresolved_candidates']
        for event in all_events:
            obs=next(o for o in event['revision']['observations'] if o['field_name']=='course_name')
            self.assertTrue(obs['evidence_ids']);self.assertEqual(event['human_verification']['status'],'NOT_VERIFIED')
            self.assertEqual(event['calendar']['approval_status'],'NOT_APPROVED')

    def test_labeled_room_code_does_not_become_name_only_course_identity(self):
        rows=[('ROOM XY255',x,y) if text=='ROOM A101' else (text,x,y) for text,x,y in ROWS]
        p,s=fixture(rows=rows);events=contents(output(p,s));e=next(e for e in events if e['course_name']=='Applied Computing')
        self.assertIsNone(e['course_code']);self.assertEqual(e['location'],'XY255')
        self.assertFalse(any(e['course_code']=='XY255' for e in events))

    def test_separate_unlabeled_person_paragraph_is_not_course_title(self):
        rows=[x for x in ROWS if x[0]!='ROOM A101']+[('Invented Person',210,240)]
        p,s=fixture(rows=rows);events=contents(output(p,s));self.assertIn('Applied Computing',[e['course_name'] for e in events])
        self.assertTrue(all('Invented Person' not in (e['course_name'] or '') for e in events))
