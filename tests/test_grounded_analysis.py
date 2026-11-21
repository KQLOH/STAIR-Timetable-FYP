"""Invented operational fixtures and mocked HTTP; no provider/research execution."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.analysis.grounded import GroundedAnalyser, PROMPT_DIGEST, provider_input, validate_output
from backend.analysis.provider import CompatibleProvider, ProviderSettings
from backend.analysis.api import router
from backend.analysis.models import Availability
from backend.analysis.service import analyze
from backend.reconstruction.models import digest
from test_m7_analysis import make, attest


def request():
    q=make().model_copy(update={'availability':(Availability(start='2026-01-05T08:00:00+00:00',end='2026-01-05T13:00:00+00:00'),)})
    return attest(q)


def answer(payload):
    return {'claims':[{'fact_id':f['fact_id'],'value':f['value']} for f in payload['facts'][:2]],
            'recommendations':[{'window_id':w['window_id'],'activity':'STUDY'} for w in payload['allowed_windows'][:1]],
            'no_window':not payload['allowed_windows']}


class FakeProvider:
    def __init__(self, change=None):self.calls=0;self.change=change;self.payload=None
    def ready(self):return True
    async def complete(self,payload):
        self.calls+=1;self.payload=deepcopy(payload)
        if self.change:return self.change(payload)
        return answer(payload)


class GroundedTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_structured_output_and_no_identifiers_sent(self):
        q,a,_=request();p=FakeProvider();r=await GroundedAnalyser(p).run(q,a,allow_provider=True)
        self.assertEqual(r['grounded']['status'],'VALIDATED');self.assertEqual(p.calls,1)
        raw=json.dumps(p.payload)
        for value in ('SYN101',q.packet.source.source_document_id,q.scope.timetable_id,'evidence_id','event_refs','raw_text','attestation'):
            self.assertNotIn(value,raw)
        self.assertTrue(r['recommendations']);self.assertFalse(r['registered_c1_ready'])

    async def test_unverified_or_missing_consent_never_calls_provider(self):
        q,a,_=request();p=FakeProvider();service=GroundedAnalyser(p)
        r=await service.run(q,None,allow_provider=True)
        self.assertEqual(r['grounded']['status'],'NOT_EXECUTED');self.assertEqual(r['recommendations'],[])
        r=await service.run(q,a)
        self.assertEqual(r['grounded']['reason'],'EXPLICIT_PROVIDER_CONSENT_REQUIRED');self.assertEqual(p.calls,0)

    async def test_missing_provider_and_availability_remain_not_executed(self):
        q,a,_=request()
        r=await GroundedAnalyser().run(q,a,allow_provider=True)
        self.assertEqual(r['grounded']['reason'],'APPROVED_PROVIDER_CONFIGURATION_REQUIRED')
        r=await GroundedAnalyser(FakeProvider()).run(q.model_copy(update={'availability':()}),a,allow_provider=True)
        self.assertEqual(r['grounded']['reason'],'EXPLICIT_VALID_AVAILABILITY_REQUIRED')

    async def test_malformed_hallucinated_and_extra_prose_fall_back(self):
        q,a,_=request()
        for bad in ({'prose':'invented private data'}, {'claims':[{'fact_id':'F999','value':100}], 'recommendations':[],'no_window':False},
                    {**answer(provider_input(q,analyze(q,a))), 'extra':'unverified words'}):
            p=FakeProvider(lambda payload:bad);r=await GroundedAnalyser(p).run(q,a,allow_provider=True)
            self.assertEqual(r['grounded']['status'],'FALLBACK');self.assertEqual(r['recommendations'],[]);self.assertEqual(p.calls,1)
            self.assertNotIn('invented private data',json.dumps(r))

    async def test_timeout_single_attempt_and_no_exception_leak(self):
        q,a,_=request()
        def fail(payload):raise TimeoutError('private-token-do-not-log')
        p=FakeProvider(fail);r=await GroundedAnalyser(p).run(q,a,allow_provider=True)
        self.assertEqual(p.calls,1);self.assertEqual(r['grounded']['status'],'FALLBACK')
        self.assertNotIn('private-token',json.dumps(r));self.assertEqual(r['recommendations'],[])

    async def test_revocation_during_provider_call_removes_verified_windows(self):
        q,a,_=request()
        def revoke(payload):a.revoke(q.attestation_id);return answer(payload)
        r=await GroundedAnalyser(FakeProvider(revoke)).run(q,a,allow_provider=True)
        self.assertFalse(r['completeness']['trusted_complete']);self.assertEqual(r['recommendations'],[])
        self.assertTrue(all(f['value'] is None for f in r['facts'] if f['type']=='verified_available_windows'))

    async def test_no_window_is_valid_not_a_fake_recommendation(self):
        q=make().model_copy(update={'availability':(Availability(start='2026-01-05T09:00:00Z',end='2026-01-05T10:00:00Z'),)})
        q,a,_=attest(q);r=await GroundedAnalyser(FakeProvider()).run(q,a,allow_provider=True)
        self.assertEqual(r['grounded']['status'],'VALIDATED');self.assertTrue(r['grounded']['output']['no_window'])
        self.assertEqual(r['recommendations'],[])

    async def test_registered_research_path_is_not_silently_enabled(self):
        q,a,_=request()
        with self.assertRaisesRegex(ValueError,'REGISTERED'):
            await GroundedAnalyser(FakeProvider()).run(q.model_copy(update={'profile':'registered'}),a,allow_provider=True)

    async def test_stale_scope_or_source_cannot_use_completeness(self):
        q,a,_=request();p=FakeProvider()
        changed=q.model_copy(update={'scope':q.scope.model_copy(update={'end':q.scope.end+timedelta(days=1)})})
        r=await GroundedAnalyser(p).run(changed,a,allow_provider=True)
        self.assertEqual(p.calls,0);self.assertEqual(r['recommendations'],[])


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        q,a,_=request();self.payload=provider_input(q,analyze(q,a))

    def test_claim_value_must_match_exactly_and_not_bool_for_number(self):
        r=answer(self.payload);r['claims'][0]['value']=True
        with self.assertRaises(ValueError):validate_output(r,self.payload)

    def test_unknown_duplicate_or_overlapping_windows_are_rejected(self):
        r=answer(self.payload);r['recommendations'][0]['window_id']='W999'
        with self.assertRaises(ValueError):validate_output(r,self.payload)
        r=answer(self.payload);r['recommendations']*=2
        with self.assertRaises(ValueError):validate_output(r,self.payload)
        p=deepcopy(self.payload);p['allowed_windows'].append({**p['allowed_windows'][0],'window_id':'overlap'})
        r=answer(p);r['recommendations'].append({'window_id':'overlap','activity':'STUDY'})
        with self.assertRaises(ValueError):validate_output(r,p)

    def test_false_no_window_and_feasible_omission_rejected(self):
        r=answer(self.payload);r['no_window']=True
        with self.assertRaises(ValueError):validate_output(r,self.payload)
        r=answer(self.payload);r['recommendations']=[]
        with self.assertRaises(ValueError):validate_output(r,self.payload)


def settings(root):
    # Explicit SYNTHETIC policy records, never actual consent or provider approval.
    record=root/'synthetic-policy.txt';record.write_text('SYNTHETIC SOFTWARE FIXTURE ONLY')
    evidence={'path':record.name,'sha256':sha256(record.read_bytes()).hexdigest()}
    return {'schema_version':'approved-operational-provider-v1','provider':'synthetic-http',
            'model_revision':'synthetic-model-v1','region':'synthetic-test-region',
            'endpoint':'https://synthetic.invalid/v1/chat/completions','prompt_digest':PROMPT_DIGEST,
            'approved_by':'SYNTHETIC TEST ONLY','approved_at':'2026-01-01T00:00:00Z','approval_record_id':'synthetic-approval',
            'privacy_record':evidence,'api_policy_record':evidence,'deletion_record':evidence,
            'training_excluded':True,'retention_days':0,'account_setting_record':'synthetic-account',
            'access_controls':'synthetic-only','subprocessors':['synthetic-only']}


class ProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.path=self.root/'provider.json';self.data=settings(self.root)

    def provider(self,handler):
        self.path.write_text(json.dumps(self.data));h=sha256(self.path.read_bytes()).hexdigest()
        return CompatibleProvider(self.path,h,transport=httpx.MockTransport(handler))

    async def test_http_request_fixes_parameters_no_storage_and_no_retries(self):
        seen=[]
        def handler(r):
            seen.append(json.loads(r.content));return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'claims':[],'recommendations':[],'no_window':True})}}]})
        p=self.provider(handler)
        with patch.dict('os.environ',{'STAIR_ANALYSIS_API_KEY':'invented-test-key'}):
            self.assertTrue(p.ready());result=await p.complete({'facts':[]})
        self.assertTrue(result['no_window']);self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]['temperature'],0);self.assertEqual(seen[0]['max_completion_tokens'],700)
        self.assertFalse(seen[0]['store']);self.assertEqual(seen[0]['reasoning_effort'],'none')

    async def test_changed_policy_file_or_missing_key_disables_provider(self):
        p=self.provider(lambda r:httpx.Response(500))
        with patch.dict('os.environ',{},clear=True):self.assertFalse(p.ready())
        with patch.dict('os.environ',{'STAIR_ANALYSIS_API_KEY':'invented'}):
            (self.root/'synthetic-policy.txt').write_text('changed');self.assertFalse(p.ready())

    async def test_redirect_oversize_or_truncation_is_not_followed(self):
        for response in (httpx.Response(302,headers={'location':'https://elsewhere.invalid'}),
                         httpx.Response(200,content=b'x'*65537),
                         httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})):
            calls=[]
            p=self.provider(lambda r:(calls.append(r),response)[1])
            with patch.dict('os.environ',{'STAIR_ANALYSIS_API_KEY':'invented'}):
                with self.assertRaises(Exception):await p.complete({})
            self.assertEqual(len(calls),1)

    async def test_unapproved_file_or_unknown_privacy_fields_rejected(self):
        self.path.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):CompatibleProvider(self.path,'0'*64)
        for field in ('region','model_revision','privacy_record','training_excluded','deletion_record'):
            data={k:v for k,v in self.data.items() if k!=field}
            with self.assertRaises(ValueError):ProviderSettings.model_validate(data)
        with self.assertRaises(ValueError):ProviderSettings.model_validate({**self.data,'endpoint':'http://unsafe.invalid'})


class GroundedAPITests(unittest.TestCase):
    def test_public_route_cannot_self_verify_or_enable_registered_experiment(self):
        app=FastAPI();app.include_router(router);client=TestClient(app)
        q,_,_=request();p=FakeProvider();app.state.m7_grounded_service=GroundedAnalyser(p)
        r=client.post('/api/analysis/v1/grounded',json={'analysis':q.model_dump(mode='json'),'allow_provider':True})
        self.assertEqual(r.status_code,200);self.assertEqual(p.calls,0)
        self.assertEqual(r.json()['grounded']['reason'],'INDEPENDENT_COMPLETE_SCHEDULE_REQUIRED')
        body=q.model_dump(mode='json');body['profile']='registered'
        self.assertEqual(client.post('/api/analysis/v1/grounded',json={'analysis':body}).status_code,409)

    def protected(self):
        import test_student_schedule_analysis as existing
        from backend.review.api import router as review_router, ReviewBridge
        helper=existing.ScheduleCheckTests('test_primary_self_confirmation_not_complete')
        helper.setUp();self.addCleanup(helper.doCleanups)
        app=FastAPI();app.include_router(review_router)
        app.state.m5_review_bridge=ReviewBridge(helper.store);app.state.m7_schedule_checks=helper.service
        provider=FakeProvider();app.state.m7_grounded_service=GroundedAnalyser(provider)
        client=TestClient(app,client=('127.0.0.1',40000));self.addCleanup(client.close)
        return helper,client,provider

    def test_protected_check_route_uses_real_gate_and_explicit_consent(self):
        h,c,p=self.protected();url='/api/review/schedule-checks/'+h.check['check_id']+'/grounded'
        body={'availability':[{'start':'2026-01-05T08:00','end':'2026-01-05T13:00'}],'allow_provider':True}
        self.assertEqual(c.post(url,json=body).status_code,403)
        headers={'Authorization':'Bearer '+h.token}
        result=c.post(url,json=body,headers=headers);self.assertEqual(result.status_code,200)
        self.assertEqual(p.calls,0);h.verified()
        result=c.post(url,json=body,headers=headers)
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(p.calls,1)
        self.assertEqual(result.json()['grounded']['status'],'VALIDATED')

    def test_changed_source_during_protected_call_is_not_delivered(self):
        h,c,p=self.protected();h.verified()
        def mutate(payload):
            h.store.packet=h.store.packet.model_copy(update={'source':h.store.packet.source.model_copy(update={'content_sha256':'0'*64})})
            return answer(payload)
        p.change=mutate
        result=c.post('/api/review/schedule-checks/'+h.check['check_id']+'/grounded',
            json={'availability':[{'start':'2026-01-05T08:00','end':'2026-01-05T13:00'}],'allow_provider':True},
            headers={'Authorization':'Bearer '+h.token})
        self.assertNotEqual(result.status_code,200)
