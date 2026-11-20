"""Synthetic FR2 transport/integration tests. No real accounts or network."""
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
import requests
from fastapi.testclient import TestClient
from backend import main
from backend.review.local_host import LocalReviewHost, create_host_app, ORIGIN
from backend.review.store import ReviewStore
from backend.review.auth import TrustedHostAuthority
from backend.services.tarumt_client import TARUMTClient, TARUMTLoginError, TARUMTNavigationError
from tests.fixtures import timetable_html

AUTH='<a href="logout.jsp">Logout</a><span class="user-info">SYNTHETIC</span>'
FORM='<form><input name="username"><input type="password" name="secret"><input type="hidden" name="csrf" value="SYNTHETIC_CSRF"></form>'
LINK='viewTimetable.jsp?fsid=SYNTHETIC-ID&fsession=203301&fbrncd=TEST'
def response(text,url=TARUMTClient.TIMETABLE_SESSION_URL):
    r=requests.Response();r.status_code=200;r.url=url;r._content=text.encode();r._content_consumed=True;return r

class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.p=TARUMTClient();self.p.session.close();self.p.session=Mock();self.addCleanup(self.p.session.close)
    def discovery(self,text):
        self.p.logged_in=True;self.p.session.get.return_value=response(AUTH+text);return self.p.discover_timetable_url()
    def test_empty_action_posts_to_actual_document_and_preserves_hidden_fields(self):
        self.p.session.get.return_value=response(FORM,self.p.LOGIN_URL);self.p.session.post.return_value=response(AUTH)
        self.p.login('SYNTHETIC','SYNTHETIC_PASSWORD');args,kw=self.p.session.post.call_args
        self.assertEqual(args[0],self.p.LOGIN_URL);self.assertEqual(kw['data']['csrf'],'SYNTHETIC_CSRF');self.assertTrue(self.p.logged_in)
    def test_unrecognized_response_never_creates_authenticated_state(self):
        self.p.session.get.side_effect=[response(FORM,self.p.LOGIN_URL),response('<h1>Service unavailable</h1>')]
        self.p.session.post.return_value=response('Challenge required')
        with self.assertRaises(TARUMTLoginError):self.p.login('SYNTHETIC','SYNTHETIC_PASSWORD')
        self.assertFalse(self.p.logged_in)
    def test_password_form_with_success_markers_is_not_authenticated(self):
        self.p.session.get.side_effect=[response(FORM,self.p.LOGIN_URL),response(AUTH+FORM)];self.p.session.post.return_value=response(AUTH+FORM)
        with self.assertRaises(TARUMTLoginError):self.p.login('SYNTHETIC','SYNTHETIC_PASSWORD')
    def test_confirmed_authenticated_probe_supported(self):
        self.p.session.get.side_effect=[response(FORM,self.p.LOGIN_URL),response(AUTH)];self.p.session.post.return_value=response('Redirecting')
        self.p.login('SYNTHETIC','SYNTHETIC_PASSWORD');self.assertTrue(self.p.logged_in)
    def test_disconnect_discards_cookie_jar_and_account_metadata(self):
        self.p.session.cookies=requests.cookies.RequestsCookieJar()
        self.p.session.cookies.set('SYNTHETIC_SESSION','SYNTHETIC_COOKIE')
        self.p.student_name='SYNTHETIC';self.p.last_timetable_url=self.p.LOGIN_URL;self.p.logged_in=True
        self.p.session.get.return_value=response('Logged out')
        self.p.close();self.assertEqual(len(self.p.session.cookies),0);self.assertIsNone(self.p.student_name);self.assertIsNone(self.p.last_timetable_url);self.assertFalse(self.p.logged_in)
    def test_session_open_does_not_inherit_unrelated_selected_week(self):
        url=self.discovery("<select id='fweek'><option selected value='9'>Week 9</option></select><button onclick=\"getTimetable('SYNTHETIC-ID','203301','TEST')\">View</button>")
        self.assertNotIn('fweek',parse_qs(urlsplit(url).query))
    def test_original_session_open_wins_over_earlier_week_callback(self):
        url=self.discovery("<button onclick=\"getWeek('SYNTHETIC-WEEK','203301','OTHER')\"></button><button onclick=\"getTimetable('SYNTHETIC-SESSION','203301','TEST')\">View</button>")
        self.assertEqual(parse_qs(urlsplit(url).query)['fsid'],['SYNTHETIC-SESSION'])
    def test_original_argument_trimming_preserved(self):
        url=self.discovery("<button onclick=\"getTimetable('  SYNTHETIC-ID  ','203301',' TEST ')\">View</button>")
        params=parse_qs(urlsplit(url).query);self.assertEqual(params['fsid'],['SYNTHETIC-ID']);self.assertEqual(params['fbrncd'],['TEST'])
    def test_navigation_failure_stage_does_not_claim_login_failure(self):
        with self.assertRaises(TARUMTNavigationError):self.discovery('<h1>Unexpected timetable navigation</h1>')
        self.assertTrue(self.p.logged_in);self.assertEqual(self.p.stage,'SESSION_NAVIGATION')
    def test_timetable_content_failure_stage_is_distinct(self):
        self.p.logged_in=True;self.p.session.get.side_effect=[response(AUTH+'<a href="'+LINK+'">View</a>'),response(AUTH+'<h1>No timetable available</h1>')]
        with self.assertRaises(TARUMTNavigationError):self.p.fetch_latest_timetable_html()
        self.assertEqual(self.p.stage,'TIMETABLE_CONTENT')
    def test_form_action_resolution_preserves_requests_url_preparation(self):
        target='https://web.tarc.edu.my/portal/loginProcess.jsp?SYNTHETIC_LABEL=TWO%20WORDS'
        for action in ('loginProcess.jsp?SYNTHETIC_LABEL=TWO WORDS','/portal/loginProcess.jsp?SYNTHETIC_LABEL=TWO WORDS','//web.tarc.edu.my/portal/loginProcess.jsp?SYNTHETIC_LABEL=TWO WORDS'):
            with self.subTest(action=action):
                self.p.session.get.return_value=response(FORM.replace('<form>','<form action="'+action+'">'),self.p.LOGIN_URL)
                self.p.session.post.return_value=response(AUTH)
                self.p.login('SYNTHETIC','SYNTHETIC_PASSWORD');self.assertEqual(self.p.session.post.call_args.args[0],target)
    def test_invalid_form_and_redirect_have_distinct_safe_diagnostics(self):
        self.p.session.get.return_value=response(FORM.replace('<form>','<form action="https://@web.tarc.edu.my/portal/loginProcess.jsp">'),self.p.LOGIN_URL)
        with self.assertRaises(TARUMTNavigationError) as caught:self.p.login('SYNTHETIC','SYNTHETIC_PASSWORD')
        self.assertEqual(caught.exception.step,'FORM_ACTION_VALIDATION');self.assertEqual(caught.exception.category,'USERINFO');self.p.session.post.assert_not_called()
    def test_original_javascript_navigation_still_works(self):
        url=self.discovery("<button onclick=\"getTimetable('SYNTHETIC-ID','203301','TEST')\">View</button>")
        self.assertEqual(parse_qs(urlsplit(url).query)['fsid'],['SYNTHETIC-ID'])
    def test_html_escaped_direct_link_now_reachable(self):
        url=self.discovery('<a href="'+LINK.replace('&','&amp;')+'">View</a>');self.assertEqual(parse_qs(urlsplit(url).query)['fbrncd'],['TEST'])
    def test_getweek_navigation_and_selected_week(self):
        url=self.discovery("<button onclick=\"getWeek('SYNTHETIC-ID','203301','TEST')\"></button><select id='fweek'><option selected value='3'>Week 3</option></select>")
        self.assertEqual(parse_qs(urlsplit(url).query)['fweek'],['3'])
    def test_get_form_encodes_source_parameters(self):
        url=self.discovery('<form action="viewTimetable.jsp"><input name="fsid" value="SYNTHETIC + ID"><input name="fsession" value="203301"><select name="fbrncd"><option selected value="TEST">Test</option></select></form>')
        self.assertEqual(parse_qs(urlsplit(url).query)['fsid'],['SYNTHETIC + ID'])
    def test_incomplete_or_conflicting_values_not_guessed(self):
        for query in ('fsid=TEST&fsession=203301','fsid=TEST&fsession=203301&fbrncd=','fsid=TEST&fsession=203301&fbrncd=A&fbrncd=B'):
            with self.subTest(query=query),self.assertRaises(TARUMTNavigationError):self.discovery('<a href="viewTimetable.jsp?'+query+'">View</a>')
    def test_external_timetable_link_not_returned(self):
        with self.assertRaises(TARUMTNavigationError):self.discovery('<a href="https://external.invalid/'+LINK+'">View</a>')
    def test_post_form_not_silently_changed_to_get(self):
        with self.assertRaises(TARUMTNavigationError):self.discovery('<form method="post" action="'+LINK+'"></form>')
    def test_expired_session_resets_authenticated_state(self):
        self.p.logged_in=True;self.p.session.get.return_value=response(FORM)
        with self.assertRaises(TARUMTLoginError):self.p.discover_timetable_url()
        self.assertFalse(self.p.logged_in)
    def test_existing_timetable_redirect_can_be_used(self):
        self.p.logged_in=True;self.p.session.get.return_value=response(AUTH,self.p.TIMETABLE_SESSION_URL.replace('viewSession.jsp',LINK))
        self.assertIn('fsid=SYNTHETIC-ID',self.p.discover_timetable_url())

class StudentHostRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.authority=TrustedHostAuthority();self.host=LocalReviewHost(ReviewStore(self.authority),'SYNTHETIC-HOST',identity=lambda:'SYNTHETIC-HOST')
        self.c=TestClient(create_host_app(self.host),base_url=ORIGIN,client=('127.0.0.1',41000));self.addCleanup(self.c.close)
        self.headers={'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'}
        p=patch.dict(main.CONNECTED_CLIENTS,{},clear=True);p.start();self.addCleanup(p.stop)
        self.portal=Mock(student_name='SYNTHETIC',last_timetable_url=TARUMTClient.TIMETABLE_SESSION_URL)
        self.portal.fetch_latest_timetable_html.return_value=timetable_html()
        p=patch.object(main,'TARUMTClient',return_value=self.portal);p.start();self.addCleanup(p.stop)
    def connect(self):return self.c.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC_PASSWORD'},headers=self.headers)
    def test_connect_refresh_disconnect_through_student_host_without_review_grant(self):
        first=self.connect();self.assertEqual(first.status_code,200,first.text);self.assertEqual(first.headers['cache-control'],'no-store');body=first.json();self.assertTrue(body['data']['events'])
        token=body['connection_token'];self.assertEqual(self.c.post('/api/intranet/timetable',json={'token':token},headers=self.headers).status_code,200)
        self.assertEqual(self.c.post('/api/intranet/disconnect',json={'token':token},headers=self.headers).status_code,200);self.portal.close.assert_called_once()
        self.assertEqual(len(main.CONNECTED_CLIENTS),0);self.assertEqual(len(self.authority._grants),0)
        self.assertEqual(self.c.get('/api/review/session',headers=self.headers).status_code,403)
    def test_cross_origin_or_review_credentials_never_reach_institution(self):
        for extra in ({'Origin':'https://external.invalid'},{'Sec-Fetch-Site':'cross-site'},{'Authorization':'Bearer SYNTHETIC'},{'X-STAIR-Review-Binding':'SYNTHETIC'},{'Host':'external.invalid:8020'}):
            with self.subTest(extra=extra):
                r=self.c.post('/api/intranet/connect',json={'student_id':'SYNTHETIC','password':'SYNTHETIC_PASSWORD'},headers={**self.headers,**extra});self.assertEqual(r.status_code,403)
        self.portal.login.assert_not_called()
    def test_unapproved_methods_routes_queries_and_content_type_refused(self):
        for method,path,headers in [('GET','/api/intranet/connect',{}),('POST','/api/intranet/timetable/sample',{}),('POST','/api/intranet/connect?password=SYNTHETIC',{}),('POST','/api/intranet/connect',{'Content-Type':'text/plain'})]:
            with self.subTest(path=path,method=method):self.assertEqual(self.c.request(method,path,json={},headers={**self.headers,**headers}).status_code,403)
        self.portal.login.assert_not_called()
    def test_failed_authentication_closes_client_without_token_or_grant(self):
        self.portal.login.side_effect=TARUMTLoginError('SYNTHETIC_PRIVATE_DETAIL');r=self.connect();self.assertEqual(r.status_code,401);self.assertNotIn('SYNTHETIC_PRIVATE_DETAIL',r.text)
        self.portal.close.assert_called_once();self.assertEqual(len(main.CONNECTED_CLIENTS),0);self.assertEqual(len(self.authority._grants),0)
    def test_502_identifies_stage_without_private_details(self):
        for stage in ('LOGIN_PAGE','LOGIN_SUBMISSION','LOGIN_CONFIRMATION','SESSION_PAGE','SESSION_NAVIGATION','TIMETABLE_FETCH','TIMETABLE_CONTENT'):
            with self.subTest(stage=stage):
                self.portal.reset_mock();self.portal.stage=stage
                self.portal.login.side_effect=TARUMTNavigationError('SYNTHETIC_PRIVATE_URL_COOKIE_PASSWORD') if stage.startswith('LOGIN_') else None
                self.portal.fetch_latest_timetable_html.side_effect=TARUMTNavigationError('SYNTHETIC_PRIVATE_URL_COOKIE_PASSWORD')
                result=self.connect();self.assertEqual(result.status_code,502);self.assertEqual(result.json()['stage'],stage)
                self.assertNotIn('SYNTHETIC_PRIVATE',result.text);self.assertNotIn('connection_token',result.json());self.assertEqual(len(main.CONNECTED_CLIENTS),0);self.portal.close.assert_called_once()
    def test_502_exposes_only_fixed_transport_reasons(self):
        self.portal.stage='LOGIN_SUBMISSION'
        for reason in ('HTTPS_REQUIRED','ORIGIN_NOT_ALLOWED','INVALID_TARGET','REDIRECT_MISSING','REDIRECT_LIMIT','SYNTHETIC_PRIVATE_COOKIE'):
            error=TARUMTNavigationError('SYNTHETIC_PRIVATE_RESPONSE');error.reason=reason
            self.portal.login.side_effect=error
            result=self.connect();self.assertEqual(result.status_code,502)
            self.assertEqual(result.json()['reason'],reason if reason!='SYNTHETIC_PRIVATE_COOKIE' else 'NAVIGATION_UNCONFIRMED')
            self.assertNotIn('SYNTHETIC_PRIVATE',result.text)
    def test_502_diagnostic_is_structural_and_allowlisted(self):
        error=TARUMTNavigationError('SYNTHETIC_PRIVATE_URL');error.reason='INVALID_TARGET';error.step='REDIRECT_VALIDATION';error.category='USERINFO';error.form='ABSOLUTE'
        self.portal.login.side_effect=error;self.portal.stage='LOGIN_SUBMISSION'
        result=self.connect();self.assertEqual(result.status_code,502);self.assertEqual(result.json()['diagnostic'],{'step':'REDIRECT_VALIDATION','category':'USERINFO','form':'ABSOLUTE'})
        error.step=error.category=error.form='SYNTHETIC_PRIVATE_TOKEN'
        result=self.connect();self.assertEqual(result.json()['diagnostic'],{'step':'UNCLASSIFIED','category':'UNCLASSIFIED','form':'UNCLASSIFIED'});self.assertNotIn('SYNTHETIC_PRIVATE',result.text)
    def test_502_never_echoes_arbitrary_stage_value(self):
        self.portal.stage='SYNTHETIC_PRIVATE_SESSION_ID';self.portal.fetch_latest_timetable_html.side_effect=TARUMTNavigationError('SYNTHETIC_PRIVATE_BODY')
        result=self.connect();self.assertEqual(result.status_code,502);self.assertEqual(result.json()['stage'],'TIMETABLE_FETCH');self.assertNotIn('SYNTHETIC_PRIVATE',result.text)
    def test_expired_refresh_cleans_up_session_and_fails_closed(self):
        token=self.connect().json()['connection_token'];self.portal.fetch_latest_timetable_html.side_effect=TARUMTLoginError('EXPIRED')
        self.assertEqual(self.c.post('/api/intranet/timetable',json={'token':token},headers=self.headers).status_code,401);self.assertNotIn(token,main.CONNECTED_CLIENTS);self.portal.close.assert_called_once()
