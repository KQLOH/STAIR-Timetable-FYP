"""Synthetic frozen scoring validates source, checking and common metric scope."""
import copy
import unittest
from backend.research.score_captured import evaluate
from backend.research.development_compare import FIELDS
from tests.test_strong_b2 import fixture,SOURCE
from backend.baselines.strong_b2 import predict
class CapturedScoreTests(unittest.TestCase):
    def records(self):
        packet=predict(SOURCE,[fixture()]).model_dump(mode='json')
        run={'scope':'DEVELOPMENT_ONLY','source_sha256':'a'*64,'raw_evidence':{'pages':[{'page':1}]},'conditions':{'synthetic':{'packet':packet}}}
        ref={'scope':'DEVELOPMENT_ONLY','source_sha256':'a'*64,'checked_by':'synthetic-reviewer','checked_at':'2035-01-01T12:00:00+08:00','all_visible_sessions_checked':True,'fields':list(FIELDS),'pages':[{'page':1,'events':[packet['events'][0]['revision']['content']]}]}
        return run,ref
    def test_same_capture_exact_metrics(self):
        run,ref=self.records();before=copy.deepcopy(ref)
        result=evaluate(run,ref)
        self.assertEqual(result['results']['synthetic']['strict_declared']['f1'],1)
        self.assertEqual(ref,before)
    def test_blank_unchecked_source_and_population_refused(self):
        for key,value in [('checked_by',None),('source_sha256','b'*64),('all_visible_sessions_checked',False),('checked_at','2035-01-01T12:00:00')]:
            run,ref=self.records();ref[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):evaluate(run,ref)
    def test_missing_field_or_duplicate_page_refused(self):
        run,ref=self.records();ref['pages'].append(copy.deepcopy(ref['pages'][0]))
        with self.assertRaises(ValueError):evaluate(run,ref)
        run,ref=self.records();ref['pages'][0]['events'][0].pop('location')
        with self.assertRaises(ValueError):evaluate(run,ref)
