"""Synthetic account, ownership, revision and calendar workflow regression tests.

Memory journals exercise the same transaction contract without private files.
No institutional account, Google account, Gold or formal evaluation is used.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
import os
import tempfile
from threading import RLock
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.student.accounts import Accounts
from backend.student.api import create_student_app
from backend.student.google import GoogleCalendar
from backend.student import workflow
from backend.review.models import ReviewError
from backend.review.models import CorrectionSubmission, FieldCorrection
from backend.reconstruction.models import digest
from test_m5_policy import fixture
from test_m4_reconstruction import SINGLE

ORIGIN = 'http://127.0.0.1:8020'


class MemoryJournal:
    def __init__(self):
        self.state = None
        self.lock = RLock()

    @contextmanager
    def locked(self):
        with self.lock:
            yield

    def load(self):
        return deepcopy(self.state), 0, None

    def append(self, state):
        self.state = deepcopy(state)


def test_accounts():
    journals = {}
    factory = lambda name: journals.setdefault(name, MemoryJournal())
    return Accounts(factory('accounts'), factory)


class StudentAccountTests(unittest.TestCase):
    def setUp(self):
        self.accounts = test_accounts()
        self.app = create_student_app(self.accounts)
        self.client = TestClient(self.app, base_url=ORIGIN, client=('127.0.0.1', 44000))
        self.headers = {'Origin': ORIGIN}

    def login(self, username='student_a', create=True, client=None):
        client = client or self.client
        response = client.post('/api/student/' + ('register' if create else 'login'),
            headers={'Origin': ORIGIN}, json={'username': username, 'password': 'synthetic-password-123'})
        self.assertEqual(response.status_code, 200, response.text)
        headers = {'Origin': ORIGIN, 'X-STAIR-CSRF': response.json()['csrf']}
        if client is self.client:
            self.headers = headers
        return headers

    def enroll(self, text=SINGLE):
        request, _ = fixture(text)
        source = request.source_packet.source
        response = self.client.post('/api/student/source', content=text.encode(), headers={**self.headers,
            'X-STAIR-Source-ID': source.source_document_id, 'X-STAIR-Source-SHA256': source.content_sha256})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post('/api/review/ingest', json=request.model_dump(mode='json'), headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()['task_ids'][0]
        task = self.client.get('/api/review/tasks/' + task_id).json()['task']
        return task, request

    def schedule(self, task, **overrides):
        body = {'task_id': task['task_id'], 'revision_id': task['current_event']['revision']['revision_id'],
                'frequency': 'ONCE', 'first_date': '2026-11-23', 'timezone': 'Asia/Kuala_Lumpur',
                'source_note': 'Invented regression timetable page 1', 'checked_original': True, **overrides}
        return self.client.post('/api/student/schedule', json=body, headers=self.headers)

    def plan(self, task):
        self.assertEqual(self.schedule(task).status_code, 200)
        result = self.client.post('/api/student/calendar/preview', json={'task_ids': [task['task_id']], 'target': 'local'}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def approve(self, plan):
        return self.client.post('/api/student/calendar/approve', json={'plan_id': plan['id'], 'confirmed': True}, headers=self.headers)

    def execute(self, plan):
        return self.client.post('/api/student/calendar/execute', json={'plan_id': plan['id']}, headers=self.headers)

    def test_anonymous_upload_and_review_denied(self):
        for path in ('/api/student/workspace', '/api/review/queue'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/extract', headers=self.headers, content=b'private').status_code, 401)

    def test_registration_password_hashed_cookie_httponly(self):
        self.login()
        with self.accounts.transaction() as state:
            row = state['accounts']['student_a']
            self.assertNotIn('synthetic-password', json.dumps(row))
            self.assertEqual(len(row['password_hash']), 128)
        session = self.client.get('/api/student/session')
        self.assertEqual(session.json()['user']['username'], 'student_a')
        self.assertEqual(session.headers['cache-control'], 'no-store')
        response = self.client.post('/api/student/login', headers={'Origin': ORIGIN},
            json={'username': 'student_a', 'password': 'synthetic-password-123'})
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=lax', response.headers['set-cookie'])

    def test_login_error_never_echoes_password(self):
        response = self.client.post('/api/student/register', headers=self.headers,
            json={'username': 'abc', 'password': {'password': 'private-secret'}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private-secret', response.text)

    def test_cross_origin_and_missing_csrf_denied(self):
        self.login()
        for headers in ({'Origin': ORIGIN}, {'Origin': 'https://attacker.example', 'X-STAIR-CSRF': self.headers['X-STAIR-CSRF']}):
            self.assertEqual(self.client.post('/api/student/logout', headers=headers).status_code, 403)

    def test_host_and_nonloopback_denied(self):
        self.assertEqual(self.client.get('/', headers={'Host': 'attacker.example'}).status_code, 403)
        foreign = TestClient(self.app, base_url=ORIGIN, client=('192.0.2.1', 44000))
        self.assertEqual(foreign.get('/').status_code, 403)

    def test_password_backoff(self):
        self.login()
        for _ in range(8):
            self.client.post('/api/student/login', headers={'Origin': ORIGIN},
                json={'username': 'student_a', 'password': 'incorrect-password'})
        response = self.client.post('/api/student/login', headers={'Origin': ORIGIN},
                json={'username': 'student_a', 'password': 'synthetic-password-123'})
        self.assertEqual(response.status_code, 429)

    def test_logout_revokes_session_and_review(self):
        self.login(); self.enroll()
        cookie = self.client.cookies.get('stair_student_session')
        self.assertEqual(self.client.post('/api/student/logout', headers=self.headers).status_code, 200)
        self.client.cookies.set('stair_student_session', cookie)
        self.assertEqual(self.client.get('/api/review/queue').status_code, 401)

    def test_review_can_reconnect_without_console_after_ending_editing(self):
        self.login();task,_=self.enroll()
        self.assertEqual(self.client.post('/api/review/session/end',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/review/queue').status_code,403)
        response=self.client.post('/api/student/review/connect',headers=self.headers,json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/review/tasks/'+task['task_id']).status_code,200)

    def test_user_cannot_read_other_users_task_or_calendar(self):
        self.login(); task, _ = self.enroll(); plan = self.plan(task);self.approve(plan);self.execute(plan)
        other = TestClient(self.app, base_url=ORIGIN, client=('127.0.0.1', 44001))
        other_headers = self.login('student_b', client=other)
        self.assertEqual(other.get('/api/student/workspace').json()['entries'], {})
        self.assertNotEqual(other.get('/api/review/tasks/' + task['task_id']).status_code, 200)
        self.assertEqual(other.post('/api/student/calendar/execute',json={'plan_id': plan['id']},headers=other_headers).status_code,404)
        # Even possessing the same bytes grants access only to a separate per-user store.
        other.post('/api/student/source', content=SINGLE.encode(), headers={**other_headers,
            'X-STAIR-Source-ID': 'synthetic-capture', 'X-STAIR-Source-SHA256': sha256(SINGLE.encode()).hexdigest()})
        self.assertEqual(other.get('/api/review/tasks/' + task['task_id']).status_code, 404)

    def test_original_hash_required_and_scope_cannot_be_forged(self):
        self.login()
        response=self.client.post('/api/student/source', content=b'wrong file', headers={**self.headers,
            'X-STAIR-Source-ID':'synthetic-capture','X-STAIR-Source-SHA256':'a'*64})
        self.assertEqual(response.status_code, 422)
        task, request=self.enroll()
        changed=request.model_copy(update={'source_packet':request.source_packet.model_copy(update={
            'source':request.source_packet.source.model_copy(update={'content_sha256':'b'*64})})})
        response=self.client.post('/api/review/ingest',headers=self.headers,json=changed.model_dump(mode='json'))
        self.assertEqual(response.status_code,403)

    def test_local_preview_approval_execute_and_no_duplicate_replay(self):
        self.login();task,_=self.enroll();plan=self.plan(task)
        self.assertEqual(plan['operations'][0]['action'],'CREATE')
        self.assertEqual(self.execute(plan).status_code,409)
        self.assertEqual(self.approve(plan).status_code,200)
        first=self.execute(plan)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['status'],'SUCCEEDED')
        self.assertEqual(self.execute(plan).json(),first.json())
        self.assertEqual(len(self.client.get('/api/student/workspace').json()['entries']),1)
        next_plan=self.client.post('/api/student/calendar/preview',json={'task_ids':[task['task_id']],'target':'local'},headers=self.headers).json()
        self.assertEqual(next_plan['operations'][0]['action'],'NO_OP')

    def test_weekly_range_and_exclusions(self):
        self.login();task,_=self.enroll()
        response=self.schedule(task,frequency='WEEKLY',last_date='2026-12-07',excluded_dates=['2026-11-30'])
        self.assertEqual(response.status_code,200,response.text)
        plan=self.client.post('/api/student/calendar/preview',json={'task_ids':[task['task_id']],'target':'local'},headers=self.headers).json()
        self.assertEqual(len(plan['operations']),2)
        self.assertEqual(plan['operations'][1]['body']['start']['dateTime'],'2026-12-07T09:00:00+08:00')

    def test_dates_zone_repeat_bounds_checked(self):
        self.login();task,_=self.enroll()
        for overrides in ({'first_date':'2026-11-24'}, {'timezone':'Mars/Olympus'},
                          {'frequency':'WEEKLY'}, {'frequency':'WEEKLY','last_date':'2030-01-01'},
                          {'checked_original':False}, {'excluded_dates':['2026-11-23']}):
            response=self.schedule(task,**overrides)
            self.assertEqual(response.status_code,422,(overrides,response.text))

    def test_old_revision_and_expired_plan_rejected(self):
        self.login();task,_=self.enroll()
        self.assertEqual(self.schedule(task,revision_id='stale-revision').status_code,409)
        plan=self.plan(task);self.approve(plan)
        real_clock=self.accounts.clock
        self.accounts.clock=lambda:real_clock()+601
        self.assertEqual(self.execute(plan).status_code,409)

    def test_schedule_edit_invalidates_previous_approval(self):
        self.login();task,_=self.enroll();plan=self.plan(task);self.approve(plan)
        self.assertEqual(self.schedule(task,first_date='2026-11-30').status_code,200)
        self.assertEqual(self.execute(plan).status_code,404)

    def test_saved_correction_invalidates_approved_revision_even_if_same_value(self):
        self.login();task,_=self.enroll();plan=self.plan(task);self.approve(plan)
        session=self.accounts.authenticate(self.client.cookies.get('stair_student_session'))
        store=self.accounts.store(session);principal=session.principal
        current=store.get(task['task_id'],principal)
        current=store.assign(current.task_id,principal.actor_id,current.etag,principal)
        current=store.start(current.task_id,current.etag,principal)
        result=store.submit(current.task_id,CorrectionSubmission(request_id=uuid4(),
            expected_revision_id=current.current_event.revision.revision_id,expected_etag=current.etag,
            reason='Synthetic regression: source-confirm the same room in a new revision',
            fields=(FieldCorrection(field_name='location',state='PRESENT',value='A1',raw_value='A1',evidence_ids=(store.evidence(current.task_id,principal)[0].evidence_id,)),)),principal)
        self.assertTrue(result['applied'],result.get('error_code'))
        response=self.execute(plan)
        self.assertEqual(response.status_code,409,response.text)

    def test_same_time_new_capture_cannot_duplicate_saved_class(self):
        self.login();task,_=self.enroll();plan=self.plan(task);self.approve(plan);self.execute(plan)
        task2,_=self.enroll(SINGLE+' ')
        self.assertEqual(self.schedule(task2).status_code,200)
        response=self.client.post('/api/student/calendar/preview',json={'task_ids':[task2['task_id']],'target':'local'},headers=self.headers)
        self.assertEqual(response.status_code,409,response.text)

    def test_durable_remote_uncertainty_does_not_resend(self):
        self.login();task,_=self.enroll();self.schedule(task)
        session=self.accounts.authenticate(self.client.cookies.get('stair_student_session'))
        class Remote:
            target='google:invented-calendar'
            calls=0
            def owned_entries(self):return {}
            def read(self,key):return None
            def content(self,value):return value
            def apply(self,operation):
                self.calls+=1
                raise ReviewError('Synthetic timeout after submission',502)
        remote=Remote()
        plan=workflow.local_preview(self.accounts,session,[task['task_id']],remote.target,remote)
        workflow.approve(self.accounts,session,plan['id'])
        first=workflow.execute(self.accounts,session,plan['id'],remote)
        self.assertEqual(first['status'],'CHECK_REQUIRED')
        self.assertEqual(workflow.execute(self.accounts,session,plan['id'],remote),first)
        self.assertEqual(remote.calls,1)

    def test_target_change_rejects_stale_approved_plan(self):
        self.login();task,_=self.enroll();plan=self.plan(task);self.approve(plan)
        session=self.accounts.authenticate(self.client.cookies.get('stair_student_session'))
        with self.accounts.transaction(write=True) as state:
            state['workspaces'][session.user_id]['entries']['different']={'title':'synthetic concurrent change'}
        self.assertEqual(self.execute(plan).status_code,409)

    def test_same_account_restart_retains_source_and_revision_not_approval_authority(self):
        self.login();task,_=self.enroll();plan=self.plan(task);self.approve(plan)
        # New sessions restore the encrypted-journal contract, not old cookie authority.
        self.client.post('/api/student/logout',headers=self.headers)
        self.accounts.stores.clear()
        self.login(create=False)
        workspace=self.client.get('/api/student/workspace').json()
        self.assertEqual(workspace['tasks'][0]['task_id'],task['task_id'])
        self.assertEqual(self.execute(plan).status_code,409)

    def test_personal_completeness_never_claims_independence(self):
        self.login();task,_=self.enroll();self.schedule(task)
        response=self.client.post('/api/student/completeness',headers=self.headers,json={'task_ids':[task['task_id']],
            'original_pages_checked':True,'groups_checked':True,'no_missing_classes':True,'exceptions_checked':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['complete_schedule_verified'])

    def test_export_compare_original_and_current_separate(self):
        self.login();task,_=self.enroll()
        exported=self.client.get('/api/student/export/'+task['task_id']).json()
        self.assertEqual(len(exported['original_predictions']),1)
        self.assertFalse(exported['automatic_accept_enabled'])
        reference={**exported['reference_template'],'checked_by':'Invented test reviewer','note':'Synthetic unit fixture only',
            'events':[{'candidate_id':task['current_event']['candidate_id'],'content':task['current_event']['revision']['content']}]}
        response=self.client.post('/api/student/compare',headers=self.headers,json={'task_id':task['task_id'],'reference':reference})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['original_predictions']['exact_matches']),1)
        self.assertEqual(response.json()['reference_trust'],'USER_SUPPLIED_NOT_INDEPENDENTLY_ATTESTED')
        reference['source_sha256']='f'*64
        self.assertEqual(self.client.post('/api/student/compare',headers=self.headers,json={'task_id':task['task_id'],'reference':reference}).status_code,409)

    def test_real_google_disabled_without_configuration(self):
        self.login()
        with patch.dict('os.environ',{},clear=True):
            info=self.client.get('/api/student/session').json()
            self.assertFalse(info['google']['configured'])
            self.assertEqual(self.client.post('/api/student/google/connect',headers=self.headers,json={}).status_code,409)

    def test_intranet_token_from_another_session_is_rejected_before_networking(self):
        self.login()
        with patch('backend.main.refresh_tarumt_timetable') as refresh:
            response=self.client.post('/api/intranet/timetable',headers=self.headers,json={'token':'somebody-elses-token'})
            self.assertEqual(response.status_code,403)
            refresh.assert_not_called()
        self.assertEqual(self.client.get('/api/intranet/timetable/sample').status_code,403)


class GoogleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.accounts=test_accounts()
        _,self.session=self.accounts.sign_in('synthetic_user','synthetic-password-123',create=True)
        self.google=GoogleCalendar(self.session,ORIGIN,clock=self.accounts.clock,config={
            'STAIR_GOOGLE_CLIENT_ID':'invented-id','STAIR_GOOGLE_CLIENT_SECRET':'invented-secret',
            'STAIR_GOOGLE_TEST_CALENDAR_ID':'invented@group.calendar.google.com'})

    def test_oauth_state_pkce_and_one_time_callback(self):
        url=self.google.start()
        self.assertIn('code_challenge_method=S256',url)
        with self.assertRaises(ReviewError):self.google.callback('wrong-state','invented-code')
        self.assertEqual(self.session.oauth,{})

    def test_primary_target_disallowed(self):
        self.google.calendar_id='primary'
        self.assertFalse(self.google.configured)

    def test_timeout_never_automatically_retries(self):
        body={'summary':'Synthetic class'}
        op={'key':'a'*64,'action':'CREATE','body':body,'before':None,'etag':None}
        with patch.object(self.google,'read',return_value=None),patch.object(self.google,'request',side_effect=ReviewError('timeout',502)) as request:
            with self.assertRaises(ReviewError):self.google.apply(op)
            self.assertEqual(request.call_count,1)

    def test_unowned_remote_event_cannot_be_overwritten(self):
        current={'summary':'Someone else','extendedProperties':{'private':{'stairAccount':'other'}}}
        op={'key':'a'*64,'action':'UPDATE','body':{'summary':'Desired'},'before':self.google.content(current),'etag':'old'}
        with patch.object(self.google,'read',return_value=current),patch.object(self.google,'request') as request:
            self.assertEqual(self.google.apply(op),'CHECK_REQUIRED')
            request.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI and user-local storage are required')
class StudentPersistenceTests(unittest.TestCase):
    def test_account_review_and_calendar_survive_dpapi_restart_without_sessions(self):
        from pathlib import Path
        from backend.review.persistence import ReviewJournal
        from backend.review.secure_directory import user_local_root
        from backend.review.windows_security import current_actor
        actor=current_actor()
        with tempfile.TemporaryDirectory(prefix='STAIR-account-regression-',dir=user_local_root()) as temp:
            directory=Path(temp)/'accounts'
            journal=ReviewJournal(directory,actor)
            factory=lambda name:ReviewJournal(journal.directory/name,actor)
            accounts=Accounts(journal,factory)
            token,session=accounts.sign_in('synthetic_persistent','invented-local-password',create=True)
            request,_=fixture();accounts.enroll_source(session,request.source_packet.source)
            store=accounts.store(session);task_id=store.ingest(request,session.principal)[0]
            task=store.get(task_id,session.principal)
            schedule=workflow.Schedule(task_id=task_id,revision_id=str(task.current_event.revision.revision_id),
                frequency='ONCE',first_date=date(2026,11,23),timezone='Asia/Kuala_Lumpur',
                source_note='Invented persistent timetable',checked_original=True)
            workflow.save_schedule(accounts,session,schedule)
            plan=workflow.local_preview(accounts,session,[task_id]);workflow.approve(accounts,session,plan['id'])
            workflow.execute(accounts,session,plan['id'])
            with accounts.transaction() as state:
                serialized=json.dumps(state)
                self.assertNotIn(token,serialized);self.assertNotIn(session.csrf,serialized)
            for record in journal.records.iterdir():
                raw=record.read_bytes()
                self.assertNotIn(b'invented-local-password',raw)
                self.assertNotIn(b'synthetic_persistent',raw)
            restarted=Accounts(ReviewJournal(directory,actor),factory)
            with self.assertRaises(ReviewError):restarted.authenticate(token)
            _,fresh=restarted.sign_in('synthetic_persistent','invented-local-password')
            restarted.connect_review(fresh)
            self.assertEqual(restarted.store(fresh).get(task_id,fresh.principal).task_id,task.task_id)
            with restarted.transaction() as state:
                self.assertEqual(len(state['workspaces'][fresh.user_id]['entries']),1)
            with self.assertRaises(ReviewError):workflow.get_plan(restarted,fresh,plan['id'])


if __name__=='__main__':
    unittest.main()
