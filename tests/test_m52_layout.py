"""Independent synthetic layouts only. No gold, baseline package or private files."""
from hashlib import sha256
from io import BytesIO
import json
import base64
import subprocess
import socket
import threading
import time
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from fastapi.testclient import TestClient
from PIL import Image,ImageDraw,ImageFont
from backend.main import app
from backend.models.evidence import SourceDocument,EvidenceSpan,BoundingBox
from backend.models.event import CanonicalTimetable
from backend.layout.service import reconstruct_layout,assess_layout,PROFILE,layout_graph
from backend.reconstruction.service import reconstruct


def packet(rows):
    text='\n'.join(r[0] for r in rows)
    source=SourceDocument(source_document_id='synthetic-m52',content_sha256=sha256(text.encode()).hexdigest(),
        route='ocr',media_type='image/png',content_hash_basis='decoded_text_utf8')
    spans=tuple(EvidenceSpan(evidence_id=f'synthetic-span-{i}',source_document_id=source.source_document_id,route='ocr',
        raw_text=row[0],page=row[2] if len(row)>2 else 1,bbox=BoundingBox(x0=row[1][0],y0=row[1][1],x1=row[1][2],y1=row[1][3],coordinate_space='PIXELS') if row[1] else None,
        confidence=None,transformations=('SYNTHETIC_TEST_DATA',)) for i,row in enumerate(rows))
    return CanonicalTimetable(source=source,evidence=spans)


def simple(code='BACS3413',name='PROJECT II',day='MONDAY',room='B101',x=30,y=20):
    return [(f'{code} {name}',(x,y,x+340,y+24)),(f'{day} 10:00 - 12:00',(x,y+60,x+350,y+84)),(f'ROOM {room}',(x,y+120,x+220,y+144))]


def contents(result):
    c=result['canonical_output'];return [e['revision']['content'] for e in c['packet']['events']+c['unresolved_candidates']]


def synthetic_png(grid=False):
    image=Image.new('RGB',(1400,550 if grid else 350),'white');d=ImageDraw.Draw(image);font=ImageFont.load_default(size=30)
    if not grid:
        for i,t in enumerate(['SOFTWARE ENGINEERING TIMETABLE','BACS3413 PROJECT II','MONDAY 10:00 - 12:00','ROOM B101']):d.text((35,20+70*i),t,fill='black',font=font)
    else:
        for i,t in enumerate(['24-HOUR TIMETABLE','CS101 NETWORKS','MONDAY 09:00 - 11:00 LECTURE','ROOM A201','MA202 CALCULUS','TUESDAY 14:00 - 16:00 TUTORIAL','ROOM C102']):
            d.text((35,10+72*i),t,fill='black',font=font)
        for y in [0,55,300,545]:d.line((5,y,1390,y),fill='black',width=2)
        for x in [5,1390]:d.line((x,0,x,545),fill='black',width=2)
    b=BytesIO();image.save(b,format='PNG');return b.getvalue()


class LayoutTests(unittest.TestCase):
    def run_layout(self,rows):return reconstruct_layout(packet(rows))[0]

    def test_four_line_root_cause_and_fix(self):
        p=packet([('SOFTWARE ENGINEERING TIMETABLE',(30,0,600,20))]+simple(y=65))
        old=reconstruct(p,'constrained')['canonical_output'];self.assertEqual(len(old['unresolved_candidates']),3)
        r,g,_=reconstruct_layout(p);self.assertEqual(len(r['canonical_output']['packet']['events']),1)
        c=contents(r)[0];self.assertEqual((c['course_code'],c['course_name'],c['day_of_week'],c['start_time'],c['end_time'],c['location']),('BACS3413','PROJECT II',1,'10:00','12:00','B101'))
        self.assertTrue(any(i['code']=='DOCUMENT_HEADING_EXCLUDED' for i in r['canonical_output']['layout']['issues']))
        for key in ['date','session_type','source_timezone','teaching_weeks','recurrence','cohort_id']:self.assertIsNone(c[key])

    def test_scale_and_translation_invariance(self):
        rows=simple('XYC777','Distributed Systems','WEDNESDAY','N-42')
        a=contents(self.run_layout(rows))[0]
        transformed=[(t,tuple(v*2.3+125 for v in b)) for t,b in rows]
        self.assertEqual(a,contents(self.run_layout(transformed))[0])

    def test_raw_geometry_and_alias_provenance(self):
        p=packet(simple());before=p.model_dump_json();r,_,_=reconstruct_layout(p)
        byid={s['evidence_id']:s for s in r['canonical_output']['packet']['evidence']}
        for s in p.evidence:
            after=byid[r['execution_metadata']['capture_aliases'][s.evidence_id]]
            self.assertEqual(after['raw_text'],s.raw_text);self.assertEqual(after['bbox'],s.bbox.model_dump(mode='json'));self.assertIsNone(after['confidence'])
        self.assertEqual(p.model_dump_json(),before)

    def test_adjacent_courses_do_not_merge(self):
        c=contents(self.run_layout(simple('CS101','Networks',y=10)+simple('MA202','Calculus','TUESDAY','C102',y=220)))
        self.assertEqual(len(c),2);self.assertEqual({v['course_code'] for v in c},{'CS101','MA202'})
        self.assertEqual({v['location'] for v in c},{'B101','C102'})

    def test_parallel_columns_ambiguous_line(self):
        rows=[('CS101 NETWORKS',(10,0,200,20)),('MA202 CALCULUS',(180,0,370,20)),('MONDAY 09:00-10:00',(80,40,320,60))]
        r=self.run_layout(rows);self.assertTrue(any(i['code']=='AMBIGUOUS_LINE_ASSOCIATION' for i in r['canonical_output']['layout']['issues']))
        self.assertFalse(any(v['course_code'] and v['start_time'] for v in contents(r)))

    def test_missing_geometry_is_not_adjacency(self):
        r=self.run_layout([(t,None) for t,_ in simple()]);self.assertEqual(len(r['canonical_output']['packet']['events']),0)
        self.assertTrue(any(v['course_code']=='BACS3413' for v in contents(r)))

    def test_no_cross_page_join(self):
        r=self.run_layout([(simple()[0][0],simple()[0][1],1)]+[(t,b,2) for t,b in simple()[1:]])
        self.assertFalse(any(v['course_code'] and v['start_time'] for v in contents(r)))

    def test_grid_axis_cells_and_multiline_venues(self):
        rows=[('24-hour timetable',(0,0,450,15)),('09:00-11:00',(150,30,340,50)),('14:00-16:00',(370,30,560,50)),('Monday',(0,90,100,180)),
            ('CS101 Networks',(155,100,320,120)),('Room B101',(155,140,300,160)),('MA202 Calculus',(375,100,550,120)),('Room C102',(375,140,530,160))]
        r=self.run_layout(rows);c=contents(r)
        self.assertEqual(len(c),2);self.assertEqual({v['start_time'] for v in c},{'09:00','14:00'});self.assertEqual({v['day_of_week'] for v in c},{1})
        self.assertTrue(all(v['location'] for v in c));self.assertTrue(any(i['code']=='TABLE_AXIS_REGION_CANDIDATE' for i in r['canonical_output']['layout']['issues']))

    def test_spanning_header_ambiguity(self):
        rows=[('09:00-11:00',(100,0,260,20)),('13:00-15:00',(230,0,400,20)),('CS101',(180,50,310,70))]
        r=self.run_layout(rows);self.assertTrue(any(i['code']=='AMBIGUOUS_SPANNING_HEADER' for i in r['canonical_output']['layout']['issues']))
        self.assertIsNone(contents(r)[0]['start_time'])

    def test_merged_day_header_and_repeated_course_different_days(self):
        rows=[('24-hour timetable',(0,0,400,15)),('09:00-11:00',(150,30,350,50)),('Monday',(0,80,90,160)),('Tuesday',(0,180,90,240)),
              ('CS101 Lecture',(160,100,340,120)),('CS101 Tutorial Group G2',(160,190,390,210))]
        r=self.run_layout(rows);c=contents(r);self.assertEqual(len(c),2);self.assertEqual({v['day_of_week'] for v in c},{1,2})
        self.assertEqual({v['section'] for v in c if v['section']},{'G2'})

    def test_twelve_hour_axis_not_promoted(self):
        r=self.run_layout([('01:00-02:00',(150,0,350,20)),('Monday',(0,40,90,80)),('CS101',(160,50,300,70))])
        self.assertIsNone(contents(r)[0]['start_time']);self.assertNotIn('13:00',json.dumps(r))
        self.assertIn('TIME_AXIS_MERIDIEM_UNRESOLVED',json.dumps(r))

    def test_single_axis_label_not_a_range(self):
        r=self.run_layout([('01:00',(100,0,200,20)),('CS101',(110,60,270,80))]);self.assertIsNone(contents(r)[0]['start_time'])
        self.assertIn('SINGLE_TIME_AXIS_LABEL_UNSUPPORTED',json.dumps(r))

    def test_compound_distinct_sessions_and_groups(self):
        r=self.run_layout([('CS101 Monday Lecture Group A 09:00-11:00; CS101 Monday Tutorial Group B 13:00-15:00',(20,20,1100,50))])
        c=contents(r);self.assertEqual(len(c),2);self.assertEqual({v['section'] for v in c},{'A','B'})
        self.assertEqual({v['start_time'] for v in c},{'09:00','13:00'})

    def test_unknown_venue_and_separate_lecturer(self):
        r=self.run_layout(simple(room='TBA')+[('Lecturer: Synthetic Tutor',(30,190,400,214))]);c=contents(r)[0]
        self.assertIsNone(c['location']);self.assertEqual(c['lecturer'],'Synthetic Tutor');self.assertIn('UNRESOLVED',json.dumps(r))

    def test_labelled_room_and_lecturer_ids_are_not_course_codes(self):
        r=self.run_layout(simple('CS101','NETWORKS',room='AB123')+[('Lecturer: ZZ999',(30,190,300,214))]);c=contents(r)
        self.assertEqual(len(c),1);self.assertEqual(c[0]['course_code'],'CS101');self.assertEqual(c[0]['location'],'AB123')
        self.assertEqual(c[0]['lecturer'],'ZZ999')

    def test_overlapping_courses_do_not_force_join(self):
        r=self.run_layout([('CS101',(10,10,210,40)),('MA202',(20,20,220,50)),('Room B101',(15,70,215,90))])
        self.assertFalse(any(v['course_code'] and v['location'] for v in contents(r)))

    def test_matched_b3_same_graph_and_scoring(self):
        p=packet(simple());a,ga,_=reconstruct_layout(p);b,gb,_=reconstruct_layout(p,'unconstrained')
        self.assertEqual(ga,gb);self.assertEqual(a['canonical_output']['base_score_digest'],b['canonical_output']['base_score_digest'])
        self.assertEqual(a['canonical_output']['components'][0]['assignments_visited'],b['canonical_output']['components'][0]['assignments_visited'])
        self.assertEqual(b['canonical_output']['method_id'],'B3-matched-layout-m52-v1')

    def test_policy_m6_gold_and_approval_stay_separate(self):
        p=packet(simple());r,_,_=reconstruct_layout(p);decisions=assess_layout(p,r);self.assertTrue(all(d.decision=='ABSTAIN' for d in decisions))
        for e in r['canonical_output']['packet']['events']:
            self.assertEqual(e['human_verification']['status'],'NOT_VERIFIED');self.assertEqual(e['calendar']['approval_status'],'NOT_APPROVED')
        with TestClient(app) as client:
            response=client.post('/api/calendar/v1/eligibility',json={'packet':r['canonical_output']['packet'],'reconstruction_status':r['canonical_output']['status']})
            self.assertEqual(response.status_code,200);self.assertTrue(all(e['actual_write_eligibility']=='BLOCKED' for e in response.json()['events']))

    def test_original_m4_path_is_reproducible(self):
        p=packet(simple());before=reconstruct(p,'constrained');reconstruct_layout(p);after=reconstruct(p,'constrained')
        self.assertEqual(before['canonical_digest'],after['canonical_digest']);self.assertEqual(len(before['canonical_output']['unresolved_candidates']),3)

    def test_variant_api_and_assessment_tamper(self):
        with TestClient(app) as c:
            p=packet(simple()).model_dump(mode='json');r=c.post('/api/reconstruct',json={'packet':p,'decoder_mode':'constrained','profile':PROFILE})
            self.assertEqual(r.status_code,200,r.text)
            body={'source_packet':p,'reconstruction':r.json()};a=c.post('/api/layout/assess',json=body);self.assertEqual(a.status_code,200,a.text)
            body['reconstruction']['canonical_output']['packet']['events'][0]['revision']['content']['location']='FAKE'
            self.assertEqual(c.post('/api/layout/assess',json=body).status_code,422)
            self.assertEqual(c.post('/api/review/ingest',json={}).status_code,403)

    def test_native_and_context_rejected(self):
        p=packet(simple()).model_copy(update={'source':packet(simple()).source.model_copy(update={'route':'native_pdf'})})
        with self.assertRaisesRegex(ValueError,'OCR_LAYOUT_ROUTE_REQUIRED'):reconstruct_layout(p)

    def test_empty_and_unreadable_evidence_retained(self):
        p=packet([('UNREADABLE ???',(10,0,200,20)),('Room TBA',(10,40,200,60))])
        r,_,_=reconstruct_layout(p);self.assertEqual(len(r['canonical_output']['packet']['evidence']),2)
        self.assertIn('UNCLASSIFIED_SOURCE_LINE',json.dumps(r));self.assertTrue(all(v['course_code'] is None for v in contents(r)))

    def test_request_bound_and_repeat_determinism(self):
        p=packet(simple());a,_,_=reconstruct_layout(p);b,_,_=reconstruct_layout(p)
        self.assertEqual(a['canonical_digest'],b['canonical_digest'])
        with self.assertRaisesRegex(ValueError,'LAYOUT_EVIDENCE_CAP'):reconstruct_layout(packet([('x',(0,i*20,100,i*20+10)) for i in range(257)]))

    def test_capture_span_aliases_do_not_change_canonical_digest(self):
        p=packet(simple());other=p.model_copy(update={'evidence':tuple(s.model_copy(update={'evidence_id':'recapture-'+s.evidence_id}) for s in p.evidence)})
        a,_,_=reconstruct_layout(p);b,_,_=reconstruct_layout(other)
        self.assertEqual(a['canonical_digest'],b['canonical_digest'])
        self.assertNotEqual(a['execution_metadata']['capture_aliases'],b['execution_metadata']['capture_aliases'])


class LiveLayoutTests(unittest.TestCase):
    def test_frontend_source_classification(self):
        from tests.test_m51_integration import NODE
        r=subprocess.run([NODE,str(Path(__file__).with_name('m52_frontend_checks.js'))],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_real_client_variant_pipeline(self):
        from backend.services.ocr_runtime import detect_runtime,OCRRuntimeError
        try:detect_runtime()
        except OCRRuntimeError as error:self.skipTest(error.code)
        result=verify_live_layout_client();self.assertEqual(result['source_label'],'OCR image upload');self.assertFalse(result['external_calendar'])

    def test_real_grid_client(self):
        from backend.services.ocr_runtime import detect_runtime,OCRRuntimeError
        try:detect_runtime()
        except OCRRuntimeError as error:self.skipTest(error.code)
        r=verify_live_layout_client(grid=True);events=r['reconstruction']['canonical_output']['packet']['events']
        self.assertEqual({e['revision']['content']['course_code'] for e in events},{'CS101','MA202'})
        self.assertTrue(all(e['revision']['content']['date'] is None for e in events))

    def test_real_four_line_ocr(self):
        from backend.services.ocr_runtime import detect_runtime,OCRRuntimeError
        try:detect_runtime()
        except OCRRuntimeError as error:self.skipTest(error.code)
        with TestClient(app) as c:
            p=c.post('/api/extract?pipeline=evidence',content=synthetic_png(),headers={'X-Filename':'synthetic-m52.png','Content-Type':'image/png'}).json()['data']
            response=c.post('/api/reconstruct',json={'packet':p,'decoder_mode':'constrained','profile':PROFILE});self.assertEqual(response.status_code,200,response.text)
            fields=contents(response.json())[0];self.assertEqual(fields['course_code'],'BACS3413');self.assertEqual(fields['course_name'],'PROJECT II')
            self.assertEqual(fields['location'],'B101');self.assertEqual(fields['start_time'],'10:00');self.assertIsNone(fields['date'])


def verify_live_layout_client(grid=False):
    import uvicorn
    from tests.test_m51_integration import NODE
    root=Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix='m52-test-',dir=root/'data/temp') as temp:
        fixture=Path(temp)/'fixture.json';fixture.write_text(json.dumps({'png':base64.b64encode(synthetic_png(grid)).decode()}))
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64);port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            deadline=time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
            if not server.started:raise AssertionError('Local synthetic API did not start')
            r=subprocess.run([NODE,str(root/'tests/m52_frontend_checks.js'),str(fixture),f'http://127.0.0.1:{port}'],capture_output=True,text=True,encoding='utf-8',timeout=120)
            if r.returncode:raise AssertionError(r.stderr[-2500:])
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            server.should_exit=True;thread.join(timeout=10);sock.close()
            if thread.is_alive():raise AssertionError('Local test API did not stop')
