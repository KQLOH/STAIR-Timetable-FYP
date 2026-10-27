"""S2b synthetic contracts and actual synthetic OCR; no real screenshot access."""
import copy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
from backend.main import app
from backend.layout.block_models import PROFILE,BlockEvidence,MAX_PIXELS
from backend.layout.block_capture import BlockCollector,detect_regions,prepare_crop
from backend.layout.block_grid import (reconstruct_blocks,assess_blocks,header_axis,
    block_graph,CONFIG)
from backend.models.event import CanonicalTimetable
from backend.reconstruction.service import reconstruct
from backend.layout.service import reconstruct_layout
from tests.s2b_fixtures import packet
from tests.s1_functional_audit import pdf

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/synthetic_samples/s2b_preparation'


def run(p,s,mode='constrained'):return reconstruct_blocks(p,s,mode)[0]
def events(result):
    c=result['canonical_output'];return c['packet']['events']+c['unresolved_candidates']
def values(result):return [e['revision']['content'] for e in events(result)]


class BlockContractTests(unittest.TestCase):
    def test_semantic_fields_date_type_group_venue_staff(self):
        p,s=packet();r=run(p,s);v=values(r)[0]
        self.assertEqual(tuple(v[k] for k in ['course_code','course_name','day_of_week','date','start_time','end_time','session_type','location','section','lecturer']),
            ('QA1701','Graph Study',1,'2033-02-07','09:00','11:00','Lecture','Q1','G9','TEST ACTOR'))
        self.assertEqual(len(r['canonical_output']['packet']['events']),1)

    def test_exact_type_word_link_not_course_letter(self):
        p,s=packet([dict(code='LL1701',kind='L',start=9,end=11,row=0,col=1,span=2)])
        r=run(p,s);e=events(r)[0];obs=next(o for o in e['revision']['observations'] if o['field_name']=='session_type')
        spans={v['evidence_id']:v for v in r['canonical_output']['packet']['evidence']}
        raw=[spans[i]['raw_text'] for i in obs['evidence_ids'] if i.startswith('s2b-word-')]
        self.assertEqual(raw,['(L)'])

    def test_neighbor_fields_do_not_leak(self):
        a=dict(code='QA1701',kind='L',start=9,end=11,row=0,col=1,span=2,venue='Q1',group='G1')
        b=dict(code='RX2840',kind='P',start=11,end=12,row=1,col=3,span=1,venue='R2',group='G2')
        p,s=packet([a,b]);r=run(p,s);got={v['course_code']:v for v in values(r)}
        self.assertEqual((got['QA1701']['location'],got['QA1701']['section'],got['QA1701']['day_of_week']),('Q1','G1',1))
        self.assertEqual((got['RX2840']['location'],got['RX2840']['section'],got['RX2840']['day_of_week']),('R2','G2',2))
        ws={w.evidence_id:w for page in s.pages for w in page.words}
        for e in events(r):
            ids={ws[i].region_id for o in e['revision']['observations'] for i in o['evidence_ids'] if i in ws and ws[i].region_id}
            self.assertEqual(len(ids),1)

    def test_two_header_rows_and_nonuniform_column_geometry(self):
        p,s=packet();axis,_=header_axis(s.pages[0]);self.assertTrue(axis['supported'])
        self.assertEqual([(c['upper'].text,c['lower'].text) for c in axis['columns'][:3]],[('8:00','9:00'),('9:00','10:00'),('10:00','11:00')])
        self.assertEqual(values(run(p,s))[0]['end_time'],'11:00')

    def test_bare_twelve_hour_range_unresolved(self):
        p,s=packet([dict(code='QA1701',kind='L',start=9,end=11,row=0,col=1,span=2,raw_time='9:00 - 11:00')])
        r=run(p,s);v=values(r)[0];self.assertIsNone(v['start_time']);self.assertIsNone(v['end_time'])
        self.assertFalse(r['canonical_output']['packet']['events'])

    def test_printed_axis_authority_can_support_bare_block_range(self):
        block=dict(code='QA1701',kind='L',start=9,end=11,row=0,col=1,span=2,raw_time='9:00 - 11:00')
        for settings in [{'explicit_headers':True},{'authority':True}]:
            p,s=packet([block],**settings);v=values(run(p,s))[0]
            self.assertEqual((v['start_time'],v['end_time']),('09:00','11:00'))

    def test_noon_transition_explicit_source(self):
        p,s=packet([dict(code='QA1701',kind='L',start=11,end=13,row=0,col=3,span=2)])
        v=values(run(p,s))[0];self.assertEqual((v['start_time'],v['end_time']),('11:00','13:00'))

    def test_conflicting_block_time_and_geometry_unresolved(self):
        p,s=packet([dict(code='QA1701',kind='L',start=9,end=11,row=0,col=1,span=2,raw_time='10:00 AM - 12:00 PM')])
        r=run(p,s);self.assertIsNone(values(r)[0]['start_time'])
        self.assertTrue(any('PRINTED_TIME_GEOMETRY_CONFLICT' in r['canonical_output']['graph']['groups'][0]['diagnostics'] for _ in [0]))

    def test_unreadable_header_keeps_direct_legible_interval(self):
        p,s=packet(header_unknown=True);v=values(run(p,s))[0]
        self.assertEqual((v['start_time'],v['end_time']),('09:00','11:00'))

    def test_unreadable_printed_interval_is_not_repaired(self):
        p,s=packet([dict(code='QA1701',kind='L',start=9,end=11,row=0,col=1,span=2,raw_time='Time TBA')],authority=True)
        self.assertIsNone(values(run(p,s))[0]['start_time'])

    def test_day_date_conflict_not_silently_corrected(self):
        p,s=packet(conflict_date=True);v=values(run(p,s))[0]
        self.assertIsNone(v['day_of_week']);self.assertIsNone(v['date'])

    def test_unknown_venue_and_missing_identity_retained(self):
        p,s=packet([dict(code='???',kind='L',start=9,end=11,row=0,col=1,span=2,venue='TBA')]);r=run(p,s);e=events(r)[0]
        self.assertIsNone(e['revision']['content']['course_code']);self.assertIsNone(e['revision']['content']['location'])
        states={o['field_name']:o['state'] for o in e['revision']['observations']};self.assertEqual(states['course_code'],'UNRESOLVED');self.assertEqual(states['location'],'UNRESOLVED')

    def test_touching_same_color_preserves_competing_anchors(self):
        a=dict(code='QA1701',kind='L',start=9,end=10,row=0,col=1,span=1)
        b=dict(code='RX2840',kind='P',start=10,end=11,row=0,col=2,span=1)
        p,s=packet([a,b],same_color=True);r=run(p,s)
        self.assertEqual({v['course_code'] for v in values(r)},{'QA1701','RX2840'})
        self.assertFalse(r['canonical_output']['packet']['events'])
        self.assertTrue(all(v['location'] is None for v in values(r)))

    def test_unsupported_axes_does_not_invent_day(self):
        p,s=packet(axes=False);r=run(p,s)
        self.assertFalse(r['canonical_output']['packet']['events']);self.assertIsNone(values(r)[0]['day_of_week'])

    def test_scale_and_token_order_do_not_change_field_values(self):
        expected=values(run(*packet()))
        for options in [{'scale':1.4},{'reverse':True}]:self.assertEqual(values(run(*packet(**options))),expected)

    def test_original_raw_evidence_and_genuine_null_confidence(self):
        p,s=packet();r=run(p,s);spans={x['evidence_id']:x for x in r['canonical_output']['packet']['evidence']}
        for source in p.evidence:
            target=spans[r['execution_metadata']['capture_aliases'][source.evidence_id]]
            self.assertEqual(target['raw_text'],source.raw_text);self.assertEqual(target['bbox'],source.bbox.model_dump())
        for w in s.pages[0].words:self.assertEqual(spans[w.evidence_id]['raw_text'],w.text);self.assertIsNone(spans[w.evidence_id]['confidence'])
        CanonicalTimetable.model_validate(r['canonical_output']['packet'])

    def test_source_parent_word_and_region_tampering_rejected(self):
        p,s=packet()
        with self.assertRaises(ValueError):run(p,s.model_copy(update={'content_sha256':'0'*64}))
        w=s.pages[0].words[0]
        for change in [{'evidence_id':'fake'},{'parent_line_id':'missing'}]:
            page=s.pages[0].model_copy(update={'words':(w.model_copy(update=change),)+s.pages[0].words[1:]})
            with self.assertRaises(ValueError):run(p,s.model_copy(update={'pages':(page,)}))

    def test_deterministic_matched_application_comparison(self):
        p,s=packet();a=run(p,s);b=run(p,s);u=run(p,s,'unconstrained')
        self.assertEqual(a['canonical_digest'],b['canonical_digest'])
        self.assertEqual(a['canonical_output']['base_score_digest'],u['canonical_output']['base_score_digest'])
        self.assertEqual(a['canonical_output']['candidate_graph_digest'],u['canonical_output']['candidate_graph_digest'])
        self.assertEqual(u['canonical_output']['method_id'],'B3-matched-app-s2b-v1')

    def test_original_m4_and_m52_results_unchanged(self):
        p,s=packet();a=reconstruct(p,'constrained')['canonical_digest'];b=reconstruct_layout(p)[0]['canonical_digest'];run(p,s)
        self.assertEqual(a,reconstruct(p,'constrained')['canonical_digest']);self.assertEqual(b,reconstruct_layout(p)[0]['canonical_digest'])

    def test_policy_and_calendar_statuses_not_promoted(self):
        p,s=packet();r=run(p,s);decisions=assess_blocks(p,s,r)
        self.assertTrue(all(d.decision.value=='ABSTAIN' for d in decisions))
        for e in events(r):
            self.assertEqual(e['human_review']['status'],'NOT_REVIEWED');self.assertEqual(e['human_verification']['status'],'NOT_VERIFIED')
            self.assertEqual(e['calendar']['approval_status'],'NOT_APPROVED');self.assertEqual(e['calendar']['sync_status'],'NOT_SUBMITTED')
        bad=copy.deepcopy(r);bad['canonical_output']['packet']['events'][0]['revision']['content']['location']='FAKE'
        with self.assertRaises(ValueError):assess_blocks(p,s,bad)

    def test_pixel_word_group_and_operation_caps(self):
        p,s=packet();raw=s.model_dump(mode='json');raw['pages'][0]['width']=MAX_PIXELS
        with self.assertRaises(ValueError):BlockEvidence.model_validate(raw)
        raw=s.model_dump(mode='json');raw['pages'][0]['words']*=1600
        with self.assertRaises(ValueError):BlockEvidence.model_validate(raw)
        with patch.dict(CONFIG,{'max_groups':0}):
            with self.assertRaisesRegex(ValueError,'GROUP_CAP'):run(p,s)
        with patch('backend.layout.block_grid.MAX_OPERATIONS',1):
            with self.assertRaisesRegex(ValueError,'OPERATION_CAP'):run(p,s)

    def test_color_components_arent_tied_to_one_palette(self):
        for color in [(24,130,97),(121,68,40),(43,60,146)]:
            image=Image.new('RGB',(400,240),'white');image.paste(color,(60,70,250,200))
            rs,*_=detect_regions(image,'synthetic',1);self.assertEqual(len(rs),1)
            b=rs[0].bbox;self.assertTrue(60<=b.x0<=62 and 70<=b.y0<=72 and 248<=b.x1<=250 and 198<=b.y1<=200)
            self.assertEqual(rs[0].boundary_status,'RECTANGULAR')

    def test_visible_separator_with_one_pixel_bridge_stays_separate(self):
        image=Image.new('RGB',(400,240),'white');image.paste((30,110,90),(50,50,150,160));image.paste((30,110,90),(152,50,252,160))
        image.paste((30,110,90),(149,159,153,160))
        rs,*_=detect_regions(image,'synthetic-bridge',1)
        self.assertEqual(len(rs),2);self.assertLess(rs[0].bbox.x1,rs[1].bbox.x0)
        self.assertTrue(all(r.boundary_status=='RECTANGULAR' for r in rs))


class BlockHTTPAndOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.client=TestClient(app);cls.client.__enter__()
    @classmethod
    def tearDownClass(cls):cls.client.__exit__(None,None,None)

    def test_strict_opt_in_profile_contracts(self):
        p,s=packet();q={'packet':p.model_dump(mode='json'),'layout_evidence':s.model_dump(mode='json'),'profile':PROFILE,'decoder_mode':'constrained'}
        self.assertEqual(self.client.post('/api/reconstruct',json=q).status_code,200)
        for override in [{'profile':'m4-development-v1'},{'profile':'ocr-ruled-grid-s2a-v1'},{'profile':'unknown'},{'layout_evidence':None}]:
            self.assertEqual(self.client.post('/api/reconstruct',json={**q,**override}).status_code,422)
        self.assertEqual(self.client.post('/api/extract?layout_profile='+PROFILE+'&pipeline=B2',content=b'x').status_code,422)

    def test_real_synthetic_ocr_http_workflow(self):
        path=FIXTURES/'01-two-row-colored.png'
        x=self.client.post('/api/extract?layout_profile='+PROFILE,content=path.read_bytes(),headers={'Content-Type':'image/png','X-Filename':'synthetic.png'})
        self.assertEqual(x.status_code,200,x.text[:200]);x=x.json()
        self.assertEqual(x['layout_evidence']['profile'],PROFILE)
        self.assertTrue(any(w['confidence'] is not None for p in x['layout_evidence']['pages'] for w in p['words']))
        r=self.client.post('/api/reconstruct',json={'packet':x['data'],'layout_evidence':x['layout_evidence'],'profile':PROFILE,'decoder_mode':'constrained'})
        self.assertEqual(r.status_code,200,r.text[:200]);r=r.json();v=next(v for v in values(r) if v['course_code']=='ZX4101')
        self.assertEqual((v['day_of_week'],v['date'],v['start_time'],v['end_time'],v['location']), (1,'2033-02-07','07:00','09:00','TEST-V201'))
        pr=self.client.post('/api/layout/s2b/assess',json={'source_packet':x['data'],'layout_evidence':x['layout_evidence'],'reconstruction':r})
        self.assertEqual(pr.status_code,200,pr.text[:200]);self.assertTrue(all(d['decision']=='ABSTAIN' for d in pr.json()['decisions']))
        bundle={'packet':r['canonical_output']['packet'],'context':{},'assessments':[],'reconstruction_status':r['canonical_output']['status']}
        calendar=self.client.post('/api/calendar/v1/preview',json={'bundle':bundle});self.assertEqual(calendar.status_code,200);self.assertFalse(calendar.json()['write_authorized'])

    def test_s2b_pdf_route_rasterizes_and_preserves_page_transforms(self):
        r=self.client.post('/api/extract?layout_profile='+PROFILE,content=pdf(),headers={'Content-Type':'application/pdf','X-Filename':'synthetic.pdf'})
        self.assertEqual(r.status_code,200,r.text[:200]);x=r.json()
        self.assertEqual(x['data']['source']['route'],'ocr');self.assertEqual({p['page'] for p in x['layout_evidence']['pages']},{1,2})
        self.assertTrue(all(p['transform']['dpi']==200 for p in x['layout_evidence']['pages']))
        self.assertTrue(all(not p['regions'] for p in x['layout_evidence']['pages']))

    def test_unrelated_colored_notice_does_not_become_course(self):
        path=FIXTURES/'08-unrelated-colored-notice.png'
        x=self.client.post('/api/extract?layout_profile='+PROFILE,content=path.read_bytes(),headers={'Content-Type':'image/png','X-Filename':'synthetic-notice.png'}).json()
        r=run(CanonicalTimetable.model_validate(x['data']),BlockEvidence.model_validate(x['layout_evidence']))
        self.assertFalse(events(r));self.assertTrue(r['canonical_output']['layout']['regions'])

    def test_default_pdf_does_not_silently_switch_to_s2b(self):
        r=self.client.post('/api/extract',content=pdf(),headers={'Content-Type':'application/pdf','X-Filename':'synthetic.pdf'}).json()
        self.assertEqual(r['data']['source']['route'],'native_pdf');self.assertNotIn('layout_evidence',r)


if __name__=='__main__':unittest.main()
