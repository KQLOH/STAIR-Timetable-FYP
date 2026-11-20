"""PRE-03 SOFTWARE cases, never independent validation results."""
import unittest
from pydantic import ValidationError
from backend.review.operating_point import ValidationInput,select_operating_point,freeze_selection,load_selection
from backend.reconstruction.models import digest

def data(scores=(.9,.8,.8,.7,.1),wrong=(),hard=()):
    return ValidationInput(origin='SYNTHETIC',partition='validation',pipeline_digest='1'*64,scoring_configuration_digest='2'*64,pre01_artifact_digest='3'*64,pre02_artifact_digest='4'*64,tau_grid=(0,.1,.5,.7,.8,.9,1),rows=tuple({'ledger_id':str(i),'source_sha256':'5'*64,'revision_digest':'6'*64,'reference_digest':'7'*64,'score':v,'gate_passed':True,'exact_event_correct':i not in wrong,'hard_violation':i in hard} for i,v in enumerate(scores)))
class OperatingPointSoftwareTests(unittest.TestCase):
    def test_exact_registered_tie_order_and_full_vector(self):
        result=select_operating_point(data(wrong=(4,)));self.assertEqual(result['selected_tau'],.5);self.assertEqual(len(result['candidate_vector']),7);self.assertFalse(result['registered_accept_eligible'])
    def test_hard_violations_cannot_be_hidden_by_high_scores(self):
        result=select_operating_point(data(hard=(0,)));self.assertEqual(result['selection_status'],'VALIDATION_INFEASIBLE');self.assertIsNone(result['selected_tau'])
    def test_missing_scores_remain_in_common_denominator(self):
        result=select_operating_point(data(scores=(.9,None,None,None,None)));self.assertEqual(result['selection_status'],'VALIDATION_INFEASIBLE');self.assertTrue(all(p['population']==5 for p in result['candidate_vector']))
    def test_empty_population_does_not_fabricate_risk(self):
        result=select_operating_point(data(scores=()));self.assertIsNone(result['candidate_vector'][0]['selective_risk']);self.assertIsNone(result['selected_tau'])
    def test_frozen_manifest_loader_preserves_no_authority(self):
        d=data();m=freeze_selection(d);self.assertEqual(load_selection(m,d,pipeline_digest=d.pipeline_digest,scoring_configuration_digest=d.scoring_configuration_digest),m);self.assertFalse(m['registered_accept_eligible'])
    def test_tampering_or_changed_configuration_rejected(self):
        d=data();m=freeze_selection(d)
        for changed in ({**m,'selected_tau':.999},{**m,'registered_accept_eligible':True}):
            with self.assertRaises(ValueError):load_selection(changed,d,pipeline_digest=d.pipeline_digest,scoring_configuration_digest=d.scoring_configuration_digest)
        with self.assertRaises(ValueError):load_selection(m,d,pipeline_digest='0'*64,scoring_configuration_digest=d.scoring_configuration_digest)
    def test_recomputed_checksum_cannot_make_fake_selection_authoritative(self):
        d=data();m=freeze_selection(d);m['registered_accept_eligible']=True;m['integrity_digest']=digest({k:v for k,v in m.items() if k!='integrity_digest'})
        with self.assertRaises(ValueError):load_selection(m,d,pipeline_digest=d.pipeline_digest,scoring_configuration_digest=d.scoring_configuration_digest)
    def test_development_test_or_duplicate_grid_not_validation(self):
        d=data().model_dump(mode='json')
        for partition in ('development','test'):
            with self.assertRaises(ValidationError):ValidationInput.model_validate({**d,'partition':partition})
        with self.assertRaises(ValidationError):ValidationInput.model_validate({**d,'tau_grid':[.9,.1,.1]})
