"""FR6 actual host/API integration using explicitly invented TEST_ONLY owners."""
from datetime import timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.calendar.local_host import (LocalCalendarHost, create_calendar_app, fixture_catalogue,
    build_local_calendar, private_sandbox_directory, HOST, ORIGIN, PREFIX, COOKIE, ROOT)
from backend.calendar.sandbox import LocalSandbox
from backend.calendar.access import CalendarAccess
from backend.calendar.workflow import TestOnlyCalendarBridge
from backend.review.auth import TrustedHostAuthority, utc_now
from backend.reconstruction.models import digest


def synthetic_host(directory):
    sandbox=LocalSandbox(directory,'test:local-calendar-host-regression',CalendarAccess(TrustedHostAuthority()),test_only=True)
    catalogue=fixture_catalogue(sandbox)
    bindings={(b.packet.source.source_document_id,b.packet.source.content_sha256) for _,b in catalogue.values()}
    authority=TrustedHostAuthority(test_only_source_bindings=bindings)
    sandbox.access=CalendarAccess(authority)
    bridge=TestOnlyCalendarBridge(sandbox,catalogue,allowed_origins=(ORIGIN,),allow_failure_simulation=False)
    actor='SYNTHETIC-LOCAL-CALENDAR-OWNER'
    return LocalCalendarHost(bridge,actor,identity=lambda:actor)


class LocalCalendarHostTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/temp').mkdir(parents=True,exist_ok=True)
        self.temp=TemporaryDirectory(prefix='m6-test-fr6-host-',dir=ROOT/'data/temp');self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'calendar'
        self.host=synthetic_host(self.directory)
        self.client=TestClient(create_calendar_app(self.host),base_url=ORIGIN,client=('127.0.0.1',42000));self.addCleanup(self.client.close)
        self.headers={'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'}
    def pending(self):
        response=self.client.post('/__local_calendar__/pending',json={'acknowledge_test_only':True},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('HttpOnly',response.headers['set-cookie']);self.assertIn('SameSite=strict',response.headers['set-cookie'])
        return response.json()
    def connect(self):
        pending=self.pending();self.host.authorize_from_console(pending['nonce'])
        self.active={**self.headers,'X-STAIR-Calendar-Binding':pending['binding']}
        return self.active
    def call(self,method,path,body=None,headers=None):
        return self.client.request(method,PREFIX+path,json=body,headers=self.active if headers is None else headers)
    def preview(self,key='base'):
        response=self.call('POST','/preview',{'fixture_id':key});self.assertEqual(response.status_code,200,response.text);return response.json()
    def approval_body(self,plan):
        return {k:plan[k] for k in ('preview_digest','snapshot_digest','input_digest','target_sandbox_id')}|{'operation_keys':[o['operation_key'] for o in plan['operations'] if o['action'] in ('CREATE','UPDATE','NO_OP')],'owner_confirmation':True}
    def approve(self,plan):
        response=self.call('POST','/approve',self.approval_body(plan));self.assertEqual(response.status_code,200,response.text);return response.json()
    def execute(self,approval):return self.call('POST','/execute',{'approval_id':approval['approval_id']})
    def test_pending_does_not_grant_or_approve_and_no_http_consent(self):
        p=self.pending();h={**self.headers,'X-STAIR-Calendar-Binding':p['binding']}
        self.assertFalse(p['authorized']);self.assertFalse(self.host.authority._grants)
        self.assertEqual(self.client.get(PREFIX+'/session',headers=h).status_code,403)
        self.assertEqual(self.client.post('/__local_calendar__/authorize',json={'nonce':p['nonce']},headers=self.headers).status_code,403)
        self.assertEqual(self.client.get('/openapi.json').status_code,404)
    def test_explicit_consent_fixed_scope_no_bearer_leak(self):
        self.connect();r=self.call('GET','/session');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['mode'],'TEST_ONLY');self.assertFalse(r.json()['external_calendar']);self.assertFalse(r.json()['failure_simulation_enabled'])
        self.assertEqual(len(r.json()['fixtures']),4)
        for c in self.host.channels.values():self.assertNotIn(c.bearer,r.text)
        self.assertTrue(all(e['human_verification']['status']=='NOT_VERIFIED' and e['calendar']['approval_status']=='NOT_APPROVED' for f in r.json()['fixtures'] for e in f['events']))
        self.assertEqual(self.call('GET','/state').json()['entries'],[])
        self.assertEqual(self.call('GET','/history').json()['approvals'],[])
    def test_cross_origin_host_forwarding_and_missing_binding_rejected(self):
        self.connect()
        for h in ({**self.active,'Origin':'https://unapproved.invalid'},self.headers,{**self.active,'X-Forwarded-For':'127.0.0.1'},{**self.active,'Host':'localhost:8022'},{**self.active,'Authorization':'Bearer SYNTHETIC-NOT-A-TOKEN'}):
            self.assertEqual(self.client.get(PREFIX+'/session',headers=h).status_code,403)
        self.assertEqual(self.client.get(PREFIX+'/session?token=not-supported',headers=self.active).status_code,403)
        self.assertEqual(self.client.get(PREFIX+'/session',headers=list(self.active.items())+[('X-STAIR-Calendar-Binding','duplicate')]).status_code,403)
    def test_nonloopback_peer_and_unsafe_route_rejected(self):
        with TestClient(create_calendar_app(self.host),base_url=ORIGIN,client=('203.0.113.5',42000)) as remote:
            self.assertEqual(remote.get('/').status_code,403)
        self.connect()
        for path in ('/api/review/queue','/api/intranet/connect','/api/calendar/v1/sync',PREFIX+'/%73ession'):
            self.assertEqual(self.client.get(path,headers=self.active).status_code,403)
    def test_browser_cannot_change_owner_scope_or_supply_event_bundles(self):
        response=self.client.post('/__local_calendar__/pending',json={'acknowledge_test_only':True,'actor_id':'forged'},headers=self.headers)
        self.assertEqual(response.status_code,403);self.assertFalse(self.host.authority._grants)
        self.connect()
        self.assertEqual(self.call('POST','/preview',{'fixture_id':'real-upload'}).status_code,404)
        self.assertEqual(self.call('POST','/preview',{'fixture_id':'base','packet':{}}).status_code,422)
        self.assertEqual(self.call('POST','/execute',{'approval_id':str(uuid4()),'failure_scenario':'FAIL_BEFORE'}).status_code,404)
        self.assertEqual(self.call('GET','/state').json()['entries'],[])
    def test_console_wrong_identity_expired_nonce_and_replay_denied(self):
        p=self.pending();self.host.identity=lambda:'SYNTHETIC-WRONG-OWNER'
        with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'])
        self.host.identity=lambda:self.host.actor;self.host.clock=lambda:10**12
        with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'])
        self.host.clock=__import__('time').time;self.connect()
        with self.assertRaises(ValueError):self.host.authorize_from_console(p['nonce'])
    def test_create_replay_noop_and_update_do_not_duplicate(self):
        self.connect();create=self.preview();self.assertEqual([o['action'] for o in create['operations']],['CREATE','CREATE'])
        approval=self.approve(create);first=self.execute(approval);self.assertEqual(first.status_code,200,first.text)
        self.assertTrue(all(o['status']=='CONFIRMED' for o in first.json()['outcomes']))
        replay=self.call('POST','/execute',{'approval_id':approval['approval_id'],'replay_only':True});self.assertTrue(all(o['duplicate_submission'] for o in replay.json()['outcomes']))
        noop=self.preview();self.assertEqual([o['action'] for o in noop['operations']],['NO_OP','NO_OP']);self.assertEqual(self.execute(self.approve(noop)).status_code,200)
        update=self.preview('update');self.assertTrue(all(o['action']=='UPDATE' for o in update['operations']));self.assertEqual(self.execute(self.approve(update)).status_code,200)
        entries=self.call('GET','/state').json()['entries'];self.assertEqual(len(entries),2)
        self.assertEqual(next(e['payload']['location'] for e in entries if e['payload']['course_code']=='SYN101'),'DEMO ROOM C')
    def test_tampered_preview_and_missing_owner_confirmation_rejected(self):
        self.connect();plan=self.preview();body=self.approval_body(plan)
        self.assertEqual(self.call('POST','/approve',body|{'snapshot_digest':'f'*64}).status_code,409)
        self.assertEqual(self.call('POST','/approve',body|{'owner_confirmation':False}).status_code,422)
        self.assertEqual(self.call('GET','/state').json()['entries'],[])
    def test_missing_expired_and_stale_approvals_rejected(self):
        self.connect();self.assertEqual(self.call('POST','/execute',{'approval_id':str(uuid4())}).status_code,404)
        old=self.preview();approval=self.approve(old);self.host.bridge.sandbox.clock=lambda:utc_now()+timedelta(minutes=11)
        self.assertEqual(self.execute(approval).json()['detail'],'APPROVAL_EXPIRED');self.host.bridge.sandbox.clock=utc_now
        self.preview('update');self.assertEqual(self.execute(approval).json()['detail'],'STALE_SOURCE_REVISION')
        self.assertEqual(self.call('GET','/state').json()['entries'],[])
    def test_changed_target_requires_fresh_preview(self):
        self.connect();plan=self.preview();self.assertEqual(self.execute(self.approve(plan)).status_code,200)
        self.assertEqual(self.call('POST','/approve',self.approval_body(plan)).json()['detail'],'STALE_PREVIEW')
    def test_invalid_timezone_and_recurrence_remain_blocked(self):
        self.connect()
        for key in ('blocked-zone','blocked-repeat'):
            plan=self.preview(key);self.assertTrue(all(not e['sandbox_write_permitted'] for e in plan['eligibility']))
            self.assertFalse(any(o['action'] in ('CREATE','UPDATE','NO_OP') for o in plan['operations']))
        self.assertEqual(self.call('GET','/state').json()['entries'],[])
    def test_end_and_expired_grant_reject_further_access(self):
        self.connect();self.host.authority.clock=lambda:utc_now()+timedelta(minutes=31)
        self.assertEqual(self.call('GET','/session').status_code,403)
        self.host.authority.clock=utc_now;self.connect();self.assertEqual(self.call('POST','/session/end',{}).status_code,200)
        self.assertEqual(self.call('GET','/session').status_code,403)
    def test_restart_preserves_catalogue_entries_history_and_invalidates_grants(self):
        self.connect();approval=self.approve(self.preview());self.execute(approval)
        before=self.call('GET','/history').json();old_headers=dict(self.active)
        old_cookie=self.client.cookies.get(COOKIE)
        new_host=synthetic_host(self.directory)
        self.assertEqual(self.host.catalogue_digest,new_host.catalogue_digest)
        with TestClient(create_calendar_app(new_host),base_url=ORIGIN,client=('127.0.0.1',42000)) as client:
            client.cookies.set(COOKIE,old_cookie,domain='127.0.0.1',path=PREFIX)
            self.assertEqual(client.get(PREFIX+'/session',headers=old_headers).status_code,403)
            p=client.post('/__local_calendar__/pending',headers=self.headers,json={'acknowledge_test_only':True}).json()
            new_host.authorize_from_console(p['nonce']);h={**self.headers,'X-STAIR-Calendar-Binding':p['binding']}
            after=client.get(PREFIX+'/history',headers=h).json();self.assertEqual(before['journal'],after['journal'])
            self.assertEqual(len(client.get(PREFIX+'/state',headers=h).json()['entries']),2)
            self.assertEqual(client.post(PREFIX+'/execute',headers=h,json={'approval_id':approval['approval_id']}).json()['detail'],'APPROVAL_GRANT_MISMATCH')
            self.assertTrue(all(o['action']=='NO_OP' for o in client.post(PREFIX+'/preview',headers=h,json={'fixture_id':'base'}).json()['operations']))
    def test_normal_application_and_real_event_eligibility_stay_blocked(self):
        from backend.main import app
        from test_m5_policy import fixture
        from backend.models.event import CanonicalTimetable
        request,run=fixture();g=run.graph()
        bundle={'packet':CanonicalTimetable(source=g.source,evidence=g.evidence,events=run.events()).model_dump(mode='json')}
        with TestClient(app) as normal:
            self.assertEqual(normal.get(PREFIX+'/session').status_code,403)
            results=normal.post('/api/calendar/v1/eligibility',json=bundle).json()['events']
            self.assertTrue(all(e['status']=='BLOCKED' and not e['sandbox_write_permitted'] for e in results))
            self.assertEqual(normal.post('/api/calendar/v1/sync',json={}).status_code,404)


@unittest.skipUnless(os.name=='nt','Windows private storage/owner boundary')
class CalendarPrivateStartupTests(unittest.TestCase):
    def test_real_windows_startup_creates_private_test_storage_without_grants(self):
        with TemporaryDirectory(prefix='m6-test-fr6-startup-',dir=ROOT/'data/temp') as directory:
            path=Path(directory)/'records';self.assertFalse(path.exists())
            app,host=build_local_calendar(Path(directory))
            self.assertTrue(path.is_dir());self.assertFalse(host.authority._grants)
            self.assertEqual(host.bridge.sandbox.snapshot_scope,'TEST_ONLY')
            self.assertFalse((Path(directory)/'review').exists())
    def test_private_directory_rejects_wrong_root(self):
        with self.assertRaises(Exception):private_sandbox_directory(ROOT/'data/calendar_sandbox/unsafe')
