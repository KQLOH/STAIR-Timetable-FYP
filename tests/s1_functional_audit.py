"""S1 audit runner: small SYNTHETIC fixtures and loopback diagnostics only.

Requires the existing app on 127.0.0.1:8000 and frontend on :5500. Never logs in
to TAR UMT, calls providers, changes application code, or opens research data.
Writes only a fresh ignored data/temp/s1-audit-* directory. Not an evaluator.
"""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen.canvas import Canvas
from fastapi.testclient import TestClient
from backend import main
from backend.models.event import CanonicalTimetable
from backend.services.ocr_runtime import detect_runtime
from backend.analysis.service import analyze
from tests.fixtures import timetable_html
from tests.test_m7_analysis import make, attest
from tests.test_m52_layout import synthetic_png


def png(lines, width=1800):
    image = Image.new('RGB', (width, max(260, 65 * len(lines) + 40)), 'white')
    draw = ImageDraw.Draw(image)
    for i, text in enumerate(lines):
        draw.text((25, 20 + 65*i), text, fill='black', font=ImageFont.load_default(size=29))
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue()


def grid(merged=False):
    image = Image.new('RGB', (1500, 520), 'white'); draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=27)
    def text(x, y, t): draw.text((x, y), t, fill='black', font=font)
    text(20, 15, '24-HOUR SYNTHETIC TIMETABLE - TEST DATA')
    for y in [80, 150, 330, 510]: draw.line((10, y, 1490, y), fill='black', width=3)
    for x in [10, 170, 1490]: draw.line((x, 80, x, 510), fill='black', width=3)
    draw.line((820, 80, 820, 150 if merged else 510), fill='black', width=3)
    if merged: draw.line((820, 330, 820, 510), fill='black', width=3)
    text(200, 100, '09:00-11:00'); text(850, 100, '11:00-13:00')
    text(20, 195, 'Monday'); text(20, 380, 'Tuesday')
    text(210, 180, 'CS101 NETWORKS' if not merged else 'CS201 MERGED SESSION')
    text(210, 235, 'ROOM A101 GROUP G1')
    if not merged:
        text(850, 180, 'MA202 CALCULUS'); text(850, 235, 'ROOM B202 GROUP G2')
    text(210, 370, 'CS303 DATABASES'); text(210, 425, 'ROOM C303')
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue()


def pdf():
    output = BytesIO(); canvas = Canvas(output, pagesize=(720, 250))
    for text in ['CS101 Monday 09:00-11:00 Lecture Room A101',
                 'MA202 Tuesday 14:00-16:00 Tutorial Room B202']:
        canvas.setFont('Helvetica', 18); canvas.drawString(20, 155, text); canvas.showPage()
    canvas.save(); return output.getvalue()


def main_audit():
    directory = ROOT/'data/temp'/datetime.now(timezone.utc).strftime('s1-audit-%Y%m%dT%H%M%SZ')
    directory.mkdir(parents=True, exist_ok=False)
    image_jpg = BytesIO(); Image.open(BytesIO(synthetic_png(grid=True))).save(image_jpg, format='JPEG', quality=95)
    fixtures = {
        'A-simple.png': ('image/png', png(['CS101 Monday 09:00-11:00 Lecture Room A101 Group G1']), ['CS101']),
        'B-multiple.jpg': ('image/jpeg', image_jpg.getvalue(), ['CS101', 'MA202']),
        'C-grid.png': ('image/png', grid(), ['CS101', 'MA202', 'CS303']),
        'D-merged.png': ('image/png', grid(True), ['CS201', 'CS303']),
        'E-compound.png': ('image/png', png(['CS101 Monday 09:00-10:00 Lecture; 11:00-12:00 Tutorial']), ['CS101']),
        'F-incomplete.png': ('image/png', png(['CS404 INCOMPLETE SESSION', 'WEDNESDAY ?? - 12:00', 'ROOM TBA', 'GROUP ?']), ['CS404']),
        'G-multipage.pdf': ('application/pdf', pdf(), ['CS101', 'MA202']),
    }
    for name, (_, raw, _) in fixtures.items(): (directory/name).write_bytes(raw)
    result = {'kind':'SYNTHETIC_FUNCTIONAL_AUDIT_NOT_RESEARCH_EVALUATION',
              'started_at':datetime.now(timezone.utc).isoformat(), 'fixture_directory':str(directory),
              'calls':[], 'flows':[], 'mocked_tar':[], 'analysis_cases':[], 'checks':[]}
    session = requests.Session(); session.trust_env = False

    def call(method, path, **kwargs):
        port = kwargs.pop('port', 8000)
        response = session.request(method, f'http://127.0.0.1:{port}'+path, timeout=90, **kwargs)
        result['calls'].append({'method':method,'path':path,'port':port,'status':response.status_code})
        return response

    for path in ['/', '/api/health', '/openapi.json']:
        call('GET', path)
    for path in ['/index.html','/js/app.js','/js/upload.js','/js/intranet.js','/js/analysis.js']:
        call('GET', path, port=5500)
    for origin in ['http://127.0.0.1:5500','https://synthetic-untrusted.invalid']:
        r=call('OPTIONS','/api/extract',headers={'Origin':origin,'Access-Control-Request-Method':'POST'})
        result['checks'].append({'id':'cors','origin':origin,'status':r.status_code,
                                 'allow_origin':r.headers.get('access-control-allow-origin')})

    for name, (mime, raw, expected_codes) in fixtures.items():
        for pipeline in ['evidence', 'B1', 'B2', 'B2H-v1']:
            r=call('POST', '/api/extract?pipeline='+pipeline, data=raw,
                   headers={'Content-Type':mime,'X-Filename':name})
            detail={'fixture':name,'sha256':sha256(raw).hexdigest(),'bytes':len(raw),
                    'pipeline':pipeline,'http_status':r.status_code,'expected_source_codes':expected_codes}
            if r.status_code != 200:
                detail['error']=r.json();result['flows'].append(detail);continue
            packet=r.json()['data'];CanonicalTimetable.model_validate(packet)
            detail.update(route=packet['source']['route'], evidence_count=len(packet['evidence']),
                          pages=sorted({s['page'] for s in packet['evidence'] if s['page'] is not None}),
                          recognized_text=[s['raw_text'] for s in packet['evidence']],
                          evidence_has_boxes=all(s['bbox'] is not None for s in packet['evidence']),
                          confidence_values=[s['confidence'] for s in packet['evidence']],
                          baseline_events=[e['revision']['content'] for e in packet['events']])
            (directory/(name+'.'+pipeline+'.extraction.json')).write_text(r.text,encoding='utf-8')
            if pipeline == 'evidence':
                profile='ocr-layout-m52-v1' if packet['source']['route']=='ocr' else 'm4-development-v1'
                request={'packet':packet,'decoder_mode':'constrained','profile':profile,'context':{}}
                rr=call('POST','/api/reconstruct',json=request);detail['reconstruct_http']=rr.status_code
                if rr.status_code==200:
                    envelope=rr.json();output=envelope['canonical_output']
                    if output:
                        events=output['packet']['events']+output['unresolved_candidates']
                        detail.update(profile=profile,decoder_status=output['status'],selected=len(output['packet']['events']),
                                      unresolved=len(output['unresolved_candidates']),events=[e['revision']['content'] for e in events],
                                      field_states=[{o['field_name']:o['state'] for o in e['revision']['observations']} for e in events],
                                      candidate_ids=[e['candidate_id'] for e in events],canonical_digest=envelope['canonical_digest'])
                        route='/api/layout/assess' if packet['source']['route']=='ocr' else '/api/policy/assess'
                        pr=call('POST',route,json={'source_packet':packet,'reconstruction':envelope})
                        detail['policy_http']=pr.status_code
                        detail['policy']=pr.json()
                        bundle={'packet':output['packet'],'context':{},'assessments':[],'reconstruction_status':output['status']}
                        er=call('POST','/api/calendar/v1/eligibility',json=bundle)
                        cr=call('POST','/api/calendar/v1/preview',json={'bundle':bundle})
                        detail['calendar']={'eligibility_http':er.status_code,'preview_http':cr.status_code,
                                            'eligibility':er.json(),'preview':cr.json()}
                        q=make().model_dump(mode='json');q.update(packet={**output['packet'],'events':events},context={},declared_kind='UNVERIFIED_CANDIDATES')
                        ar=call('POST','/api/analysis/v1/analyze',json=q)
                        detail['analysis_http']=ar.status_code;detail['analysis']=ar.json()
                        if name=='A-simple.png':
                            repeated=call('POST','/api/reconstruct',json=request).json()
                            unconstrained=call('POST','/api/reconstruct',json={**request,'decoder_mode':'unconstrained'}).json()
                            detail['repeat_digest_equal']=repeated['canonical_digest']==envelope['canonical_digest']
                            detail['matched_b3']={'method':unconstrained['canonical_output']['method_id'],
                                'base_score_digest_equal':output['base_score_digest']==unconstrained['canonical_output']['base_score_digest']}
                    (directory/(name+'.reconstruction.json')).write_text(rr.text,encoding='utf-8')
            result['flows'].append(detail)
        print(json.dumps({'completed_fixture':name}),flush=True)

    bad_inputs=[('empty.png','image/png',b''),('bad.png','image/png',b'\x89PNG\r\n\x1a\nSYNTHETIC CORRUPT'),
                ('unsafe.exe','application/octet-stream',b'MZ'),('mismatch.png','application/pdf',fixtures['A-simple.png'][1])]
    for name,mime,raw in bad_inputs:
        r=call('POST','/api/extract',data=raw,headers={'Content-Type':mime,'X-Filename':name})
        result['checks'].append({'id':'invalid_upload','fixture':name,'status':r.status_code})
    r=call('POST','/api/extract',data=b'x',headers={'Content-Type':'image/png','X-Filename':'..\\synthetic.png'})
    result['checks'].append({'id':'unsafe_filename_invalid_content','status':r.status_code})
    for path in ['/api/review/queue','/api/calendar/v1/history','/api/calendar/v1/sandbox','/api/calendar/v1/reconciliation-diagnostics']:
        r=call('GET',path);result['checks'].append({'id':'unconfigured_host_denied','path':path,'status':r.status_code})
    for path in ['/api/calendar/v1/approve','/api/calendar/v1/execute','/api/calendar/v1/sync']:
        r=call('POST',path,json={});result['checks'].append({'id':'absent_public_write_route','path':path,'status':r.status_code})

    # Isolated in-process fixtures: no actual university login and no live-app bridge.
    portal=MagicMock();portal.student_name='SYNTHETIC PERSON';portal.last_timetable_url='https://synthetic.invalid/timetable'
    portal.fetch_latest_timetable_html.return_value=timetable_html()
    with patch.dict(main.CONNECTED_CLIENTS,{},clear=True), patch.object(main,'TARUMTClient',return_value=portal), TestClient(main.app) as client:
        connected=client.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC'})
        token=connected.json()['connection_token'];data=connected.json()['data']
        result['mocked_tar'].append({'case':'connect','status':connected.status_code,'events':len(data['events']),
                                     'source_route':data['canonical']['source']['route'],
                                     'dates':[e.get('date') for e in data['events']]})
        for label,path in [('refresh','/api/intranet/timetable'),('disconnect','/api/intranet/disconnect'),('expired','/api/intranet/timetable')]:
            rr=client.post(path,json={'token':token});result['mocked_tar'].append({'case':label,'status':rr.status_code})
        from backend.services.tarumt_client import TARUMTLoginError
        portal.login.side_effect=TARUMTLoginError('SYNTHETIC FAILURE')
        rr=client.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC'})
        result['mocked_tar'].append({'case':'invalid_authentication','status':rr.status_code,'private_error_redacted':'SYNTHETIC FAILURE' not in rr.text})

    q=make();observed=analyze(q)
    complete,authority,_=attest(q)
    from backend.analysis.models import Availability
    complete=complete.model_copy(update={'availability':(Availability(start='2026-01-05T08:00:00+00:00',end='2026-01-05T18:00:00+00:00'),)})
    # Availability participates in binding; re-attest this synthetic request.
    complete,authority,_=attest(complete)
    full=analyze(complete,authority)
    for label,value in [('unverified_synthetic',observed),('isolated_synthetic_complete',full)]:
        result['analysis_cases'].append({'case':label,'result':value,'basis':'ISOLATED AUTOMATED SYNTHETIC SERVICE CHECK'})
    runtime=detect_runtime()
    result['runtime']={'executable':runtime.executable,'version':runtime.engine_version,'languages':runtime.languages}
    result['legacy_saved_sample_exists']=main.SAMPLE_RESPONSE_FILE.is_file()
    result['legacy_saved_sample_content_read']=False
    result['completed_at']=datetime.now(timezone.utc).isoformat()
    result['boundaries']={'research_evaluation':False,'heldout_read':False,'baseline_written':False,
                          'real_credentials':False,'external_calendar_writes':False,'live_host_authorization_modified':False}
    (directory/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'result_path':str(directory/'results.json'),'http_calls':len(result['calls']),'flows':len(result['flows'])}),flush=True)


if __name__=='__main__':
    main_audit()
