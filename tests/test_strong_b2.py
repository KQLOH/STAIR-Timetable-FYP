"""Invented source-word geometry; no private source answers."""
import unittest
from PIL import Image,ImageDraw
from backend.baselines.strong_b2 import Word,lines,predict,colored_cells,ruled_cells,course_anchors
from backend.models.evidence import SourceDocument

SOURCE=SourceDocument(source_document_id='synthetic',content_sha256='a'*64,route='ocr',media_type='image/png',content_hash_basis='original_bytes')
def fixture(scale=1,rows=None,vertical=False):
    words=[]
    def add(text,x,y):
        for word in text.split():
            words.append(Word(str(len(words)),word,tuple(v*scale for v in (x,y,x+len(word)*6,y+10)),.9));x+=len(word)*6+5
    add('Mon',10,120);add('Tue',10,310);add('08:00',150,30);add('09:00',250,30);add('10:00',350,30)
    rows=rows or ['XY1234 (L)','DK Q','Alex River','09:00 AM - 10:00 AM']
    for i,row in enumerate(rows):add(row,205,115+i*20)
    return {'page':1,'width':600*scale,'height':450*scale,'words':words,'cells':[tuple(v*scale for v in (200,100,400,225))]}

class StrongB2Tests(unittest.TestCase):
    def test_scale_and_determinism(self):
        expected=None
        for scale in (1,2,4):
            p=fixture(scale);r=predict(SOURCE,[p]);self.assertEqual(len(r.events),1)
            value=r.events[0].revision.content
            self.assertEqual((value.course_code,value.location,value.lecturer,value.day_of_week,value.start_time),('XY1234','DK Q','Alex River',1,'09:00'))
            self.assertEqual(r.model_dump(),predict(SOURCE,[p]).model_dump())
            if expected:self.assertEqual(value,expected)
            expected=value
    def test_wrapped_course_and_lecturer(self):
        r=predict(SOURCE,[fixture(rows=['XY','1234 (P)','DK Q','Alex','River','09:30 AM - 10:30 AM'])])
        self.assertEqual(r.events[0].revision.content.course_code,'XY1234')
        self.assertEqual(r.events[0].revision.content.lecturer,'Alex River')
    def test_multiple_sessions(self):
        p=fixture(rows=['XY1234 (L)','DK Q','Alex','09:00 AM - 10:00 AM','ZZ2345 (T)','R123','Blair','10:00 AM - 11:00 AM'])
        p['cells']=[(200,100,500,300)]
        self.assertEqual(len(predict(SOURCE,[p]).events),2)
    def test_missing_fields_retained(self):
        r=predict(SOURCE,[fixture(rows=['XY1234 (L)'])]);e=r.events[0]
        self.assertIsNone(e.revision.content.lecturer)
        self.assertIsNone(e.revision.content.teaching_weeks)
    def test_no_course_no_event(self):
        self.assertEqual(len(predict(SOURCE,[fixture(rows=['Holiday','No classes'])]).events),0)
    def test_unknown_room(self):
        r=predict(SOURCE,[fixture(rows=['XY1234 (L)','TBA','Alex','09:00 AM - 10:00 AM'])])
        self.assertIsNone(r.events[0].revision.content.location)
    def test_midpoint_day_ownership(self):
        p=fixture();p['words']=[Word(w.id,w.text,(w.box[0],w.box[1]+190,w.box[2],w.box[3]+190),w.confidence) if float(w.box[0])>=200 and w.box[1]>100 else w for w in p['words']]
        p['cells']=[(200,290,400,415)]
        self.assertEqual(predict(SOURCE,[p]).events[0].revision.content.day_of_week,2)
    def test_weekday_columns(self):
        p=fixture();p['words']=[Word(w.id,w.text,(w.box[1],w.box[0],w.box[3],w.box[2]),w.confidence) for w in p['words']]
        p['width'],p['height']=p['height'],p['width'];p['cells']=[(100,200,225,400)]
        self.assertEqual(predict(SOURCE,[p]).events[0].revision.content.day_of_week,1)
    def test_colored_adjacent_cells_and_text_holes(self):
        im=Image.new('RGB',(400,300),'white');d=ImageDraw.Draw(im)
        d.rectangle((30,50,150,200),fill='green');d.rectangle((152,50,300,200),fill='green')
        d.text((40,80),'TEXT',fill='white')
        self.assertEqual(len(colored_cells(im)),2)
    def test_duplicate_suppression(self):
        p=fixture(rows=['XY1234 (L)','DK Q','Alex','09:00 AM - 10:00 AM']*2);p['cells']=[(200,100,500,300)]
        r=predict(SOURCE,[p]);self.assertEqual(len(r.events),1)
        self.assertIn('B2_STRONG_DUPLICATE_SUPPRESSED',[x.code for x in r.issues])
    def test_no_header_guess(self):
        p=fixture();p['words']=[w for w in p['words'] if w.text not in ('Mon','Tue')]
        self.assertFalse(predict(SOURCE,[p]).events)

    def test_ambiguous_room_keeps_literal(self):
        e=predict(SOURCE,[fixture(rows=['XY1234 (L)','Q?12','Alex','09:30 AM - 10:30 AM'])]).events[0]
        o=next(o for o in e.revision.observations if o.field_name=='location')
        self.assertEqual(o.state.value,'UNRESOLVED');self.assertEqual(o.raw_value,'Q?12')
    def test_explicit_section_and_weeks(self):
        p=fixture(rows=['XY1234 (L)','DK Q','Alex','09:00 AM - 10:00 AM','Section: G2 Weeks: 1-3'])
        p['cells']=[(200,100,550,260)]
        c=predict(SOURCE,[p]).events[0].revision.content
        self.assertEqual(c.section,'G2');self.assertEqual(c.teaching_weeks,(1,2,3))
        self.assertIsNone(c.excluded_weeks)
    def test_clock_noise_not_a_lecturer(self):
        e=predict(SOURCE,[fixture(rows=['XY1234 (L)','DK Q','Alex','900AM-1000PM'])]).events[0]
        self.assertEqual(e.revision.content.lecturer,'Alex')
    def test_merged_geometry_time_axis(self):
        p=fixture(rows=['XY1234 (L)','DK Q','Alex']);p['cells']=[(100,100,300,225)]
        c=predict(SOURCE,[p]).events[0].revision.content
        self.assertEqual((c.start_time,c.end_time),('08:00','10:00'))
    def test_word_evidence_and_default_safety(self):
        r=predict(SOURCE,[fixture()]);e=r.events[0]
        self.assertEqual(e.human_verification.status,'NOT_VERIFIED')
        known={s.evidence_id for s in r.evidence}
        self.assertTrue(all(set(o.evidence_ids)<=known for o in e.revision.observations))

    def test_invalid_ocr_clock_is_not_an_axis_value(self):
        p=fixture(rows=['XY1234 (L)','DK Q','Alex'])
        p['words'].append(Word('noise','40:00',(450,30,480,40),.9))
        r=predict(SOURCE,[p])
        self.assertTrue(all(e.revision.content.end_time!='40:00' for e in r.events))

    def test_venue_code_does_not_start_a_session(self):
        for room in ('AB123','PQ999','TA255','PA103'):
            p=fixture(rows=['XY1234 (L)',room,'Alex River','09:00 AM - 10:00 AM'])
            r=predict(SOURCE,[p]);self.assertEqual(len(r.events),1)
            self.assertEqual(r.events[0].revision.content.location,room)
            self.assertEqual(r.events[0].revision.content.course_code,'XY1234')
    def test_room_shaped_legitimate_course_is_preserved(self):
        for course in ('TA255','PA103','ZZZ-9876Q'):
            e=predict(SOURCE,[fixture(rows=[course+' (L)','PQ999','Alex','09:00 AM - 10:00 AM'])]).events[0]
            self.assertEqual(e.revision.content.course_code,course)
    def test_multiple_sessions_with_course_shaped_venues(self):
        p=fixture(rows=['XY1234 (L)','AB123','Alex','09:00 AM - 10:00 AM','ZZ2345 (T)','PQ999','Blair','10:00 AM - 11:00 AM'])
        p['cells']=[(200,100,500,300)]
        r=predict(SOURCE,[p]);self.assertEqual(len(r.events),2)
        self.assertEqual([e.revision.content.location for e in r.events],['AB123','PQ999'])
    def test_unlabelled_bare_identifier_remains_ambiguous(self):
        e=predict(SOURCE,[fixture(rows=['AB123','Alex','09:00 AM - 10:00 AM'])]).events[0]
        self.assertIsNone(e.revision.content.course_code)
        self.assertEqual(next(o for o in e.revision.observations if o.field_name=='course_code').state.value,'UNRESOLVED')
    def test_course_name_only_event(self):
        e=predict(SOURCE,[fixture(rows=['Applied Mechanics (L)','PQ999','Alex','09:00 AM - 10:00 AM'])]).events[0]
        self.assertIsNone(e.revision.content.course_code)
        self.assertEqual(e.revision.content.course_name,'Applied Mechanics')
        self.assertTrue(e.revision.core_fields_present)
    def test_wrapped_second_course(self):
        p=fixture(rows=['XY1234 (L)','AB123','Alex','09:00 AM - 10:00 AM','ZZ','2345 (T)','PQ999','Blair','10:00 AM - 11:00 AM'])
        p['cells']=[(200,100,500,350)]
        self.assertEqual([e.revision.content.course_code for e in predict(SOURCE,[p]).events],['XY1234','ZZ2345'])
    def test_observed_uncolored_merged_cell(self):
        im=Image.new('RGB',(500,350),'white');d=ImageDraw.Draw(im)
        for y in (40,160,300):d.line((20,y,480,y),fill='black',width=2)
        for x in (20,480):d.line((x,40,x,300),fill='black',width=2)
        d.line((250,40,250,160),fill='black',width=2)
        cells=ruled_cells(im);self.assertEqual(len(cells),3)
        self.assertTrue(any(b[2]-b[0]>400 and b[1]>150 for b in cells))

    def test_broken_previous_clock_does_not_drop_next_course(self):
        p=fixture(rows=['XY1234 (L)','AB123','Alex','900AM-1000PM','ZZ2345','PQ999','Blair','10:00 AM - 11:00 AM'])
        p['cells']=[(200,100,500,300)]
        r=predict(SOURCE,[p]);self.assertEqual(len(r.events),2)
        self.assertIsNone(r.events[1].revision.content.course_code)
        self.assertEqual(next(o for o in r.events[1].revision.observations if o.field_name=='course_code').raw_value,'ZZ2345')

    def test_distant_type_cannot_certify_bare_identifier(self):
        p=fixture(rows=['AB123','Alex','09:00 AM - 10:00 AM','(L)'])
        e=predict(SOURCE,[p]).events[0]
        self.assertIsNone(e.revision.content.course_code)
        self.assertIsNone(e.revision.content.location)
    def test_leading_staff_text_cannot_promote_room_to_course(self):
        e=predict(SOURCE,[fixture(rows=['Alex AB123','Blair','09:00 AM - 10:00 AM'])]).events[0]
        self.assertIsNone(e.revision.content.course_code)
        self.assertIsNone(e.revision.content.location)
