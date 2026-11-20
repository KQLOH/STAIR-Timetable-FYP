"""Offline institutional transport: no real login, credentials or network."""
import unittest
from unittest.mock import Mock
import requests
from backend.services.tarumt_client import TARUMTClient,TARUMTNavigationError
class PortalTransportTests(unittest.TestCase):
    def setUp(self):
        self.portal=TARUMTClient();self.portal.session.close();self.portal.session=Mock();self.addCleanup(self.portal.session.close)
    def response(self,status=200,location=None,text='OK'):
        r=requests.Response();r.status_code=status;r.url=self.portal.LOGIN_URL;r._content=text.encode();r._content_consumed=True
        if location:r.headers['Location']=location
        return r
    def test_successful_same_origin_request(self):
        self.portal.session.get.return_value=self.response();self.assertEqual(self.portal._get(self.portal.LOGIN_URL).status_code,200)
        self.assertFalse(self.portal.session.get.call_args.kwargs['allow_redirects'])
    def test_off_origin_and_invalid_urls_never_sent(self):
        for url in ('https://external.invalid/','http://web.tarc.edu.my/portal/','https://web.tarc.edu.my:444/','https://user:pass@web.tarc.edu.my/','https://web.tarc.edu.my.evil.invalid/','https://web.tarc.edu.my/\nsecret'):
            with self.subTest(url=url),self.assertRaises(TARUMTNavigationError):self.portal._get(url)
        self.portal.session.get.assert_not_called()
    def test_cross_origin_post_redirect_cannot_forward_credentials(self):
        self.portal.session.post.return_value=self.response(307,'https://external.invalid/steal')
        with self.assertRaises(TARUMTNavigationError) as e:self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC_SECRET'})
        self.assertNotIn('SYNTHETIC_SECRET',str(e.exception));self.assertEqual(self.portal.session.post.call_count,1);self.portal.session.get.assert_not_called()
    def test_same_origin_303_does_not_repost_credentials(self):
        self.portal.session.post.return_value=self.response(303,'/portal/home.jsp');self.portal.session.get.return_value=self.response()
        self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC_SECRET'})
        self.assertEqual(self.portal.session.post.call_count,1);self.assertNotIn('data',self.portal.session.get.call_args.kwargs)
    def test_same_origin_307_preserves_legitimate_post(self):
        self.portal.session.post.side_effect=[self.response(307,'/portal/continue.jsp'),self.response()]
        self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC_SECRET'})
        self.assertEqual(self.portal.session.post.call_count,2)
    def test_same_origin_fragment_redirect_is_not_a_server_origin_change(self):
        self.portal.session.post.return_value=self.response(302,self.portal.LOGIN_URL+'#SYNTHETIC_SECTION')
        self.portal.session.get.return_value=self.response(text='SYNTHETIC authenticated response')
        self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC_SECRET'})
        self.assertEqual(self.portal.session.post.call_count,1);self.assertEqual(self.portal.session.get.call_args.args[0],self.portal.LOGIN_URL)
        self.assertNotIn('data',self.portal.session.get.call_args.kwargs)
    def test_fragment_normalization_never_allows_external_origin_or_http(self):
        for target in ('https://external.invalid/#SYNTHETIC','http://web.tarc.edu.my/portal/#SYNTHETIC','https://web.tarc.edu.my:444/portal/#SYNTHETIC'):
            self.portal.session.post.reset_mock();self.portal.session.post.return_value=self.response(307,target)
            with self.subTest(target=target),self.assertRaises(TARUMTNavigationError):self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC_SECRET'})
            self.assertEqual(self.portal.session.post.call_count,1)
        self.portal.session.get.assert_not_called()
    def test_response_fragment_is_removed_without_weakening_origin_validation(self):
        response=self.response();response.url=self.portal.LOGIN_URL+'#SYNTHETIC'
        self.portal.session.get.return_value=response
        result=self.portal._get(self.portal.LOGIN_URL);self.assertEqual(result.url,self.portal.LOGIN_URL)
    def test_navigation_rejection_reason_is_fixed_and_private(self):
        for url,reason in [('https://external.invalid/','ORIGIN_NOT_ALLOWED'),('http://web.tarc.edu.my/','HTTPS_REQUIRED')]:
            with self.subTest(reason=reason),self.assertRaises(TARUMTNavigationError) as caught:self.portal._get(url)
            self.assertEqual(caught.exception.reason,reason);self.assertNotIn(url,str(caught.exception))
    def test_same_origin_redirect_query_spaces_match_requests_preparation(self):
        self.portal.session.post.return_value=self.response(302,self.portal.LOGIN_URL+'?SYNTHETIC_LABEL=TWO WORDS')
        self.portal.session.get.return_value=self.response()
        self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC'})
        self.assertEqual(self.portal.session.get.call_args.args[0],self.portal.LOGIN_URL+'?SYNTHETIC_LABEL=TWO%20WORDS')
        self.assertNotIn('data',self.portal.session.get.call_args.kwargs)
    def test_path_and_query_spaces_encoded_but_controls_refused(self):
        self.assertEqual(self.portal._trusted_url(self.portal.BASE_URL+'SYNTHETIC PATH?label=TWO WORDS'),self.portal.BASE_URL+'SYNTHETIC%20PATH?label=TWO%20WORDS')
        for value in ('\r','\n','\t','\x00','\x7f'):
            with self.subTest(control=repr(value)),self.assertRaises(TARUMTNavigationError):self.portal._trusted_url(self.portal.LOGIN_URL+'?label=SYNTHETIC'+value)
    def test_raw_redirect_controls_cannot_disappear_during_urljoin(self):
        self.portal.session.post.return_value=self.response(302,self.portal.LOGIN_URL+'?label=SYNTHETIC\nDATA')
        with self.assertRaises(TARUMTNavigationError) as caught:self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC'})
        self.assertEqual(caught.exception.step,'REDIRECT_RESOLUTION');self.assertEqual(caught.exception.category,'CONTROL_CHARACTER');self.portal.session.get.assert_not_called()
    def test_redirect_base_is_actual_response_url(self):
        redirected=self.response(302,'?SYNTHETIC_LABEL=TWO WORDS');redirected.url=self.portal.TIMETABLE_SESSION_URL
        self.portal.session.post.return_value=redirected;self.portal.session.get.return_value=self.response()
        self.portal._request('POST',self.portal.LOGIN_URL,data={'password':'SYNTHETIC'})
        self.assertEqual(self.portal.session.get.call_args.args[0],self.portal.TIMETABLE_SESSION_URL+'?SYNTHETIC_LABEL=TWO%20WORDS')
    def test_userinfo_and_malformed_port_remain_rejected(self):
        for url in ('https://@web.tarc.edu.my/portal/','https://name:secret@web.tarc.edu.my/portal/','https://web.tarc.edu.my:BAD/portal/'):
            with self.subTest(url=url),self.assertRaises(TARUMTNavigationError):self.portal._get(url)
        self.portal.session.get.assert_not_called()
    def test_redirect_loop_bounded(self):
        self.portal.session.get.side_effect=lambda *a,**k:self.response(302,'/portal/login.jsp')
        with self.assertRaises(TARUMTNavigationError):self.portal._get(self.portal.LOGIN_URL)
        self.assertEqual(self.portal.session.get.call_count,9)
    def test_discovered_external_login_action_is_rejected(self):
        self.portal.session.get.return_value=self.response(text='<form action="https://external.invalid/steal"><input name="student_id"><input type="password" name="password"></form>')
        with self.assertRaises(TARUMTNavigationError):self.portal.login('SYNTHETIC_STUDENT','SYNTHETIC_PASSWORD')
        self.portal.session.post.assert_not_called()
    def test_logout_always_closes_even_when_redirect_refused(self):
        self.portal.logged_in=True;self.portal.session.get.return_value=self.response(302,'https://external.invalid/exit')
        self.portal.close();self.portal.session.close.assert_called_once();self.assertFalse(self.portal.logged_in)

class RequestsCookieRedirectCompatibility(unittest.TestCase):
    def test_legacy_and_guarded_session_keep_cookie_across_fragment_redirect(self):
        from requests.adapters import BaseAdapter
        from http.client import HTTPMessage
        from types import SimpleNamespace
        class SyntheticAdapter(BaseAdapter):
            def __init__(self):self.calls=[]
            def send(self,request,**kwargs):
                self.calls.append(request);r=requests.Response();r.request=request;r.url=request.url;r._content=b'SYNTHETIC';r._content_consumed=True
                msg=HTTPMessage()
                if request.method=='POST':
                    r.status_code=302;r.headers['Location']=TARUMTClient.LOGIN_URL+'?SYNTHETIC_LABEL=TWO WORDS#SYNTHETIC_SECTION'
                    value='SYNTHETIC_SESSION=SYNTHETIC_VALUE; Path=/portal/; Secure; HttpOnly'
                    r.headers['Set-Cookie']=value;msg.add_header('Set-Cookie',value)
                else:r.status_code=200
                r.raw=SimpleNamespace(_original_response=SimpleNamespace(msg=msg))
                return r
            def close(self):pass
        for guarded in (False,True):
            with self.subTest(guarded=guarded):
                p=TARUMTClient();a=SyntheticAdapter();p.session.mount('https://',a)
                try:
                    if guarded:r=p._request('POST',p.LOGIN_URL,data={'password':'SYNTHETIC'})
                    else:r=p.session.post(p.LOGIN_URL,data={'password':'SYNTHETIC'},allow_redirects=True)
                    self.assertEqual(r.status_code,200);self.assertEqual([x.method for x in a.calls],['POST','GET'])
                    self.assertIn('SYNTHETIC_SESSION=SYNTHETIC_VALUE',a.calls[1].headers.get('Cookie',''));self.assertIsNone(a.calls[1].body)
                    self.assertIn('TWO%20WORDS',a.calls[1].url)
                    if guarded:self.assertNotIn('#',a.calls[1].url)
                finally:p.session.cookies.clear();p.session.close()
