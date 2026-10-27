"""Read-only HTTP boundary and static/browser-JS checks using synthetic source data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil,subprocess,unittest
from fastapi.testclient import TestClient

from backend.main import app
from backend.calendar.api import CalendarReadBridge
from backend.calendar.access import CalendarAccess
from backend.calendar.sandbox import LocalSandbox
from backend.calendar.testing import SyntheticFixtureAuthority
from backend.calendar.models import CalendarInput
from backend.review.auth import TrustedHostAuthority


class CalendarAPITests(unittest.TestCase):
    def setUp(self):
        self.old=getattr(app.state,'m6_calendar_bridge',None);app.state.m6_calendar_bridge=None
        root=Path(__file__).resolve().parents[1]/'data/temp'
        self.temp=TemporaryDirectory(prefix='m6-test-',dir=root);self.addCleanup(self.temp.cleanup)
        self.auth=TrustedHostAuthority();self.actor,self.token=self.auth.issue('synthetic-m6-http-owner',{'read'})
        self.access=CalendarAccess(self.auth);self.access.enrol(self.actor,'test:http',{'read','preview','approve','execute','reconcile'})
        self.store=LocalSandbox(Path(self.temp.name)/'calendar','test:http',self.access,test_only=True)
        self.fixture=SyntheticFixtureAuthority(self.store);self.bundle=self.fixture.make()
        self.client=TestClient(app,client=('127.0.0.1',45000))
        self.headers={'Authorization':'Bearer '+self.token}

    def tearDown(self):app.state.m6_calendar_bridge=self.old

    def test_public_eligibility_always_operationally_blocked(self):
        r=self.client.post('/api/calendar/v1/eligibility',json=self.bundle.model_dump(mode='json'))
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['events'][0]['status'],'BLOCKED')
        self.assertFalse(r.json()['events'][0]['sandbox_write_permitted'])
        self.assertNotIn('SYN101',r.text)

    def test_public_preview_diagnostic_only_and_nonpersistent(self):
        r=self.client.post('/api/calendar/v1/preview',json={'bundle':self.bundle.model_dump(mode='json')})
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['write_authorized'])
        self.assertEqual(r.json()['preview']['operations'][0]['action'],'BLOCKED')
        self.assertFalse(self.store.snapshot(self.actor).entries)

    def test_test_authority_flag_not_accepted_from_http(self):
        body=self.bundle.model_dump(mode='json');body['test_authorized']=True
        self.assertEqual(self.client.post('/api/calendar/v1/eligibility',json=body).status_code,422)

    def test_legacy_ready_and_approve_cannot_authorize(self):
        body=self.bundle.model_dump(mode='json');body.update({'READY':True,'actor_id':'synthetic-admin','approved':True})
        self.assertEqual(self.client.post('/api/calendar/v1/eligibility',json=body).status_code,422)

    def test_no_http_approval_or_mutation_endpoints(self):
        for route in ('approve','execute','sync','delete','reconcile'):
            self.assertEqual(self.client.post('/api/calendar/v1/'+route,json={'actor_id':'fake','approved':True}).status_code,404)

    def test_host_read_routes_disabled_by_default(self):
        for route in ('sandbox','history','reconciliation-diagnostics'):
            self.assertEqual(self.client.get('/api/calendar/v1/'+route).status_code,403)

    def test_enrolled_host_read_only_bridge(self):
        app.state.m6_calendar_bridge=CalendarReadBridge(self.store)
        for route in ('sandbox','history','reconciliation-diagnostics'):
            r=self.client.get('/api/calendar/v1/'+route,headers=self.headers)
            self.assertEqual(r.status_code,200)
            self.assertNotIn(str(self.temp.name),r.text)

    def test_existing_review_token_without_scope_rejected(self):
        app.state.m6_calendar_bridge=CalendarReadBridge(self.store)
        _,token=self.auth.issue('synthetic-review-only',{'read'})
        self.assertEqual(self.client.get('/api/calendar/v1/sandbox',headers={'Authorization':'Bearer '+token}).status_code,403)

    def test_university_token_rejected(self):
        app.state.m6_calendar_bridge=CalendarReadBridge(self.store)
        self.assertEqual(self.client.get('/api/calendar/v1/sandbox',headers={'Authorization':'Bearer synthetic-university-token'}).status_code,403)

    def test_nonlocal_and_forwarded_peer_rejected(self):
        app.state.m6_calendar_bridge=CalendarReadBridge(self.store)
        client=TestClient(app,client=('203.0.113.15',45000))
        self.assertEqual(client.get('/api/calendar/v1/sandbox',headers={**self.headers,'X-Forwarded-For':'127.0.0.1'}).status_code,403)

    def test_malformed_body_redacted(self):
        r=self.client.post('/api/calendar/v1/eligibility',content=b'{private-data',headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,422)
        self.assertNotIn('private-data',r.text)

    def test_body_size_limit(self):
        r=self.client.post('/api/calendar/v1/preview',content=b' '*2097153,headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)

    def test_unsupported_media_type(self):
        self.assertEqual(self.client.post('/api/calendar/v1/eligibility',content=b'no').status_code,415)

    def test_m4_m5_application_output_stays_blocked(self):
        from test_m5_policy import fixture
        from backend.review.policy import assess_run
        from backend.models.event import CanonicalTimetable
        _,run=fixture();g=run.graph()
        input=CalendarInput(packet=CanonicalTimetable(source=g.source,evidence=g.evidence,events=run.events()),
                            reconstruction_status=run.search_status,assessments=assess_run(run))
        r=self.client.post('/api/calendar/v1/eligibility',json=input.model_dump(mode='json'))
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['events'][0]['status'],'BLOCKED')
        self.assertIn('MISSING_STABLE_KEY',r.json()['events'][0]['reason_codes'])


class FrontendSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]
        self.html=(self.root/'frontend/index.html').read_text(encoding='utf-8')
        self.js=(self.root/'frontend/js/app.js').read_text(encoding='utf-8')

    def test_legacy_ui_is_explicitly_demo_and_not_authorized(self):
        self.assertIn('Calendar NOT AUTHORIZED',self.html)
        self.assertIn('Legacy READY and Approve labels are display-only',self.html)
        self.assertIn('id="syncButton" disabled',self.html)
        self.assertIn('Demo extraction history',self.html)

    def test_bulk_labels_are_not_backend_approval(self):
        self.assertIn('no backend approval',self.js)
        self.assertIn('Demo reviewed',self.js)
        self.assertNotIn('Events approved',self.js)

    def test_sync_handler_cannot_report_success(self):
        part=self.js[self.js.index('function setupCalendarControls()'):self.js.index('function renderCalendar()')]
        self.assertIn('button.disabled = true',part)
        self.assertIn('Calendar writes blocked',part)
        self.assertNotIn('fetch(',part)
        self.assertNotIn('pre-commit check',part)

    def test_no_new_calendar_network_wiring_and_intranet_preserved(self):
        self.assertNotIn('/api/calendar',self.js)
        self.assertIn('async function connectLiveIntranet',self.js)
        self.assertIn('loadSampleTop',self.html)

    def test_javascript_syntax(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable for frontend syntax verification')
        result=subprocess.run([node,'--check',str(self.root/'frontend/js/app.js')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
