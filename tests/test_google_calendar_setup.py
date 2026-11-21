"""Invented Web credentials and in-memory Google HTTP; no real OAuth or writes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, parse_qs, unquote
import subprocess
import sys
import unittest

from fastapi.testclient import TestClient
from backend.student.api import create_student_app
from backend.student.google import GoogleCalendar, SCOPES
from backend.student.google_config import load_google_config
from backend.review.models import ReviewError
import test_student_accounts as base

CONFIG={'STAIR_GOOGLE_CLIENT_ID':'synthetic.apps.googleusercontent.com',
        'STAIR_GOOGLE_CLIENT_SECRET':'synthetic-secret-only',
        'STAIR_GOOGLE_TEST_CALENDAR_ID':'synthetic-test@group.calendar.google.com'}


class Response:
    def __init__(self, status, data):self.status_code=status;self.data=data
    def json(self):return deepcopy(self.data)


class GoogleHTTP:
    def __init__(self):
        self.events={};self.mutations=[];self.tokens=0;self.primary=False;self.role='owner';self.scope=SCOPES
    def post(self,url,**kwargs):
        self.tokens+=1
        return Response(200,{'access_token':'invented-access-token','refresh_token':'invented-refresh-token',
            'expires_in':3600,'scope':self.scope,'token_type':'Bearer'})
    def request(self,method,url,**kwargs):
        path=unquote(urlsplit(url).path)
        if '/users/me/calendarList/' in path:
            return Response(200,{'summary':'Synthetic STAIR test','primary':self.primary,'accessRole':self.role})
        if method=='GET' and path.endswith('/events'):return Response(200,{'items':list(self.events.values())})
        key=path.rsplit('/',1)[-1]
        if method=='GET':return Response(200,self.events[key]) if key in self.events else Response(404,{})
        if method=='POST':key=kwargs['json']['id']
        if method=='POST' and key in self.events:return Response(409,{})
        if method=='PATCH' and kwargs['headers'].get('If-Match')!=self.events[key]['etag']:return Response(412,{})
        self.mutations.append(method)
        event=deepcopy(kwargs['json']);event['id']=key;event['etag']='"synthetic-'+str(len(self.mutations))+'"'
        for name in ('start','end'):
            event[name]['dateTime']=datetime.fromisoformat(event[name]['dateTime']).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
        self.events[key]=event
        return Response(201 if method=='POST' else 200,event)


class GoogleConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'web.json'
        self.data={'web':{'client_id':CONFIG['STAIR_GOOGLE_CLIENT_ID'],'client_secret':CONFIG['STAIR_GOOGLE_CLIENT_SECRET'],
            'redirect_uris':[base.ORIGIN+'/api/student/google/callback']}}
    def write(self):self.path.write_text(json.dumps(self.data));return self.path
    def load(self):return load_google_config(self.write(),CONFIG['STAIR_GOOGLE_TEST_CALENDAR_ID'],base.ORIGIN)
    def test_valid_download_is_not_copied_or_modified(self):
        path=self.write();before=path.read_bytes()
        self.assertEqual(load_google_config(path,CONFIG['STAIR_GOOGLE_TEST_CALENDAR_ID'],base.ORIGIN),CONFIG)
        self.assertEqual(before,path.read_bytes())
    def test_desktop_wrong_port_missing_secret_and_primary_are_rejected(self):
        original=deepcopy(self.data)
        for data in ({'installed':original['web']},{'web':{**original['web'],'redirect_uris':['http://localhost:8020/api/student/google/callback']}},
                     {'web':{**original['web'],'client_secret':''}}, {'web':{**original['web'],'redirect_uris':None}}):
            self.data=data
            with self.assertRaises(ValueError):self.load()
        self.data=original
        with self.assertRaises(ValueError):load_google_config(self.write(),'primary',base.ORIGIN)
    def test_repository_credentials_are_rejected_before_reading(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            load_google_config(Path(__file__).resolve().parents[1]/'credentials.json','synthetic',base.ORIGIN)
    def test_offline_cli_does_not_print_credentials_or_start_server(self):
        result=subprocess.run([sys.executable,'-B','tools/run_student_app.py','--google-credentials',str(self.write()),
            '--google-calendar-id',CONFIG['STAIR_GOOGLE_TEST_CALENDAR_ID'],'--check-google-config'],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('offline checks',result.stdout)
        self.assertNotIn(CONFIG['STAIR_GOOGLE_CLIENT_SECRET'],result.stdout+result.stderr)
        self.assertNotIn(CONFIG['STAIR_GOOGLE_TEST_CALENDAR_ID'],result.stdout+result.stderr)


class GoogleConnectionTests(unittest.TestCase):
    login=base.StudentAccountTests.login
    enroll=base.StudentAccountTests.enroll
    schedule=base.StudentAccountTests.schedule
    approve=base.StudentAccountTests.approve
    execute=base.StudentAccountTests.execute
    def setUp(self):
        self.accounts=base.test_accounts();self.http=GoogleHTTP()
        self.factory=lambda session:GoogleCalendar(session,base.ORIGIN,clock=self.accounts.clock,transport=self.http,config=CONFIG)
        self.app=create_student_app(self.accounts,google_factory=self.factory)
        self.client=TestClient(self.app,base_url=base.ORIGIN,client=('127.0.0.1',44000));self.addCleanup(self.client.close)
        self.login()
    def connect(self):
        response=self.client.post('/api/student/google/connect',json={},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        params=parse_qs(urlsplit(response.json()['url']).query)
        return self.client.get('/api/student/google/callback',params={'state':params['state'][0],'code':'invented-code'},follow_redirects=False)
    def preview(self,task):
        result=self.client.post('/api/student/calendar/preview',headers=self.headers,json={'task_ids':[task['task_id']],'target':'google'})
        self.assertEqual(result.status_code,200,result.text);return result.json()
    def test_google_setup_guide_has_exact_callback_and_no_secret(self):
        result=self.client.get('/google-calendar-setup');self.assertEqual(result.status_code,200)
        self.assertIn(base.ORIGIN+'/api/student/google/callback',result.text)
        self.assertIn('Web application',result.text);self.assertNotIn(CONFIG['STAIR_GOOGLE_CLIENT_SECRET'],result.text)
    def test_oauth_connection_and_session_expose_no_tokens_or_secret(self):
        self.assertEqual(self.connect().headers['location'],'/?google=connected')
        result=self.client.get('/api/student/session');self.assertTrue(result.json()['google']['connected'])
        for secret in ('invented-access-token','invented-refresh-token',CONFIG['STAIR_GOOGLE_CLIENT_SECRET']):self.assertNotIn(secret,result.text)
    def test_primary_read_only_or_missing_scopes_do_not_connect(self):
        for primary,role,scope in ((True,'owner',SCOPES),(False,'reader',SCOPES),(False,'owner','')):
            self.http.primary,self.http.role,self.http.scope=primary,role,scope
            self.assertEqual(self.connect().headers['location'],'/?google=failed')
            self.assertFalse(self.client.get('/api/student/session').json()['google']['connected'])
    def test_approved_create_readback_noop_and_update_keep_one_google_event(self):
        self.connect();task,_=self.enroll();self.assertEqual(self.schedule(task).status_code,200)
        plan=self.preview(task);self.assertEqual(plan['operations'][0]['action'],'CREATE')
        self.assertEqual(self.execute(plan).status_code,409);self.assertEqual(self.http.mutations,[])
        self.assertEqual(self.approve(plan).status_code,200);self.assertEqual(self.execute(plan).json()['status'],'SUCCEEDED')
        self.assertEqual(self.execute(plan).json()['status'],'SUCCEEDED');self.assertEqual(self.http.mutations,['POST'])
        again=self.preview(task);self.assertEqual(again['operations'][0]['action'],'NO_OP')
        # Simulated manual edit in the external calendar, kept visible in the next preview.
        key=next(iter(self.http.events));self.http.events[key]['location']='Synthetic external room edit'
        self.http.events[key]['etag']='"external-version"'
        update=self.preview(task);self.assertEqual(update['operations'][0]['action'],'UPDATE')
        self.approve(update);self.assertEqual(self.execute(update).json()['status'],'SUCCEEDED')
        self.assertEqual(self.http.mutations,['POST','PATCH']);self.assertEqual(len(self.http.events),1)
        self.assertEqual(self.client.get('/api/student/workspace').json()['entries'],{})
    def test_etag_change_after_approval_does_not_overwrite_google_edit(self):
        self.connect();task,_=self.enroll();self.schedule(task);plan=self.preview(task);self.approve(plan);self.execute(plan)
        key=next(iter(self.http.events));self.http.events[key]['location']='Synthetic changed room';self.http.events[key]['etag']='"v2"'
        plan=self.preview(task);self.approve(plan);self.http.events[key]['etag']='"v3"'
        result=self.execute(plan);self.assertEqual(result.json()['status'],'CHECK_REQUIRED')
        self.assertEqual(self.http.mutations,['POST'])
    def test_disconnect_and_reconnect_invalidate_pending_google_approvals(self):
        self.connect();task,_=self.enroll();self.schedule(task);plan=self.preview(task);self.approve(plan)
        self.client.post('/api/student/google/disconnect',headers=self.headers,json={})
        self.assertEqual(self.execute(plan).status_code,404);self.assertEqual(self.http.mutations,[])
        self.connect();plan=self.preview(task);self.approve(plan);self.connect()
        self.assertEqual(self.execute(plan).status_code,404);self.assertEqual(self.http.mutations,[])
    def test_strict_fyp_mode_refuses_google_connection(self):
        self.accounts.calendar_policy='registered-accept'
        result=self.client.post('/api/student/google/connect',headers=self.headers,json={})
        self.assertEqual(result.status_code,409);self.assertEqual(self.http.tokens,0)
    def test_malformed_token_response_is_sanitized(self):
        from unittest.mock import patch
        session=self.accounts.authenticate(self.client.cookies.get('stair_student_session'))
        google=self.factory(session)
        for bad in ([],{'access_token':'','expires_in':3600},{'access_token':'invented','expires_in':1}):
            with patch.object(self.http,'post',return_value=Response(200,bad)):
                with self.assertRaises(ReviewError) as result:google._token({})
                self.assertNotIn('invented',str(result.exception))
