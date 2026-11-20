"""Synthetic FR3 regression cases; no held-out labels or verifier authority."""
from io import BytesIO
from types import SimpleNamespace
import copy,unittest
from PIL import Image,ImageDraw
from test_s2a_ruled import fixture,output,contents,events
from backend.layout.ruled_capture import visible_grid
from backend.layout.ruled import cells,time_header
from backend.models.evidence import BoundingBox

def blank_grid(merged=False,local=False,partial=False,wide_day=False):
    image=Image.new('RGB',(1500,570),'white');draw=ImageDraw.Draw(image)
    left=350 if wide_day else 170;middle=900 if wide_day else 820
    for y in (80,150,330,510):draw.line((10,y,1490,y),fill='black',width=3)
    for x in (10,left,1490):draw.line((x,80,x,510),fill='black',width=3)
    if merged:
        draw.line((middle,80,middle,150),fill='black',width=3);draw.line((middle,330,middle,510),fill='black',width=3)
    else:draw.line((middle,80,middle,510),fill='black',width=3)
    if local:draw.line((left,240,middle if partial else 1490,240),fill='black',width=3)
    return image

def dated(day1='Monday',day2='Tuesday',date1='2026-01-05',date2='2026-01-06'):
    rows=[('24-HOUR TIMETABLE - SYNTHETIC TEST',20,15),('09:00-11:00',400,100),('11:00-13:00',960,100),('Physics',400,180),('Room A101',400,235),('Design Studies',400,370),('Room C303',400,425)]
    rows += [(date1,20,245),(date2,20,430)]
    if day1:rows.append((day1,20,195))
    if day2:rows.append((day2,20,380))
    return fixture(image=blank_grid(wide_day=True),rows=rows)

class FR3ReconstructionTests(unittest.TestCase):
    def test_weekday_and_date_in_same_cell_are_both_source_linked(self):
        p,s=dated();es=events(output(p,s));found={e['revision']['content']['course_name']:e for e in es}
        e=found['Physics'];c=e['revision']['content'];self.assertEqual((c['date'],c['day_of_week'],c['start_time']),('2026-01-05',1,'09:00'))
        self.assertTrue(next(o for o in e['revision']['observations'] if o['field_name']=='date')['evidence_ids'])
    def test_date_only_header_does_not_invent_weekday(self):
        p,s=dated(None,None);cs=contents(output(p,s));self.assertEqual(len(cs),2)
        self.assertEqual({c['date'] for c in cs},{'2026-01-05','2026-01-06'});self.assertTrue(all(c['day_of_week'] is None for c in cs))
    def test_unreadable_weekday_preserves_date_without_guess(self):
        p,s=dated('??', 'Tuesday');cs=contents(output(p,s));e=next(c for c in cs if c['course_name']=='Physics')
        self.assertEqual(e['date'],'2026-01-05');self.assertIsNone(e['day_of_week'])
    def test_printed_day_date_conflict_is_not_repaired_by_guess(self):
        p,s=dated(date1='2026-01-06');es=events(output(p,s));e=next(e for e in es if e['revision']['content']['course_name']=='Physics')
        self.assertEqual(e['revision']['content']['day_of_week'],1);self.assertEqual(e['revision']['content']['date'],'2026-01-06')
        self.assertNotEqual(e['calendar']['approval_status'],'APPROVED')
    def test_single_word_course_name_from_supported_cell(self):
        p,s=dated();self.assertIn('Physics',[c['course_name'] for c in contents(output(p,s))])
    def test_single_trailing_header_period_only(self):
        self.assertTrue(time_header({'text':'09:00 - 11:00.'}));self.assertFalse(time_header({'text':'09:00 - 11:00...'}))
    def test_short_explicit_meridiem_header_is_retained_without_24h_statement(self):
        p,s=fixture(rows=[('9:00 AM - 11:00 AM',200,100),('Monday',20,195),('Physics',210,180)])
        self.assertEqual(contents(output(p,s))[0]['start_time'],'09:00')
    def test_clock_without_am_pm_or_24h_authority_stays_unknown(self):
        p,s=fixture(rows=[('09:00 - 11:00.',200,100),('Monday',20,195),('Physics',210,180)])
        self.assertIsNone(contents(output(p,s))[0]['start_time'])
    def test_local_horizontal_rule_splits_stacked_merged_sessions(self):
        rows=[('24-HOUR SYNTHETIC TIMETABLE',20,15),('09:00-11:00',200,100),('11:00-13:00',850,100),('Monday',20,195),('Physics',210,170),('ROOM A101',210,205),('Data Science',210,255),('ROOM B202',210,290),('Tuesday',20,380),('Design Studies',210,370)]
        p,s=fixture(image=blank_grid(merged=True,local=True),rows=rows);result=output(p,s);cs=contents(result)
        byname={c['course_name']:c for c in cs};self.assertEqual(set(byname),{'Physics','Data Science','Design Studies'})
        for name in ('Physics','Data Science'):self.assertEqual((byname[name]['day_of_week'],byname[name]['start_time'],byname[name]['end_time']),(1,'09:00','13:00'))
        self.assertEqual((byname['Physics']['location'],byname['Data Science']['location']),('A101','B202'))
        self.assertTrue(any(c.get('row_fragment')==1 for c in result['canonical_output']['layout']['cells']))
    def test_partial_local_rule_remains_ambiguous(self):
        p,s=fixture(image=blank_grid(merged=True,local=True,partial=True));cs,issues=cells(s.pages[0]);self.assertTrue(any(i['code']=='UNSUPPORTED_PARTIAL_CELL_SPLIT' for i in issues));self.assertTrue(any(c['ambiguous'] for c in cs))
    def test_unaligned_underline_does_not_create_session_split(self):
        im=blank_grid(merged=True);ImageDraw.Draw(im).line((250,240,700,240),fill='black',width=3);self.assertEqual(visible_grid(im)['local_horizontal_rules'],[])
    def test_glyphs_crossing_open_separator_do_not_become_rule(self):
        im=blank_grid(merged=True);ImageDraw.Draw(im).rectangle((818,190,822,204),fill='black');word=SimpleNamespace(bbox=BoundingBox(x0=800,y0=185,x1=850,y1=210,coordinate_space='PIXELS'))
        g=visible_grid(im,[word]);i=g['xs'].index(820);self.assertGreater(g['raw_vertical_coverage'][1][i],.05);self.assertLessEqual(g['vertical_coverage'][1][i],.05)
    def test_persistent_partial_rule_is_not_masked_as_glyph(self):
        im=blank_grid(merged=True);ImageDraw.Draw(im).line((820,180,820,240),fill='black',width=3)
        word=SimpleNamespace(bbox=BoundingBox(x0=800,y0=175,x1=850,y1=245,coordinate_space='PIXELS'))
        small=[SimpleNamespace(bbox=BoundingBox(x0=200,y0=185,x1=230,y1=205,coordinate_space='PIXELS')) for _ in range(5)]
        g=visible_grid(im,[word,*small]);i=g['xs'].index(820);self.assertGreater(g['vertical_coverage'][1][i],.05);self.assertLess(g['vertical_coverage'][1][i],.96)
    def test_closed_rule_remains_closed_even_under_ocr_box(self):
        im=blank_grid();w=SimpleNamespace(bbox=BoundingBox(x0=800,y0=150,x1=850,y1=330,coordinate_space='PIXELS'));g=visible_grid(im,[w]);self.assertGreaterEqual(g['vertical_coverage'][1][g['xs'].index(820)],.96)
    def test_local_rule_contract_rejects_invalid_coordinates(self):
        p,s=fixture();g=copy.deepcopy(s.pages[0].grid);g['local_horizontal_rules']=[{'y':99999,'x0':170,'x1':1490}]
        with self.assertRaises(ValueError):cells(s.pages[0].model_copy(update={'grid':g}))

    def test_scaled_rules_keep_local_session_boundary(self):
        for scale in (1,3):
            with self.subTest(scale=scale):
                g=visible_grid(blank_grid(merged=True,local=True).resize((1500*scale,570*scale)))
                self.assertEqual(len(g['local_horizontal_rules']),1);self.assertEqual(len(g['ys']),4)
                self.assertAlmostEqual(g['local_horizontal_rules'][0]['y']/scale,240,delta=2)
    def token_data(self,text='Physics',left=210,top=180):
        return {'text':[text],'left':[left],'top':[top],'width':[150],'height':[20],'block_num':[1],'par_num':[1],'line_num':[1],'conf':[90]}
    def test_empty_header_retry_keeps_original_words_and_maps_crop(self):
        from backend.layout.ruled_capture import recover_empty_header
        image=blank_grid();data=self.token_data();before=copy.deepcopy(data);calls=[]
        def recognize(crop):calls.append(crop.size);return self.token_data('09:00-11:00',200,20),{'config':'SYNTHETIC_TEST_ONLY'}
        result,runtime=recover_empty_header(image,data,{},recognize)
        self.assertEqual(data,before);self.assertEqual(len(calls),1);self.assertEqual(result['text'],['Physics','09:00-11:00'])
        crop=runtime['s2a_header_retry']['crop'];self.assertEqual(result['left'][1],200+crop[0]);self.assertEqual(result['top'][1],20+crop[1])
        self.assertFalse(runtime['s2a_header_retry']['replaces_existing_words']);self.assertNotEqual(result['block_num'][0],result['block_num'][1])
    def test_existing_header_reading_is_never_replaced(self):
        from backend.layout.ruled_capture import recover_empty_header
        data=self.token_data('??:00-11:00',210,100)
        def forbidden(*a):self.fail('Existing raw header must not be overwritten by a retry')
        result,runtime=recover_empty_header(blank_grid(),data,{},forbidden);self.assertIs(result,data)
    def test_header_retry_failure_preserves_existing_ocr(self):
        from backend.layout.ruled_capture import recover_empty_header
        from backend.services.ocr_runtime import OCRRuntimeError
        data=self.token_data()
        def failing(*a):raise OCRRuntimeError('SYNTHETIC_FAILURE','Synthetic diagnostic')
        result,runtime=recover_empty_header(blank_grid(),data,{},failing);self.assertIs(result,data);self.assertEqual(runtime['s2a_header_retry']['error_code'],'SYNTHETIC_FAILURE')
    def test_outside_crop_words_cannot_gain_source_coordinates(self):
        from backend.layout.ruled_capture import recover_empty_header
        data=self.token_data();result,runtime=recover_empty_header(blank_grid(),data,{},lambda im:(self.token_data('09:00-11:00',-20,0),{}))
        self.assertEqual(result['text'],data['text']);self.assertEqual(runtime['s2a_header_retry']['added_words'],0)

    def test_period_number_above_range_is_not_a_clock_value(self):
        def w(text,y):return SimpleNamespace(text=text,bbox=SimpleNamespace(y0=y,y1=y+12))
        c={'text':'2 09:00-11:00','words':[w('2',90),w('09:00-11:00',120)]}
        self.assertTrue(time_header(c));c['words'][0]=w('2',120);self.assertFalse(time_header(c))
    def test_period_label_does_not_block_missing_clock_retry_or_duplicate(self):
        from backend.layout.ruled_capture import recover_empty_header
        image=blank_grid();data=self.token_data('2',210,90)
        g=visible_grid(image);crop=(g['xs'][0]+g['rule_inset'],g['ys'][0]+g['rule_inset'])
        extra={'text':['2','09:00-11:00'],'left':[210-crop[0],200],'top':[90-crop[1],38],'width':[150,150],'height':[20,20],'block_num':[1,1],'par_num':[1,1],'line_num':[1,2],'conf':[90,90]}
        result,runtime=recover_empty_header(image,data,{},lambda im:(extra,{}));self.assertEqual(result['text'].count('2'),1);self.assertIn('09:00-11:00',result['text']);self.assertEqual(runtime['s2a_header_retry']['added_words'],1)
