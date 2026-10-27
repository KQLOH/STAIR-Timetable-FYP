"""S4A independent synthetic acceptance harness. No live review/calendar authority.

The QA server copies actual routes/frontend. Its extra loopback controls only
hold responses or change invented portal/failure behavior, never mint grants.
"""
import asyncio
from contextlib import ExitStack
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO
import json,os,shutil,sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
WORK=ROOT/'data/temp/s4a-acceptance';PORT=8016
MOCK_ID='SYNTHETIC_QA_ONLY';MOCK_SECRET='S4A_MOCK_SECRET_CANARY_NOT_REAL'


def fixtures():
    from tests.s1_functional_audit import png,grid,pdf
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas
    out=WORK/'fixtures';out.mkdir(parents=True,exist_ok=True)
    texts=['CS101 Monday 2033-02-07 09:00-11:00 Lecture Room A101 Group G1']
    (out/'simple.png').write_bytes(png(texts));Image.open(out/'simple.png').save(out/'simple.jpg',quality=95)
    (out/'grid.png').write_bytes(grid());(out/'merged.png').write_bytes(grid(True))
    (out/'compound.png').write_bytes(png(['CS101 Monday 09:00-10:00 Lecture; 11:00-12:00 Tutorial']))
    (out/'incomplete.png').write_bytes(png(['CS404 INCOMPLETE SESSION','Wednesday ROOM TBA GROUP UNKNOWN']))
    (out/'multipage.pdf').write_bytes(pdf())
    for filename,lines in {
        'dated-weeks.pdf':['CS101 Monday 2033-02-07 09:00-11:00 Lecture Room A101','MA202 Monday 2033-02-21 14:00-16:00 Tutorial Room B202'],
        'replacement.pdf':['MA202 Tuesday 2033-02-08 14:00-16:00 Tutorial Room B202'],
        'slow-original.pdf':['CS303 Wednesday 2033-02-09 10:00-12:00 Lecture Room C303'],
        'safe-text.pdf':['CS101 Monday 2033-02-07 09:00-11:00 Lecture Room A101','S4A_SOURCE_CANARY <img src=x onerror=alert(1)>']}.items():
        raw=BytesIO();canvas=Canvas(raw,pagesize=(1200,330));canvas.setFont('Helvetica',17)
        for i,line in enumerate(lines):canvas.drawString(20,260-i*65,line)
        canvas.save();(out/filename).write_bytes(raw.getvalue())
    (out/'missing-runtime.png').write_bytes((out/'simple.png').read_bytes())
    (out/'invalid.png').write_bytes(b'\x89PNG\r\n\x1a\nCORRUPT SYNTHETIC S4A')
    (out/'invalid.pdf').write_bytes(b'%PDF-1.7\nCORRUPT SYNTHETIC S4A')
    for original,name in [('01-two-row-colored.png','blocks.png'),('07-transposed-unsupported.png','unsupported.png')]:
        shutil.copyfile(ROOT/'tests/synthetic_samples/s2b_preparation'/original,out/name)
    manifest={p.name:sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    (WORK/'fixture-manifest.json').write_text(json.dumps({'kind':'INDEPENDENT_SYNTHETIC_QA_NOT_GOLD','sha256':manifest},indent=2),encoding='utf-8')


class Controls:
    def __init__(self):
        self.rule=None;self.held={};self.requests=[];self.portal_login_fail=False;self.portal_fetch_fail=False;self.portals=[]
    def state(self):
        return {'armed':self.rule,'held':[{'id':key,'path':v['path'],'released':v['event'].is_set(),'returned':v['returned']} for key,v in self.held.items()],
            'requests':self.requests,'portal_clients':len(self.portals),'portal_closed':sum(p.closed for p in self.portals),
            'review_enrolled':False,'calendar_enrolled':False}


class MockPortal:
    def __init__(self,controls):
        self.controls=controls;controls.portals.append(self);self.student_name='SYNTHETIC QA PERSON';self.last_timetable_url='https://synthetic.invalid/mock';self.closed=False;self.expired=False;self.calls=0
    def login(self,student,password):
        from backend.services.tarumt_client import TARUMTLoginError
        fail=self.controls.portal_login_fail;self.controls.portal_login_fail=False
        if fail or student!=MOCK_ID or password!=MOCK_SECRET:raise TARUMTLoginError('S4A_PRIVATE_ERROR_CANARY')
    def fetch_latest_timetable_html(self):
        from tests.fixtures import timetable_html
        from backend.services.tarumt_client import TARUMTLoginError,TARUMTNavigationError
        if self.expired:raise TARUMTLoginError('S4A_PRIVATE_ERROR_CANARY')
        if self.controls.portal_fetch_fail:
            self.controls.portal_fetch_fail=False;raise TARUMTNavigationError('S4A_PRIVATE_ERROR_CANARY')
        self.calls+=1;code='SYN7101' if self.calls==1 else 'SYN7202'
        return timetable_html(visible=f'{code} (L) 9:00 AM - 10:00 AM',title='SYNTHETIC QA COURSE (Lecture) SYNTHETIC STAFF (MOCK ROOM, Test Block)',header='Mon 2033-02-07',metadata=False)
    def close(self):self.closed=True


def extraction_wrapper(original):
    def extract(filename,mime,data,**kwargs):
        if filename=='missing-runtime.png':
            from backend.services import ocr_runtime
            with patch.object(ocr_runtime,'detect_runtime',side_effect=ocr_runtime.OCRRuntimeError('OCR_EXECUTABLE_MISSING','Synthetic QA: executable unavailable; no software was removed.')):
                return original(filename,mime,data,**kwargs)
        return original(filename,mime,data,**kwargs)
    return extract


def qa_app(controls):
    from fastapi import FastAPI,Request,HTTPException
    from backend import main
    # Copy routes, not production startup state. The mock runs in a dedicated process.
    app=FastAPI();app.include_router(main.app.router);app.user_middleware=list(main.app.user_middleware)
    @app.middleware('http')
    async def qa_delay(request:Request,call_next):
        if not request.url.path.startswith('/__qa__/'):
            controls.requests.append({'path':request.url.path,'filename':request.headers.get('x-filename'),'method':request.method})
        rule=controls.rule
        if rule and request.url.path==rule['path'] and (not rule.get('filename') or request.headers.get('x-filename')==rule['filename']):controls.rule=None
        else:rule=None
        response=await call_next(request)
        if rule:
            key='hold-'+str(len(controls.held)+1);entry={'path':request.url.path,'event':asyncio.Event(),'returned':False};controls.held[key]=entry
            try:await asyncio.wait_for(entry['event'].wait(),timeout=120)
            except asyncio.TimeoutError:pass
            entry['returned']=True
        return response
    @app.get('/__qa__/state')
    async def qa_state():return controls.state()
    @app.post('/__qa__/control')
    async def qa_control(request:Request):
        if not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):raise HTTPException(403)
        body=await request.json();action=body.get('action')
        if action=='hold':
            if body.get('path') not in ('/api/extract','/api/intranet/timetable','/api/intranet/connect'):raise HTTPException(422)
            controls.rule={'path':body['path'],'filename':body.get('filename')}
        elif action=='release':
            for entry in controls.held.values():entry['event'].set()
        elif action=='expire':
            for portal in controls.portals:portal.expired=True
        elif action=='login-failure':controls.portal_login_fail=True
        elif action=='fetch-failure':controls.portal_fetch_fail=True
        else:raise HTTPException(422)
        return {'ok':True,'action':action}
    return app


def serve():
    import socket,uvicorn
    from fastapi.staticfiles import StaticFiles
    from backend import main
    with socket.socket() as probe:probe.bind(('127.0.0.1',PORT))
    fixtures();controls=Controls();app=qa_app(controls)
    assert getattr(app.state,'m5_review_bridge',None) is None and getattr(app.state,'s3c_calendar_bridge',None) is None
    target=WORK/'frontend';shutil.copytree(ROOT/'frontend',target,dirs_exist_ok=True)
    p=target/'js/app.js';p.write_text(p.read_text(encoding='utf-8').replace('http://127.0.0.1:8000',f'http://127.0.0.1:{PORT}'),encoding='utf-8')
    app.mount('/s4a',StaticFiles(directory=target,html=True),name='s4a-public-acceptance')
    (WORK/'server.json').write_text(json.dumps({'pid':os.getpid(),'port':PORT,'kind':'OWNED_S4A_SYNTHETIC_QA','operator_enrolled':False,'mock_portal':True,'controlled_response_gates':True}),encoding='utf-8')
    with ExitStack() as stack:
        stack.enter_context(patch.object(main,'TARUMTClient',lambda:MockPortal(controls)))
        stack.enter_context(patch.object(main,'extract_document',extraction_wrapper(main.extract_document)))
        stack.enter_context(patch.dict(main.CONNECTED_CLIENTS,{},clear=True))
        uvicorn.run(app,host='127.0.0.1',port=PORT,access_log=False)


def check():
    import requests
    from backend.models.event import CanonicalTimetable
    session=requests.Session();session.trust_env=False;cases=[];calls=[];flows={}
    def call(method,path,**kwargs):
        r=session.request(method,f'http://127.0.0.1:{PORT}'+path,timeout=90,**kwargs)
        calls.append({'method':method,'path':path,'status':r.status_code});return r
    def case(ident,title,fn):
        try:observed=fn();cases.append({'id':ident,'title':title,'level':'ACTUAL_HTTP','status':'PASS','observed':observed})
        except Exception as error:cases.append({'id':ident,'title':title,'level':'ACTUAL_HTTP','status':'FAIL','observed':str(error)[:400]})
    def flow(filename,mime,profile,expected,pipeline='evidence'):
        layout=profile if profile in ('ocr-ruled-grid-s2a-v1','ocr-block-grid-s2b-v1') else None
        path='/api/extract?pipeline='+pipeline+('&layout_profile='+layout if layout else '')
        r=call('POST',path,data=(WORK/'fixtures'/filename).read_bytes(),headers={'Content-Type':mime,'X-Filename':filename});assert r.status_code==200,r.text[:140]
        extraction=r.json();source=extraction['data'];CanonicalTimetable.model_validate(source)
        expected_route='native_pdf' if mime=='application/pdf' and pipeline=='evidence' and not layout else 'ocr'
        assert source['source']['route']==expected_route
        assert source['evidence'] and all(e['page'] and e['bbox'] and e['route']==expected_route for e in source['evidence'])
        body={'packet':source,'decoder_mode':'constrained','profile':profile,'context':{}}
        if layout:body['layout_evidence']=extraction['layout_evidence']
        r=call('POST','/api/reconstruct',json=body);assert r.status_code==200,r.text[:180]
        reconstruction=r.json();c=reconstruction['canonical_output'];assert c
        packet=c['packet'];events=packet['events']+c['unresolved_candidates']
        contents=[e['revision']['content'] for e in events]
        if filename=='multipage.pdf':
            assert {span['page'] for span in source['evidence']}=={1,2}
            for event in events:
                refs={ref for field in event['revision']['observations'] for ref in field['evidence_ids']}
                # Reconstructed spans are in the canonical output evidence collection.
                lookup={span['evidence_id']:span['page'] for span in packet['evidence']}
                pages={lookup[ref] for ref in refs if ref in lookup}
                assert len(pages)==1, 'Missing or cross-page evidence ownership'
                expected_page={'CS101':1,'MA202':2}.get(event['revision']['content']['course_code'])
                if expected_page:assert pages=={expected_page}, 'Wrong source page'
        if filename=='compound.png':
            assert {(e['course_code'],e['day_of_week'],e['start_time'],e['end_time'],e['session_type']) for e in contents}=={
                ('CS101',1,'09:00','10:00','Lecture'),('CS101',1,'11:00','12:00','Tutorial')}
        for code,fields in expected.items():
            matches=[e for e in contents if e['course_code']==code];assert matches,code+' missing'
            for key,value in fields.items():assert any(e[key]==value for e in matches),(code,key,value)
        if filename=='unsupported.png':assert not packet['events'] and c['unresolved_candidates']
        if filename=='incomplete.png':assert not packet['events'] and all(e['start_time'] is None for e in contents)
        if layout:policy_path='/api/layout/'+('s2a' if 's2a' in layout else 's2b')+'/assess'
        else:policy_path='/api/layout/assess' if profile=='ocr-layout-m52-v1' else '/api/policy/assess'
        pb={'source_packet':source,'reconstruction':reconstruction,'reconstruction_context':{}}
        if layout:pb['layout_evidence']=extraction['layout_evidence']
        policy=call('POST',policy_path,json=pb);assert policy.status_code==200,policy.text[:140]
        assert all(d['decision']!='ACCEPT' and d['calibrated_probability'] is None for d in policy.json()['decisions'])
        bundle={'packet':packet,'context':{},'assessments':[],'reconstruction_status':c['status']}
        preview=call('POST','/api/calendar/v1/preview',json={'bundle':bundle});assert preview.status_code==200 and preview.json()['write_authorized'] is False
        analysis=call('POST','/api/analysis/v1/analyze',json={'packet':{**packet,'events':events},'event_set_id':'S4A-SYNTHETIC','profile':'m7-development-v1','declared_kind':'UNVERIFIED_CANDIDATES','context':{},
            'scope':{'scope_id':'S4A-SYNTHETIC','timetable_id':packet['source']['timetable_id'] or 'S4A-SYNTHETIC','start':'2033-02-07','end':'2033-02-28','timezone':'Etc/UTC'}})
        assert analysis.status_code==200,analysis.text[:140];assert not analysis.json()['completeness']['trusted_complete']
        assert analysis.json()['recommendation_status']=='BLOCKED_INCOMPLETE_SCHEDULE'
        record={'source':source['source'],'selected':len(packet['events']),'partial':len(c['unresolved_candidates']),'contents':contents,
            'findings':c['findings'],'status':c['status'],'calendar_write_authorized':False,'analysis_label':analysis.json()['completeness']['label']}
        flows[filename+':'+pipeline]=record;return record
    for item in [
        ('H01','PNG complete pipeline','simple.png','image/png','ocr-layout-m52-v1',{'CS101':{'date':'2033-02-07','start_time':'09:00','location':'A101'}}),
        ('H02','JPG complete pipeline','simple.jpg','image/jpeg','ocr-layout-m52-v1',{'CS101':{'location':'A101'}}),
        ('H03','Native two-page PDF','multipage.pdf','application/pdf','m4-development-v1',{'CS101':{'location':'A101'},'MA202':{'location':'B202'}}),
        ('H04','S2a ruled grid','grid.png','image/png','ocr-ruled-grid-s2a-v1',{'CS101':{'start_time':'09:00','location':'A101'},'MA202':{'start_time':'11:00','location':'B202'},'CS303':{'location':'C303'}}),
        ('H05','S2a merged columns','merged.png','image/png','ocr-ruled-grid-s2a-v1',{'CS201':{'start_time':'09:00','end_time':'13:00'}}),
        ('H06','S2a compound sessions','compound.png','image/png','ocr-ruled-grid-s2a-v1',{'CS101':{'day_of_week':1}}),
        ('H07','S2b supported colored blocks','blocks.png','image/png','ocr-block-grid-s2b-v1',{'ZX4101':{'date':'2033-02-07','start_time':'07:00','location':'TEST-V201'}}),
        ('H08','Unsupported transpose stays partial','unsupported.png','image/png','ocr-block-grid-s2b-v1',{}),
        ('H09','Missing time remains explicit','incomplete.png','image/png','ocr-layout-m52-v1',{'CS404':{'start_time':None,'location':None}}),
        ('H10','Dated week scope','dated-weeks.pdf','application/pdf','m4-development-v1',{'CS101':{'date':'2033-02-07'},'MA202':{'date':'2033-02-21'}}),
        ('H11','Untrusted source text is retained','safe-text.pdf','application/pdf','m4-development-v1',{'CS101':{'date':'2033-02-07'}})]:
        ident,title,filename,mime,profile,expected=item
        case(ident,title,lambda f=filename,m=mime,p=profile,e=expected:flow(f,m,p,e))
    for ident,pipeline in [('H12','B1'),('H13','B2'),('H14','B2H-v1')]:
        case(ident,'PDF '+pipeline+' keeps shared OCR route',lambda p=pipeline:flow('multipage.pdf','application/pdf','m4-development-v1',{},p))
    for ident,filename,mime,expected in [('H15','invalid.png','image/png',422),('H16','invalid.pdf','application/pdf',422),('H17','missing-runtime.png','image/png',503)]:
        def failure(f=filename,m=mime,status=expected):
            r=call('POST','/api/extract',data=(WORK/'fixtures'/f).read_bytes(),headers={'Content-Type':m,'X-Filename':f});assert r.status_code==status,r.text[:140]
            if f=='missing-runtime.png':assert r.json()['detail']['diagnostic_code']=='OCR_EXECUTABLE_MISSING'
            return {'status':r.status_code,'detail':r.json()['detail']}
        case(ident,'Transparent rejection: '+filename,failure)
    def refusals():
        for method,path in [('GET','/api/review/queue'),('POST','/api/review/ingest'),('GET','/api/review/session'),('GET','/api/calendar/v1/test-only/session'),('POST','/api/calendar/v1/test-only/preview'),('POST','/api/calendar/v1/test-only/approve'),('POST','/api/calendar/v1/test-only/execute')]:
            assert call(method,path,json={'actor_id':'FORGED','approved':True}).status_code==403
        for path in ('/api/review/grants','/api/calendar/v1/test-only/grants','/api/calendar/v1/execute','/api/calendar/v1/sync','/api/calendar/v1/google','/api/calendar/v1/oauth'):
            assert call('POST',path,json={}).status_code==404
        return {'protected_denials':7,'absent_issuer_external_or_legacy_mutation_routes':6}
    case('H18','Default deny and no grant/provider escalation',refusals)
    def portal():
        bad=call('POST','/api/intranet/connect',json={'student_id':MOCK_ID,'password':'WRONG_SYNTHETIC'});assert bad.status_code==401
        login=call('POST','/api/intranet/connect',json={'student_id':MOCK_ID,'password':MOCK_SECRET});assert login.status_code==200,login.text[:140]
        token=login.json()['connection_token'];assert login.json()['data']['canonical']['source']['route']=='tarumt_html'
        fresh=call('POST','/api/intranet/timetable',json={'token':token});assert fresh.status_code==200
        assert fresh.json()['data']['events'][0]['subject_code']=='SYN7202'
        assert call('POST','/api/intranet/disconnect',json={'token':token}).status_code==200
        assert call('POST','/api/intranet/timetable',json={'token':token}).status_code==401
        assert all(MOCK_SECRET not in r.text and 'S4A_PRIVATE_ERROR_CANARY' not in r.text for r in (bad,login,fresh))
        return {'transport':'SYNTHETIC_MOCK_ONLY','connect':200,'refresh':200,'disconnect':200,'invalid_and_expired':401,'canonical_route':'tarumt_html'}
    case('H19','Mocked TAR lifecycle and safe errors',portal)
    # Retain only independent synthetic responses; no credentials/tokens enter artifacts.
    result={'kind':'SYNTHETIC_SOFTWARE_ACCEPTANCE_NOT_RESEARCH_EVALUATION','cases':cases,'calls':calls,'flows':flows,'live_operator_enrolled':False}
    (WORK/'http-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'cases':len(cases),'passed':sum(c['status']=='PASS' for c in cases),'failed':[{'id':c['id'],'observed':c['observed']} for c in cases if c['status']=='FAIL'],'http_calls':len(calls)}))


if __name__=='__main__':
    WORK.mkdir(parents=True,exist_ok=True);{'serve':serve,'check':check,'fixtures':fixtures}[sys.argv[1]]()
