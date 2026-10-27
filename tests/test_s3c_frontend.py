"""Synthetic DOM replay plus markup; separate from actual live browser QA."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,shutil,subprocess,unittest
from bs4 import BeautifulSoup
from s3c_fixtures import workflow_records

ROOT=Path(__file__).resolve().parents[1]
NODE=shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')


class S3cFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='m6-test-s3c-ui-',dir=ROOT/'data/temp');cls.records=Path(cls.temp.name)/'records.json'
        cls.records.write_text(json.dumps(workflow_records(Path(cls.temp.name)/'calendar')),encoding='utf-8')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_read_only_markup_and_accessible_labels(self):
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        panel=page.find(id='sandboxWorkflow');self.assertIsNotNone(page.find(id=panel['aria-labelledby']))
        for name in ('sandboxFixture','sandboxOwnerConfirmation','approveSandbox','executeSandbox','reconcileSandbox','replaySandbox'):
            self.assertTrue(page.find(id=name).has_attr('disabled'),name)
        self.assertEqual(page.find(id='sandboxWorkflowStatus')['aria-live'],'polite')
        self.assertTrue(page.find(id='syncButton').has_attr('disabled'))
        self.assertIsNotNone(page.find(id='sandboxFixture').find_parent('label'))


def attach(case):
    def test(self):
        result=subprocess.run([NODE,str(ROOT/'tests/s3c_frontend_checks.js'),str(self.records),case],cwd=ROOT,capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr[-2500:])
    setattr(S3cFrontendTests,'test_'+case,test)


for case in ['public_default_deny','authorized_catalogue_only','exact_diff_and_explicit_approval','execute_and_idempotent_replay',
    'update_has_meaningful_exact_difference','source_selection_invalidates_approval','history_real_journal_and_recovery_selection',
    'stale_preview_not_reported_as_approval','revoked_approval_preserves_host_session','interrupted_execution_requires_reconciliation',
    'duplicate_click_submits_once','expiry_clears_private_views','late_reply_cannot_restore_session','safe_text_and_no_storage_authority','session_expiry_timer_clears_authority','approval_expiry_requires_new_preview']:
    attach(case)
