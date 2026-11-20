"""Invented software fixtures; never calibration evidence or human approvals."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import json
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.reconstruction.models import Finding,digest
from backend.review.models import PolicyAssessment,RegisteredAssessmentRef
from backend.review.operating_point import ValidationInput
from backend.review.selection import (TAU_GRID,REQUIRED_ARTIFACTS,FrozenConfiguration,
    DevelopmentInput,select_development,verify_development,select_validation,verify_validation)
from backend.review.registration import OperatingRegistry,RuntimeFeatures,FrozenM4Features
from backend.review.policy import assess_event,assess_run
from backend.calendar.eligibility import assess
from backend.calendar.models import CalendarInput,CalendarError,Snapshot
from backend.calendar.planning import build_preview
from backend.models.event import FieldState
from test_m5_policy import fixture
import test_m6_calendar as calendar_tests


def configuration(**changes):
    data=dict(profile='synthetic-registered-v1',provider_id='synthetic-frozen-features-v1',settings_digest='1'*64,
        artifacts={k:digest(k) for k in REQUIRED_ARTIFACTS},parameters={'wX':1,'wR':0,'wC':0,'wP':0},
        parameter_order=('wX','wR','wC','wP'),weights=(10000,0,0),penalty_weight=0,
        required_accept_fields=('course_code','start_time','end_time','source_timezone','recurrence','location',
            'session_type','lecturer','section','teaching_weeks','excluded_weeks'))
    data.update(changes)
    return FrozenConfiguration.model_validate(data)


def development(config=None,scores=(.9,.8,.8,.7,.1),wrong=(),hard=(),harmful=()):
    return DevelopmentInput(origin='SYNTHETIC',partition='development',pre01_artifact_digest='3'*64,candidates=(dict(
        configuration=config or configuration(),reference_count=len(scores),rows=tuple(dict(ledger_id=str(i),
            source_page_id='synthetic-development-page',source_sha256='4'*64,revision_digest='5'*64,
            reference_digest='6'*64,score=s,gate_passed=True,exact_event_correct=i not in wrong,
            hard_violation=i in hard,matched_reference_id=str(i) if i not in wrong else None,
            review_eligible=True,harmful_final_state=i in harmful) for i,s in enumerate(scores))),))


def chain(config=None):
    dev=development(config);pre02=select_development(dev);spec=verify_development(pre02,dev)
    val=ValidationInput(origin='SYNTHETIC',partition='validation',pipeline_digest=spec.pipeline_digest(),
        scoring_configuration_digest=spec.checksum(),pre01_artifact_digest=dev.pre01_artifact_digest,
        pre02_artifact_digest=pre02['integrity_digest'],tau_grid=TAU_GRID,rows=tuple(dict(
            ledger_id='validation-'+str(i),source_page_id='synthetic-validation-page',source_sha256='7'*64,
            revision_digest='8'*64,reference_digest='9'*64,score=s,gate_passed=True,exact_event_correct=True,
            hard_violation=False) for i,s in enumerate((.9,.8,.8,.7,.1))))
    return dev,pre02,val,select_validation(val,pre02,dev)


class SelectionTests(unittest.TestCase):
    def test_development_full_vector_and_discarded_tau(self):
        dev,pre02,val,pre03=chain()
        self.assertTrue(pre02['development_tau_discarded'])
        self.assertEqual(len(pre02['candidate_vector'][0]['points']),10)
        self.assertNotIn('selected_tau',pre02)
        self.assertEqual(verify_validation(pre03,val,pre02,dev)[1],.5)

    def test_all_development_thresholds_infeasible(self):
        for d in (development(scores=(.1,)),development(hard=(0,)),development(harmful=(4,)),development(wrong=(0,))):
            r=select_development(d)
            self.assertEqual(r['status'],'DEVELOPMENT_INFEASIBLE')
            with self.assertRaises(ValueError):verify_development(r,d)

    def test_development_risk_boundary(self):
        d=development(scores=(.9,)*20,wrong=(0,))
        self.assertEqual(select_development(d)['status'],'SELECTED_NON_THRESHOLD_CONFIGURATION')
        d=development(scores=(.9,)*19,wrong=(0,))
        self.assertEqual(select_development(d)['status'],'DEVELOPMENT_INFEASIBLE')

    def test_configuration_selection_f1_then_review_then_parameter_tuple(self):
        a=development().candidates[0]
        params={**a.configuration.parameters,'wP':1}
        b=a.model_copy(update={'configuration':configuration(parameters=params,penalty_weight=1)})
        d=development().model_copy(update={'candidates':(b,a)})
        r=select_development(d)
        self.assertEqual(r['selected_configuration_digest'],a.configuration.checksum())
        # Same exact reconstruction; increasing review demand loses before tuple.
        b=b.model_copy(update={'rows':tuple(x.model_copy(update={'score':.45}) if i==3 else x for i,x in enumerate(b.rows))})
        r=select_development(d.model_copy(update={'candidates':(b,a)}))
        self.assertEqual(r['selected_configuration_digest'],a.configuration.checksum())

    def test_higher_exact_f1_wins_before_review_demand(self):
        a=development().candidates[0]
        b=development(wrong=(4,)).candidates[0]
        b=b.model_copy(update={'configuration':configuration(parameters={'wX':1,'wR':0,'wC':0,'wP':1},penalty_weight=1)})
        d=development().model_copy(update={'candidates':(b,a)})
        self.assertEqual(select_development(d)['selected_configuration_digest'],a.configuration.checksum())

    def test_grid_weights_applicability_and_finite_parameters_frozen(self):
        cases=[dict(tau_grid=(.5,.9)),dict(weights=(9900,100,0)),dict(required_accept_fields=()),
            dict(parameters={'wX':float('nan'),'wR':0,'wC':0,'wP':0}),dict(parameters={'wX':0,'wR':0,'wC':0,'wP':0})]
        for change in cases:
            with self.assertRaises(ValidationError):configuration(**change)

    def test_validation_cannot_change_grid_configuration_or_preparation_chain(self):
        dev,p2,val,p3=chain()
        for update in ({'tau_grid':(.5,)},{'pipeline_digest':'0'*64},{'scoring_configuration_digest':'0'*64},
                       {'pre02_artifact_digest':'0'*64},{'origin':'INDEPENDENT_VALIDATION_CLAIM'}):
            with self.assertRaises(ValueError):select_validation(val.model_copy(update=update),p2,dev)

    def test_tampered_vector_even_rehashed_rejected(self):
        dev,p2,val,p3=chain();bad=deepcopy(p3);bad['selected_tau']=.9
        bad['integrity_digest']=digest({k:v for k,v in bad.items() if k!='integrity_digest'})
        with self.assertRaises(ValueError):verify_validation(bad,val,p2,dev)
        bad=deepcopy(p2);bad['candidate_vector'][0]['tp']=999
        bad['integrity_digest']=digest({k:v for k,v in bad.items() if k!='integrity_digest'})
        with self.assertRaises(ValueError):verify_development(bad,dev)

    def test_freeze_chronology_rejected(self):
        dev,p2,val,p3=chain();p3['recorded_at']='2000-01-01T00:00:00+00:00'
        p3['integrity_digest']=digest({k:v for k,v in p3.items() if k!='integrity_digest'})
        with self.assertRaises(ValueError):verify_validation(p3,val,p2,dev)

    def test_validation_all_fail_and_no_population_rejected(self):
        dev,p2,val,p3=chain()
        for rows in ((),tuple(r.model_copy(update={'score':None}) for r in val.rows)):
            changed=val.model_copy(update={'rows':rows});record=select_validation(changed,p2,dev)
            self.assertEqual(record['selection_status'],'VALIDATION_INFEASIBLE')
            with self.assertRaises(ValueError):verify_validation(record,changed,p2,dev)

    def test_common_development_frame_and_one_to_one_references(self):
        d=development().model_dump(mode='json');c=deepcopy(d['candidates'][0]);c['rows'][1]['matched_reference_id']='0'
        with self.assertRaises(ValidationError):DevelopmentInput.model_validate({**d,'candidates':[c]})
        c=deepcopy(d['candidates'][0]);c['configuration']['parameters']['wP']=1;c['configuration']['penalty_weight']=1
        c['rows'][0]['source_page_id']='another-page'
        with self.assertRaises(ValidationError):DevelopmentInput.model_validate({**d,'candidates':[*d['candidates'],c]})


class SyntheticFeatures:
    provider_id='synthetic-frozen-features-v1'
    def __init__(self):self.rows={}
    def __call__(self,run,event,evidence,findings):return self.rows.get(str(event.revision.revision_id))
    def put(self,event,score):
        self.rows[str(event.revision.revision_id)]=RuntimeFeatures(revision_digest=digest(event.revision.model_dump(mode='json')),x=score,penalty=0)


def explicit_synthetic_absence(bundle):
    """Invented applicability assertions, not source readings or human records."""
    from backend.research.workflow import REGISTERED_CONDITIONAL_FIELDS
    events=[]
    for event in bundle.packet.events:
        observations=tuple(o.model_copy(update={'state':FieldState.SOURCE_ABSENT,
            'evidence_ids':(bundle.packet.evidence[0].evidence_id,),
            'justification':'SYNTHETIC fixture explicitly declares this conditional field absent'})
            if o.field_name in REGISTERED_CONDITIONAL_FIELDS and o.state.value=='MISSING' else o for o in event.revision.observations)
        events.append(event.model_copy(update={'revision':event.revision.model_copy(update={'observations':observations})}))
    return bundle.model_copy(update={'packet':bundle.packet.model_copy(update={'events':tuple(events)})})


class RegisteredRuntimeTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing invented calendar fixture boundary, not real Gold.
        self.calendar=calendar_tests.CalendarTests('test_deterministic_mapping');self.calendar.setUp()
        self.addCleanup(self.calendar.doCleanups)
        self.bundle=explicit_synthetic_absence(self.calendar.fixtures.make(changes={'recurrence':{'frequency':'ONCE','source_rule':'synthetic one-off'}}))
        self.event=self.bundle.packet.events[0]
        _,native=fixture();self.native=native;graph=native.graph().model_copy(update={'source':self.bundle.packet.source,'evidence':self.bundle.packet.evidence})
        self.run=SimpleNamespace(profile='synthetic-registered-v1',decoder_mode='constrained',original_json='{}',
            source_json=self.bundle.packet.model_dump_json(),graph=lambda:graph,input_digest='1'*64,
            reconstruction_digest='2'*64,search_status='COMPLETE_FINITE_SEARCH',base_score_digest=None,
            candidate_groups=((self.event.candidate_id,graph.groups[0].group_id),),component_statuses=(),unresolved_ids=())
        self.findings=tuple(Finding(rule_id=r,passed=True,severity='HARD',group_ids=(),reason='SYNTHETIC SOFTWARE FIXTURE') for r in ('S01','S02','T01','R01'))
        self.provider=SyntheticFeatures();self.provider.put(self.event,.9)
        self.registry=OperatingRegistry(feature_provider=self.provider)
        self.artifacts=chain(configuration(settings_digest=graph.settings_digest))
        self.scope=((self.bundle.packet.source.source_document_id,self.bundle.packet.source.content_sha256),)
        self.registry.register_test_only(*self.artifacts,source_bindings=self.scope)

    def decision(self,event=None,**kwargs):
        return assess_event(self.run,event or self.event,self.findings,registry=self.registry,**kwargs)
    def ref(self,decision):
        return RegisteredAssessmentRef.model_validate({k:decision.model_dump(mode='json')[k] for k in RegisteredAssessmentRef.model_fields})

    def test_accept_review_abstain_are_distinct_test_only_rankings(self):
        for score,expected in ((.9,'ACCEPT'),(.45,'REVIEW'),(.2,'ABSTAIN')):
            self.provider.put(self.event,score);d=self.decision()
            self.assertEqual(d.decision.value,expected);self.assertEqual(d.registration_mode,'TEST_ONLY')
            self.assertEqual(d.registered_routing_eligible,expected=='ACCEPT');self.assertIsNone(d.calibrated_probability)
            self.assertFalse(d.schedule_verified)

    def test_receipt_round_trip_and_signature_tampering(self):
        d=self.decision();self.assertEqual(PolicyAssessment.model_validate_json(d.model_dump_json()),d)
        self.assertTrue(self.registry.verifies(d,self.bundle.packet,self.event))
        self.assertEqual(self.registry.resolve(self.ref(d)),d)
        for change in ({'ranking_score':.99},{'reason_codes':('altered',)},{'registration_signature':'0'*64}):
            self.assertFalse(self.registry.verifies(d.model_copy(update=change),self.bundle.packet,self.event))
        with self.assertRaises(ValueError):self.registry.resolve(self.ref(d).model_copy(update={'revision_digest':'0'*64}))

    def test_no_registration_and_revocation_fail_closed(self):
        self.registry.revoke();d=self.decision()
        self.assertFalse(d.registered_routing_eligible);self.assertIsNone(d.registration_mode)

    def test_same_revision_revalidation_supersedes_previous_accept(self):
        accepted=self.decision();self.assertTrue(self.registry.verifies(accepted,self.bundle.packet,self.event))
        self.decision(revalidation_required=True)
        self.assertFalse(self.registry.verifies(accepted,self.bundle.packet,self.event))
        with self.assertRaises(ValueError):self.registry.resolve(self.ref(accepted))

    def test_changed_frozen_runtime_file_revokes_active_proofs(self):
        import hashlib
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic-runtime.txt';path.write_text('SYNTHETIC FROZEN RUNTIME',encoding='utf-8')
            self.registry._runtime_files[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
            first=self.decision();self.assertTrue(first.registered_routing_eligible)
            path.write_text('SYNTHETIC MODIFIED RUNTIME',encoding='utf-8')
            self.assertFalse(self.registry.verifies(first,self.bundle.packet,self.event))
            self.assertFalse(self.registry.active)
            self.assertFalse(self.decision().registered_routing_eligible)

    def test_wrong_capture_or_genuine_source_cannot_reuse_test_registration(self):
        for name in ('synthetic:wrong','real-student-upload'):
            packet=self.bundle.packet.model_copy(update={'source':self.bundle.packet.source.model_copy(update={'source_document_id':name}),'events':(),'evidence':()})
            self.run.source_json=packet.model_dump_json();d=self.decision()
            self.assertFalse(d.registered_routing_eligible);self.assertIsNone(d.registration_mode)

    def test_configuration_and_hard_constraint_fail_closed(self):
        old=self.run.graph;self.run.graph=lambda:old().model_copy(update={'settings_digest':'0'*64})
        self.assertEqual(self.decision().decision.value,'ABSTAIN')
        self.run.graph=old
        d=assess_event(self.run,self.event,self.findings+(Finding(rule_id='T99',passed=False,severity='HARD',group_ids=(),reason='SYNTHETIC conflict'),),registry=self.registry)
        self.assertEqual(d.decision.value,'ABSTAIN')

    def test_incomplete_search_and_unconstrained_decoder_abstain(self):
        self.run.search_status='SEARCH_INCOMPLETE';self.assertEqual(self.decision().decision.value,'ABSTAIN')
        self.run.search_status='COMPLETE_FINITE_SEARCH';self.run.decoder_mode='unconstrained'
        self.assertEqual(self.decision().decision.value,'ABSTAIN')

    def test_missing_mandatory_recurrence_does_not_accept(self):
        event=self.calendar.bundle.packet.events[0];self.run.candidate_groups=((event.candidate_id,self.native.graph().groups[0].group_id),)
        self.provider.put(event,.9)
        self.assertNotEqual(self.decision(event).decision.value,'ACCEPT')

    def test_missing_applicable_fields_count_toward_bounded_review(self):
        fields={'location','lecturer','section'}
        event=self.event.model_copy(update={'revision':self.event.revision.model_copy(update={
            'content':self.event.revision.content.model_copy(update={f:None for f in fields}),
            'observations':tuple(o.model_copy(update={'state':FieldState.MISSING,'evidence_ids':(),'justification':None,'raw_value':None}) if o.field_name in fields else o for o in self.event.revision.observations)})})
        self.provider.put(event,.9)
        self.assertEqual(self.decision(event).decision.value,'ABSTAIN')

    def test_source_structure_uncertainty_prevents_accept(self):
        graph=self.run.graph();self.run.graph=lambda:graph.model_copy(update={'diagnostics':('UNSUPPORTED_SOURCE_STRUCTURE',)})
        self.assertNotEqual(self.decision().decision.value,'ACCEPT')

    def test_wrong_revision_features_cannot_accept(self):
        self.provider.rows[str(self.event.revision.revision_id)]=RuntimeFeatures(revision_digest='0'*64,x=1,penalty=0)
        self.assertEqual(self.decision().decision.value,'ABSTAIN')

    def test_pending_correction_abstains_and_old_receipt_is_stale(self):
        first=self.decision();new=explicit_synthetic_absence(self.calendar.fixtures.make(previous=self.bundle,changes={'location':'SYNTHETIC SECOND ROOM'}))
        changed=new.packet.events[0]
        self.assertFalse(self.registry.verifies(first,new.packet,changed))
        d=self.decision(changed,revalidation_required=True)
        self.assertEqual(d.decision.value,'ABSTAIN')
        self.assertFalse(self.registry.verifies(first,self.bundle.packet,self.event))
        with self.assertRaises(ValueError):self.registry.resolve(self.ref(first))
        # Original ranking cannot be reused after a correction/revalidation.
        self.assertEqual(self.decision(changed).decision.value,'ABSTAIN')
        self.assertFalse(self.decision().registered_routing_eligible)

    def test_revalidated_current_features_are_fresh_and_not_human_verification(self):
        self.decision();new=explicit_synthetic_absence(self.calendar.fixtures.make(previous=self.bundle,changes={'location':'SYNTHETIC SECOND ROOM'}))
        event=new.packet.events[0];self.provider.put(event,.45)
        d=self.decision(event,source_evidence=new.packet.evidence)
        self.assertEqual(d.decision.value,'REVIEW');self.assertFalse(d.schedule_verified)
        self.assertEqual(event.human_verification.status,'NOT_VERIFIED')

    def test_calendar_requires_proof_approval_and_separate_fixture_authority(self):
        d=self.decision();bundle=self.bundle.model_copy(update={'assessments':(d,)})
        result=assess(bundle,self.event,registry=self.registry)
        self.assertNotIn('M5_ACCEPT_DISABLED',result.reason_codes)
        self.assertIn('OPERATIONAL_WRITE_BLOCKED',result.reason_codes)
        self.assertFalse(result.sandbox_write_permitted)
        plan=self.calendar.store.preview(bundle,self.calendar.actor)
        self.assertEqual(plan.operations[0].action,'BLOCKED')
        self.assertIn('M5_ACCEPT_DISABLED',assess(bundle,self.event).reason_codes)

    def test_saved_accept_is_displayed_as_stale_after_restart_without_rewriting_history(self):
        import test_m5_review as review_tests
        helper=review_tests.ReviewTests('test_queue_creation_and_summary');helper.setUp()
        try:
            view=helper.store.get(helper.id,helper.admin)
            run=helper.store._runs[view.run_id]
            run.snapshot=self.run;run.evidence=self.bundle.packet.evidence
            run.events={self.event.candidate_id:self.event};run.context=self.native.graph().context
            record=self.decision()
            stored=view.model_copy(update={'current_event':self.event,'decision':record})
            helper.store._tasks[helper.id].view=stored;helper.store.registry=self.registry
            self.assertEqual(helper.store.get(helper.id,helper.admin).decision.decision.value,'ACCEPT')
            # Restarted process has no valid issued receipts until re-assessment.
            helper.store.registry=None
            returned=helper.store.get(helper.id,helper.admin)
            self.assertFalse(returned.decision.registered_routing_eligible)
            self.assertNotEqual(returned.decision.decision.value,'ACCEPT')
            self.assertEqual(returned.current_event.revision,self.event.revision)
            self.assertEqual(helper.store._tasks[helper.id].view,stored)
            snapshot=helper.store.snapshot(helper.id,helper.admin)
            self.assertFalse(snapshot['tasks'][0]['decision']['registered_routing_eligible'])
            self.assertFalse(helper.store._tasks[helper.id].corrections)
        finally:helper.doCleanups()

    def test_synthetic_registered_calendar_create_replay_update_and_revoke(self):
        self.calendar.store.registry=self.registry
        def permit_invented(bundle):
            # Private test authority signs only this known invented fixture.
            d=self.decision(bundle.packet.events[0],source_evidence=bundle.packet.evidence)
            result=bundle.model_copy(update={'assessments':(d,)})
            self.calendar.fixtures._registered.add(digest(result.model_dump(mode='json')))
            return result
        current=permit_invented(self.bundle)
        plan=self.calendar.store.preview(current,self.calendar.actor)
        self.assertEqual(plan.operations[0].action,'CREATE')
        with self.assertRaises(CalendarError):self.calendar.store.execute(uuid4(),self.calendar.actor)
        approval=self.calendar.store.approve(plan.preview_digest,self.calendar.actor)
        self.calendar.store.execute(approval.approval_id,self.calendar.actor)
        self.assertEqual(self.calendar.store.preview(current,self.calendar.actor).operations[0].action,'NO_OP')
        new=explicit_synthetic_absence(self.calendar.fixtures.make(previous=self.bundle,changes={'location':'SYNTHETIC SECOND ROOM'}))
        self.provider.put(new.packet.events[0],.9);updated=permit_invented(new)
        plan=self.calendar.store.preview(updated,self.calendar.actor)
        self.assertEqual(plan.operations[0].action,'UPDATE')
        self.registry.revoke()
        self.assertEqual(self.calendar.store.preview(updated,self.calendar.actor).operations[0].action,'BLOCKED')

    def test_registration_revocation_after_approval_prevents_execution(self):
        self.calendar.store.registry=self.registry
        bundle=self.bundle.model_copy(update={'assessments':(self.decision(),)})
        self.calendar.fixtures._registered.add(digest(bundle.model_dump(mode='json')))
        plan=self.calendar.store.preview(bundle,self.calendar.actor)
        approval=self.calendar.store.approve(plan.preview_digest,self.calendar.actor)
        self.registry.revoke()
        with self.assertRaises(CalendarError) as caught:self.calendar.store.execute(approval.approval_id,self.calendar.actor)
        self.assertEqual(caught.exception.code,'REGISTERED_POLICY_STALE_REPREVIEW_REQUIRED')
        self.assertFalse(self.calendar.store.snapshot(self.calendar.actor).entries)

    def test_calendar_http_resolves_receipt_and_rejects_tampering(self):
        from fastapi import FastAPI
        from backend.calendar.api import router
        app=FastAPI();app.include_router(router);app.state.m5_operating_registry=self.registry
        with TestClient(app) as client:
            body=self.bundle.model_dump(mode='json');body['registered_assessment_refs']=[self.ref(self.decision()).model_dump(mode='json')]
            response=client.post('/api/calendar/v1/eligibility',json=body)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('M5_ACCEPT_DISABLED',response.json()['events'][0]['reason_codes'])
            self.assertFalse(response.json()['events'][0]['sandbox_write_permitted'])
            body['registered_assessment_refs'][0]['registration_signature']='0'*64
            self.assertEqual(client.post('/api/calendar/v1/preview',json={'bundle':body}).status_code,409)

    def test_registered_report_counts_routes_without_claiming_accuracy(self):
        from backend.review.source_routing import report
        result=report((self.decision(),))
        self.assertEqual(result['registered_accept_count'],1)
        self.assertEqual(result['automatic_coverage'],1)
        self.assertEqual(result['registered_policy_status'],'TEST_ONLY_REGISTERED')
        self.assertIsNone(result['supported_event_accuracy']);self.assertIsNone(result['unsafe_acceptance'])

    def test_test_loader_preserves_scope_and_failed_reload_disables(self):
        with TemporaryDirectory() as temp:
            args={}
            for key,item in zip(('development','pre02','validation','pre03'),self.artifacts):
                path=Path(temp)/(key+'.json');path.write_text(item.model_dump_json() if hasattr(item,'model_dump_json') else json.dumps(item),encoding='utf-8')
                args[key+'_path']=path
            registry=OperatingRegistry(feature_provider=self.provider)
            registry.load_test_only(**args,source_bindings=self.scope)
            self.assertEqual(registry.mode,'TEST_ONLY')
            args['pre03_path'].write_text('{}',encoding='utf-8')
            with self.assertRaises((ValueError,KeyError)):registry.load_test_only(**args,source_bindings=self.scope)
            self.assertFalse(registry.active)

    def test_invalid_synthetic_scope_and_origin_cannot_install(self):
        for scope in ((),(('real:source','0'*64),)):
            with self.assertRaises(ValueError):self.registry.register_test_only(*self.artifacts,source_bindings=scope)
        dev,p2,val,p3=self.artifacts
        with self.assertRaises(ValueError):self.registry.register_test_only(dev.model_copy(update={'origin':'AUTHENTIC'}),p2,val,p3,source_bindings=self.scope)

    def test_genuine_registration_requires_actual_pre01_closure(self):
        dev,p2,val,p3=self.artifacts
        with self.assertRaises(ValueError):self.registry.register(dev,p2,val,p3,bundle=SimpleNamespace(origin='SYNTHETIC'),evidence_root=Path('.'),approval_artifact_id='none',runtime_files={})
        with patch('backend.research.readiness.assess_pre01',return_value=SimpleNamespace(status='NOT READY')):
            with self.assertRaises(ValueError):self.registry.register(dev.model_copy(update={'origin':'AUTHENTIC'}),p2,val.model_copy(update={'origin':'INDEPENDENT_VALIDATION_CLAIM'}),p3,bundle=SimpleNamespace(origin='AUTHENTIC'),evidence_root=Path('.'),approval_artifact_id='none',runtime_files={})
        self.assertFalse(self.registry.active)

    def test_direct_claim_without_receipt_rejected(self):
        plain=assess_event(self.run,self.event,self.findings).model_dump(mode='json')
        with self.assertRaises(ValidationError):PolicyAssessment.model_validate({**plain,'registered_routing_eligible':True,'decision':'ACCEPT'})
        self.assertNotIn('registration_mode',plain)


class NativeProviderTests(unittest.TestCase):
    def test_frontend_receipt_forwarding_and_truthful_test_only_counts(self):
        import subprocess
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/registered_routing_checks.cjs')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_primary_annotation_cli_does_not_claim_independent_audit(self):
        from backend.research.annotation_package import apply_action,main
        from backend.research.workflow import submit
        from tests.research_fixtures import assigned,submission,stamp,CONDITIONAL_FIELDS
        workflow=submit(assigned(),submission());sub=workflow.submissions[0]
        request={'action':'verify-primary','payload':{'inventory':sub.inventory.model_dump(mode='json'),
            'bindings':[(sub.labels[0].label_id,sub.inventory.sessions[0].session_id)],
            'recorded_by':stamp('synthetic-primary',6).model_dump(mode='json'),
            'field_registry_artifact_id':'synthetic-registry','conditional_fields':list(CONDITIONAL_FIELDS)}}
        checked=apply_action(workflow,request)
        self.assertEqual(checked.state.value,'MANUALLY_VERIFIED_REFERENCE')
        self.assertNotEqual(checked.state.value,'INDEPENDENTLY_VERIFIED')
        import contextlib,io
        with TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            path=Path(directory)/'workflow.json';path.write_text(checked.model_dump_json(),encoding='utf-8')
            out=Path(directory)/'export.json'
            self.assertEqual(main(['export','--workflow',str(path),'--out',str(out)]),0)
            self.assertEqual(json.loads(out.read_text())['events'][0]['status'],'MANUALLY_VERIFIED_REFERENCE')

    def test_native_frozen_features_recomputed_and_corrected_revision_not_reused(self):
        request,run=fixture();event=run.events()[0]
        provider=FrozenM4Features(request.reconstruction_settings)
        features=provider(run,event,run.graph().evidence,())
        self.assertIsNotNone(features)
        self.assertEqual(features.revision_digest,digest(event.revision.model_dump(mode='json')))
        changed=event.model_copy(update={'revision':event.revision.model_copy(update={'version':2,'revision_id':uuid4(),'previous_revision_id':event.revision.revision_id})})
        self.assertIsNone(provider(run,changed,run.graph().evidence,()))

    def test_missing_or_changed_frozen_settings_provider_fails_closed(self):
        request,run=fixture();provider=FrozenM4Features(request.reconstruction_settings)
        graph=run.graph().model_copy(update={'settings_digest':'0'*64})
        run=replace(run,graph_json=graph.model_dump_json())
        self.assertIsNone(provider(run,run.events()[0],graph.evidence,()))

    def test_runtime_freeze_verifies_actual_members_not_just_manifest_hash(self):
        from backend.review.registration import _verify_runtime_files
        with TemporaryDirectory() as directory:
            root=Path(directory);member=root/'synthetic-code.txt';member.write_text('SYNTHETIC CODE',encoding='utf-8')
            import hashlib
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            manifest=root/'code.json';manifest.write_text(json.dumps({'schema':'freeze-file-set-v1','files':[
                {'root':'evidence','path':member.name,'sha256':sha(member)}]}),encoding='utf-8')
            config=configuration(artifacts={k:sha(manifest) for k in REQUIRED_ARTIFACTS})
            files={k:manifest for k in REQUIRED_ARTIFACTS}
            self.assertIn(str(member),_verify_runtime_files(config,files,root))
            member.write_text('SYNTHETIC CHANGE',encoding='utf-8')
            with self.assertRaises(ValueError):_verify_runtime_files(config,files,root)

    def test_authentic_bootstrap_refuses_synthetic_manifest(self):
        from backend.review.registration import load_approved_registry
        with TemporaryDirectory() as directory:
            path=Path(directory)/'bootstrap.json'
            path.write_text(json.dumps({'schema':'approved-operating-point-v1','origin':'SYNTHETIC'}),encoding='utf-8')
            with self.assertRaises(ValueError):load_approved_registry(path,'0'*64)

    def test_review_store_ingestion_correction_and_revalidation_invoke_registry(self):
        import test_m5_review as review_tests
        helper=review_tests.ReviewTests('test_queue_creation_and_summary');helper.setUp()
        try:
            calls=[]
            class Observer:
                def route(self,run,event,findings,base,**kwargs):
                    calls.append((event.revision.version if event else None,base.reason_codes,kwargs));return base
            helper.store.registry=Observer()
            view=helper.begin()
            result=helper.store.submit(helper.id,helper.proposal(view),helper.reviewer)
            self.assertTrue(result['applied'])
            task=helper.store.revalidate(helper.id,result['task'].etag,helper.reviewer)
            self.assertEqual(task.current_event.revision.version,2)
            self.assertTrue(any('REVALIDATION_REQUIRED' in reasons for _,reasons,_ in calls))
            self.assertEqual(calls[-1][0],2)
            self.assertTrue(calls[-1][2]['source_evidence'])
            self.assertFalse(task.decision.registered_routing_eligible)
        finally:helper.doCleanups()
