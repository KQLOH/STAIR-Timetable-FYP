"""Replay isolated real backend responses in synthetic DOM tests; no live enrollment."""
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from bs4 import BeautifulSoup
from s3b_workflow_fixtures import workflow_fixture

ROOT=Path(__file__).resolve().parents[1]
NODE=shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')


class S3bFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='s3b-frontend-',dir=ROOT/'data/temp');cls.fixture=Path(cls.temp.name)/'synthetic.json'
        cls.fixture.write_text(json.dumps(workflow_fixture()),encoding='utf-8')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_accessible_read_only_default_markup(self):
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        panel=page.find(id='reviewDrawer');self.assertEqual(panel['role'],'dialog');self.assertEqual(panel['aria-modal'],'true')
        self.assertIsNotNone(page.find(id=panel['aria-labelledby']))
        self.assertTrue(page.find(id='reviewCorrectionControls').has_attr('disabled'))
        self.assertTrue(page.find(id='ingestReviewSource').has_attr('disabled'))
        self.assertTrue(page.find(id='endReviewSession').has_attr('disabled'))
        self.assertEqual(page.find(id='reviewValidationFeedback')['aria-live'],'polite')


def attach(case):
    def test(self):
        result=subprocess.run([NODE,str(ROOT/'tests/s3b_frontend_checks.js'),str(self.fixture),case],cwd=ROOT,capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr[-3500:])
        if case=='raw_ingestion_body_preserves_original_envelope':
            record=next(json.loads(line) for line in result.stdout.splitlines() if 'ingestion_body' in line)
            from backend.review.binding import AssessmentInput,bind_input
            payload=AssessmentInput.model_validate_json(record['ingestion_body']);bound=bind_input(payload)
            self.assertEqual(bound.profile,'m4-development-v1')
    setattr(S3bFrontendTests,'test_'+case,test)


for case in ['public_default_deny','public_canonical_details','scoped_queue_and_task','supported_correction_revision_and_history',
    'revalidation_and_new_preview','wrong_operator_controls_disabled','missing_evidence_and_field_states',
    'disposition_reason_remains_usable_after_revalidation',
    'conflicting_and_unresolved_states','human_assertion_requires_explicit_original_evidence','expiry_clears_private_form_and_history',
    'stale_etag_does_not_claim_success','duplicate_click_does_not_duplicate_submission','revocation_and_no_storage_authority',
    'expiry_timer_clears_private_projection',
    'late_reply_after_forget_cannot_restore_private_state','safe_text_rendering_and_source_links','cross_page_selection_rejected',
    'raw_ingestion_body_preserves_original_envelope','application_invalidation_and_source_isolation','different_capture_and_image_page_not_highlighted']:
    attach(case)
