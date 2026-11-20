"""Static/syntax checks complement the separately executed real Chrome workflow."""
from pathlib import Path
import subprocess
import unittest
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
class LocalCalendarFrontendTests(unittest.TestCase):
    def test_sandbox_has_friendly_controls_and_clear_test_scope(self):
        page=BeautifulSoup((ROOT/'frontend/calendar-sandbox.html').read_text(encoding='utf-8'),'html.parser')
        self.assertIn('TEST DATA ONLY',page.get_text())
        self.assertIn('invented',page.get_text().lower())
        self.assertFalse(page.find_all('input',{'type':'file'}))
        for name in ('sandboxFixture','sandboxOwnerConfirmation','approveSandbox','executeSandbox','replaySandbox'):
            self.assertTrue(page.find(id=name).has_attr('disabled'))
        for name in ('sandboxFailure','sandboxRecoveryApproval','reconcileSandbox'):
            self.assertIsNotNone(page.find(id=name).find_parent('details'))
    def test_bootstrap_has_no_web_storage_or_bearer_delivery(self):
        source=(ROOT/'frontend/js/local-calendar-host.js').read_text(encoding='utf-8')
        for value in ('localStorage','sessionStorage','Bearer '):self.assertNotIn(value,source)
        self.assertIn("credentials:'same-origin'",source)
        self.assertIn("redirect:'error'",source)
        self.assertIn('X-STAIR-Calendar-Binding',source)
        self.assertIn('stopImmediatePropagation',source)
    def test_javascript_syntax(self):
        for name in ('frontend/js/sandbox.js','frontend/js/local-calendar-host.js'):
            result=subprocess.run(['node','--check',str(ROOT/name)],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
    def test_student_entry_opens_separate_sandbox_without_replacing_review(self):
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        link=page.find('a',href='http://127.0.0.1:8022')
        self.assertEqual(link['target'],'_blank');self.assertIn('noopener',link['rel'])
        self.assertTrue(page.find(id='syncButton').has_attr('disabled'))
