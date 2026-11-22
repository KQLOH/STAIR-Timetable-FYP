"""Exact run resolution uses bound evidence, not filenames, counts or similarity."""
import unittest
from fastapi.testclient import TestClient
import test_student_accounts as base
from test_m5_policy import fixture

class SavedRunTests(unittest.TestCase):
    setUp=base.StudentAccountTests.setUp
    login=base.StudentAccountTests.login
    enroll=base.StudentAccountTests.enroll

    def connect(self,request,client=None,headers=None):
        return (client or self.client).post('/api/review/connect-run',
            content=request.model_dump_json(),headers={**(headers or self.headers),'Content-Type':'application/json'})

    def test_exact_repeat_restores_legacy_ingest_without_new_journal_or_task(self):
        self.login();task,request=self.enroll()
        store=self.accounts.store(next(iter(self.accounts.sessions.values())))
        before=store.journal.load()[0]
        result=self.connect(request);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['task_ids'],[task['task_id']])
        self.assertEqual(self.connect(request).json(),result.json())
        self.assertEqual(before,store.journal.load()[0])

    def test_same_source_different_bound_policy_is_separate_and_restart_safe(self):
        self.login();task,request=self.enroll()
        other=request.model_copy(update={'policy_profile':'m5-source-review-v1'})
        first=self.connect(request);second=self.connect(other)
        self.assertEqual(second.status_code,200,second.text)
        self.assertNotEqual(first.json()['run_id'],second.json()['run_id'])
        self.assertTrue(set(first.json()['task_ids']).isdisjoint(second.json()['task_ids']))
        self.client.post('/api/student/logout',headers=self.headers)
        self.accounts.stores.clear()
        self.login(create=False)
        self.client.post('/api/student/review/connect',json={},headers=self.headers)
        self.assertEqual(self.connect(other).json(),second.json())
        original=self.client.get('/api/review/tasks/'+task['task_id']).json()['task']
        self.assertEqual(original['current_event'],task['current_event'])

    def test_other_source_is_not_resolved_by_similar_events(self):
        self.login();_,request=self.enroll()
        unrelated,_=fixture('SYN202 Tuesday 2026-11-24 10:00-11:00 Lecture')
        self.assertEqual(self.connect(unrelated).status_code,403)
        self.assertEqual(len(self.client.get('/api/student/workspace').json()['tasks']),1)

    def test_other_account_cannot_resolve_or_read_private_run(self):
        self.login();task,request=self.enroll()
        client=TestClient(self.app,base_url=base.ORIGIN,client=('127.0.0.1',44001))
        headers=self.login('student_b',client=client)
        self.assertEqual(self.connect(request,client,headers).status_code,409)
        self.assertNotEqual(client.get('/api/review/tasks/'+task['task_id']).status_code,200)
        self.assertEqual(client.get('/api/student/workspace').json()['tasks'],[])
        source=request.source_packet.source
        response=client.post('/api/student/source',content=base.SINGLE.encode(),headers={**headers,
            'X-STAIR-Source-ID':source.source_document_id,'X-STAIR-Source-SHA256':source.content_sha256})
        self.assertEqual(response.status_code,200,response.text)
        own=self.connect(request,client,headers)
        self.assertEqual(own.status_code,200,own.text)
        self.assertNotIn(task['task_id'],own.json()['task_ids'])
        self.assertNotEqual(client.get('/api/review/tasks/'+task['task_id']).status_code,200)
        self.assertEqual(len(client.get('/api/student/workspace').json()['tasks']),1)
