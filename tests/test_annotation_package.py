"""Synthetic annotation-helper regressions; no authentic labels or identities."""
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from backend.research.annotation_package import (new_task, add_region, add_event,
    submission_from_task, submission_findings, apply_action, write_new, main)
from backend.research.models import ResearchBundle, SourceTimetable, SourcePage
from backend.research.workflow import validate_history
from tests.research_fixtures import HASH, stamp, assigned, submission, verified


def bundle(partition='development'):
    return ResearchBundle(origin='SYNTHETIC', sources=(SourceTimetable(
        source_id='synthetic-source', source_document_id='synthetic-document',
        original_sha256=HASH, page_count=1),), pages=(SourcePage(
        page_id='synthetic-page', source_id='synthetic-source', page_number=1,
        partition=partition),))


def draft(empty=False):
    task = new_task(bundle(), 'synthetic-page', 'PRIMARY')
    add_region(task, 'r1', 'NON_EVENT' if empty else 'UNKNOWN',
               'Synthetic page upper-left cell', 'Synthetic test region only')
    if not empty:
        add_event(task, 'e1', 'r1')
    task['submission']['submission_id'] = 'synthetic-submission-v1'
    task['submission']['inventory']['recorded_by'] = stamp('synthetic-primary', 1).model_dump(mode='json')
    task['submission']['inventory']['coverage_attestation'] = stamp('synthetic-primary', 2).model_dump(mode='json')
    task['submission']['submitted_by'] = stamp('synthetic-primary', 3).model_dump(mode='json')
    return task


class AnnotationPackageTests(unittest.TestCase):
    def test_draft_has_no_labels_actors_or_timestamps(self):
        task = new_task(bundle(), 'synthetic-page', 'INDEPENDENT')
        self.assertEqual(task['submission']['labels'], [])
        self.assertIsNone(task['submission']['submitted_by'])
        self.assertEqual(task['submission']['inventory']['sessions'], [])

    def test_non_development_pages_rejected(self):
        for part in ('test', 'validation', 'excluded'):
            with self.subTest(part=part), self.assertRaisesRegex(ValueError, 'DEVELOPMENT_ONLY'):
                new_task(bundle(part), 'synthetic-page', 'PRIMARY')

    def test_missing_attestation_stays_pending(self):
        with self.assertRaises(ValidationError):
            submission_from_task(new_task(bundle(), 'synthetic-page', 'PRIMARY'), bundle())

    def test_all_registered_fields_are_blank(self):
        task = draft()
        label = task['submission']['labels'][0]
        self.assertTrue(all(v is None for v in label['revision']['content'].values()))
        self.assertTrue(all(o['state'] == 'MISSING' for o in label['revision']['observations']))
        sub = submission_from_task(task, bundle())
        self.assertIn('CRITICAL_FIELDS_UNRESOLVED', submission_findings(sub))

    def test_multiple_sessions_are_distinct_with_shared_region(self):
        task = draft(); add_event(task, 'e2', 'r1')
        sub = submission_from_task(task, bundle())
        self.assertEqual(sub.inventory.regions[0].kind, 'MULTI_SESSION')
        self.assertEqual(len(sub.labels), 2)
        self.assertNotEqual(sub.labels[0].revision.revision_id, sub.labels[1].revision.revision_id)

    def test_duplicate_event_and_region_rejected(self):
        task = draft()
        with self.assertRaises(ValueError): add_event(task, 'e1', 'r1')
        with self.assertRaises(ValueError): add_region(task, 'r1', 'UNKNOWN', 'here', 'note')

    def test_empty_page_needs_explicit_non_event_accounting(self):
        task = draft(True)
        self.assertEqual(len(submission_from_task(task, bundle()).labels), 0)
        with self.assertRaises(ValueError): add_event(task, 'e1', 'r1')
        task['submission']['inventory']['regions'] = []
        with self.assertRaisesRegex(ValueError, 'PAGE_ACCOUNTING'):
            submission_from_task(task, bundle())

    def test_source_hash_and_evidence_page_mismatch(self):
        task = draft(); task['source_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'BINDING'): submission_from_task(task, bundle())
        task = draft(); task['submission']['inventory']['evidence'][0]['page'] = 2
        with self.assertRaisesRegex(ValueError, 'PAGE_MISMATCH'): submission_from_task(task, bundle())

    def test_time_format_and_unknown_field_rejected(self):
        for value in ('9 AM', '25:10'):
            task = draft(); rev = task['submission']['labels'][0]['revision']
            rev['content']['start_time'] = value
            obs = next(o for o in rev['observations'] if o['field_name'] == 'start_time')
            obs.update(state='PRESENT', raw_value=value, evidence_ids=[task['submission']['inventory']['evidence'][0]['evidence_id']])
            with self.assertRaises(ValidationError): submission_from_task(task, bundle())
        task = draft(); task['submission']['unexpected_column'] = 'not allowed'
        with self.assertRaises(ValidationError): submission_from_task(task, bundle())

    def test_unknown_is_not_not_applicable(self):
        task = draft(); rev = task['submission']['labels'][0]['revision']
        obs = next(o for o in rev['observations'] if o['field_name'] == 'location')
        obs.update(state='UNRESOLVED', raw_value='TBA', justification='Source explicitly says TBA',
                   evidence_ids=[task['submission']['inventory']['evidence'][0]['evidence_id']])
        sub = submission_from_task(task, bundle())
        self.assertIsNone(sub.labels[0].revision.content.location)
        self.assertIn('UNRESOLVED_FIELDS_RETAINED', submission_findings(sub))
        obs.update(state='NOT_APPLICABLE', justification=None)
        with self.assertRaises(ValidationError): submission_from_task(task, bundle())

    def test_difficult_session_cannot_be_dropped(self):
        task = draft(); task['submission']['labels'] = []
        with self.assertRaises(ValidationError): submission_from_task(task, bundle())

    def test_changed_revision_requires_predecessor(self):
        task = draft(); task['submission']['labels'][0]['revision']['version'] = 2
        with self.assertRaises(ValidationError): submission_from_task(task, bundle())

    def test_origin_and_coverage_author_must_match(self):
        task = draft(); task['origin'] = 'AUTHENTIC'
        with self.assertRaises(ValueError): submission_from_task(task, bundle())
        task = draft(); task['submission']['inventory']['coverage_attestation']['actor_id'] = 'other'
        with self.assertRaises(ValueError): submission_from_task(task, bundle())

    def test_native_submit_transition_preserved(self):
        state = apply_action(assigned(), {'action':'submit','payload':submission().model_dump(mode='json')})
        self.assertTrue(validate_history(state))
        self.assertEqual(state.state.value, 'PRIMARY_SUBMITTED')
        self.assertIsNone(state.verification)

    def test_independent_cannot_be_primary(self):
        w = assigned(); payload = submission('INDEPENDENT', 3).model_dump(mode='json')
        for field in ('recorded_by','coverage_attestation'):
            if payload['inventory'].get(field): payload['inventory'][field]['actor_id'] = 'synthetic-primary'
        payload['submitted_by']['actor_id'] = 'synthetic-primary'
        with self.assertRaises(ValueError): apply_action(w, {'action':'submit','payload':payload})

    def test_forged_state_and_early_export_blocked(self):
        w = assigned().model_copy(update={'state':'INDEPENDENTLY_VERIFIED'})
        with self.assertRaises(ValueError): apply_action(w, {'action':'submit','payload':{}})
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            p = Path(d); write_new(p/'w.json', assigned().model_dump(mode='json'))
            self.assertEqual(main(['export','--workflow',str(p/'w.json'),'--out',str(p/'gold.json')]), 2)
            self.assertFalse((p/'gold.json').exists())

    def test_export_reuses_verified_workflow_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            p = Path(d); write_new(p/'w.json', verified().model_dump(mode='json'))
            args = ['export','--workflow',str(p/'w.json'),'--out',str(p/'gold.json')]
            self.assertEqual(main(args), 0)
            self.assertEqual(json.loads((p/'gold.json').read_text())['verified_event_count'], 1)
            before = (p/'gold.json').read_bytes()
            self.assertEqual(main(args), 2)
            self.assertEqual((p/'gold.json').read_bytes(), before)

    def test_validation_errors_do_not_echo_private_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); task = draft(); task['submission']['submitted_by']['recorded_at'] = 'PRIVATE_SECRET_VALUE'
            write_new(p/'t.json', task); write_new(p/'b.json', bundle().model_dump(mode='json'))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(['check-task','--bundle',str(p/'b.json'),'--task',str(p/'t.json')])
            self.assertEqual(rc, 2); self.assertNotIn('PRIVATE_SECRET_VALUE', output.getvalue())


if __name__ == '__main__': unittest.main()
