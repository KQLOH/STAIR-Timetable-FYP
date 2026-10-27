"""Invented S3c data and isolated authority. No live host enrollment."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.calendar.api import router
from backend.calendar.access import CalendarAccess
from backend.calendar.sandbox import LocalSandbox
from backend.calendar.testing import SyntheticFixtureAuthority
from backend.calendar.workflow import TestOnlyCalendarBridge
from backend.review.auth import TrustedHostAuthority
from backend.review.api import router as review_router


class Scenario:
    def __init__(self,directory,*,simulate=True):
        self.directory=Path(directory);self.target='test:s3c-isolated-local'
        initial=TrustedHostAuthority();self.store=LocalSandbox(self.directory,self.target,CalendarAccess(initial),test_only=True)
        self.factory=SyntheticFixtureAuthority(self.store)
        self.base=self.factory.make(events=[
            {'course_code':'SYN101','session_type':'Lecture','day_of_week':1,'date':'2033-02-07','start_time':'09:00','end_time':'10:00','source_timezone':'Etc/UTC','location':'SYNTHETIC ROOM A','session_id':'synthetic-session:1'},
            {'course_code':'SYN202','session_type':'Tutorial','day_of_week':2,'date':'2033-02-08','start_time':'12:00','end_time':'13:00','source_timezone':'Etc/UTC','location':'SYNTHETIC ROOM B','session_id':'synthetic-session:2'}])
        self.update=self.factory.make(previous=self.base,changes={'location':'SYNTHETIC ROOM C'})
        self.blocked=self.factory.make(previous=self.base,changes={'source_timezone':None})
        source=self.base.packet.source;self.bindings={(source.source_document_id,source.content_sha256)}
        self.auth=TrustedHostAuthority(test_only_source_bindings=self.bindings)
        self.actor,self.token=self.auth.issue('SYNTHETIC-S3C-DEMO-OWNER',{'read'},mode='TEST_ONLY',source_bindings=self.bindings,lifetime_minutes=60)
        self.access=CalendarAccess(self.auth);self.access.enrol(self.actor,self.target,{'read','preview','approve','execute','reconcile'})
        self.store.access=self.access
        self.catalogue={'base':('Invented timetable v1',self.base),'update':('Invented timetable v2: room correction',self.update),'blocked':('Invented incomplete timezone example',self.blocked)}
        self.bridge=TestOnlyCalendarBridge(self.store,self.catalogue,allow_failure_simulation=simulate)
        self.app=FastAPI();self.app.include_router(router);self.app.include_router(review_router)
        self.app.state.s3c_calendar_bridge=self.bridge
        self.client=TestClient(self.app,client=('127.0.0.1',45000))
        self.headers={'Authorization':'Bearer '+self.token}

    def call(self,method,path,body=None,headers=None):
        return self.client.request(method,'/api/calendar/v1/test-only'+path,json=body,headers=self.headers if headers is None else headers)

    def preview(self,fixture='base'):
        r=self.call('POST','/preview',{'fixture_id':fixture});assert r.status_code==200,r.text;return r.json()

    @staticmethod
    def approval_body(plan):
        return {key:plan[key] for key in ('preview_digest','snapshot_digest','input_digest','target_sandbox_id')} | {
            'operation_keys':[o['operation_key'] for o in plan['operations'] if o['action'] in ('CREATE','UPDATE','NO_OP')],'owner_confirmation':True}

    def approve(self,plan):
        r=self.call('POST','/approve',self.approval_body(plan));assert r.status_code==200,r.text;return r.json()

    def execute(self,approval,scenario='NONE'):
        return self.call('POST','/execute',{'approval_id':approval['approval_id'],'failure_scenario':scenario})


def workflow_records(directory):
    s=Scenario(directory)
    try:
        session=s.call('GET','/session').json();create=s.preview();approval=s.approve(create)
        history_approved=s.call('GET','/history').json();state_before=s.call('GET','/state').json()
        executed=s.execute(approval);assert executed.status_code==200,executed.text
        replay=s.execute(approval);assert replay.status_code==200,replay.text
        history_create=s.call('GET','/history').json();state_create=s.call('GET','/state').json()
        noop=s.preview();noop_approval=s.approve(noop);nooped=s.execute(noop_approval).json()
        update=s.preview('update');update_approval=s.approve(update);updated=s.execute(update_approval).json()
        history=s.call('GET','/history').json();state=s.call('GET','/state').json()
        return {'kind':'ISOLATED_SYNTHETIC_TESTCLIENT_RECORDS_NOT_LIVE_ENROLLMENT','session':session,'create':create,'approval':approval,
            'executed':executed.json(),'replay':replay.json(),'history_create':history_create,'state_create':state_create,'history_approved':history_approved,'state_before':state_before,
            'noop':noop,'noop_approval':noop_approval,'nooped':nooped,'update':update,'update_approval':update_approval,
            'updated':updated,'history':history,'state':state}
    finally:s.client.close()
